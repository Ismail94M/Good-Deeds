"""Profile pictures (M6A, docs/M6-DESIGN.md §3.3 P2 to P6, §4.3, D116 to D123, Q95).

A profile picture is a photo with ``purpose = 'avatar'``: the same upload, complete, worker,
signed URLs, ``held/`` moves and purge jobs as deed photos. What is new lives here:

- ``set_avatar`` (P2): checks the square crop against the upright image's header and queues
  the upload; the worker cuts the square on the server (``_pipeline.crop_square``).
- ``go_live`` (the worker, once variants are stored): the new picture replaces the live one in
  one transaction, and the old one's objects are deleted right after commit.
- ``remove_avatar`` (P4): owner removal, deleted at once.
- ``my_avatar`` (P3) and ``report_target`` (P6).

Every change of the live picture emits ``AvatarChanged`` (identity keeps the pointer).

Lock order: the ``media_avatar`` advisory lock (per owner), then photo rows. Admin decisions
and restores of an avatar take ``photo_case`` first, then ``media_avatar`` (``_admin``); nothing
takes ``photo_case`` after ``media_avatar``, so the orders never cross.
"""

import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import advisory_xact_lock
from app.core.errors import AppError, Conflict, NotFound
from app.core.events import emit
from app.core.ratelimit import RateLimiter
from app.core.storage import ObjectStorage
from app.modules.identity import service as identity
from app.modules.media import _keys, _pipeline
from app.modules.media._common import photo_not_found, utcnow
from app.modules.media._limits import MediaLimits
from app.modules.media.events import AvatarChanged, AvatarChangeReason
from app.modules.media.models import (
    PHOTO_REPORT_PRIORITY,
    CaseDecision,
    Photo,
    PhotoPurpose,
    PhotoReportCase,
    PhotoReportReason,
    PhotoStatus,
    PhotoTakedown,
    ReportCaseStatus,
)
from app.modules.media.schemas import (
    AvatarPending,
    AvatarRemovedNotice,
    AvatarReportTarget,
    AvatarSet,
    MyAvatarOut,
    ReportReasonOut,
)
from app.modules.media.service import live_avatar_version, sign_avatar

# Report reasons for a profile picture (D121), in the order shown, with their labels.
AVATAR_REPORT_REASON_LABELS: dict[PhotoReportReason, str] = {
    PhotoReportReason.CHILD_NO_GUARDIAN_CONSENT: "Shows a child",
    PhotoReportReason.IMPERSONATION: "Not this person / impersonation",
    PhotoReportReason.PRIVATE_DETAILS: "Private details",
    PhotoReportReason.INAPPROPRIATE: "Inappropriate or harmful",
    PhotoReportReason.OTHER: "Other",
}
# The owner's removal notice (Q95). ``not_consented`` can only come from an admin's choice.
_REMOVAL_LABELS: dict[PhotoReportReason, str] = {
    **AVATAR_REPORT_REASON_LABELS,
    PhotoReportReason.NOT_CONSENTED: "Shows someone who didn't agree",
}
# Upload rows that hold a place in processing.
_IN_WORK = (PhotoStatus.QUEUED, PhotoStatus.PROCESSING)


class Unprocessable(AppError):
    status_code = 422


def avatar_report_reasons() -> list[ReportReasonOut]:
    return [
        ReportReasonOut(id=reason.value, label=label, priority=PHOTO_REPORT_PRIORITY[reason])
        for reason, label in AVATAR_REPORT_REASON_LABELS.items()
    ]


def ensure_avatars_enabled(limits: MediaLimits) -> None:
    if not limits.avatars_enabled:
        raise Conflict("Profile pictures are switched off for now.", code="avatars_disabled")


async def enforce_change_limit(
    limiter: RateLimiter, limits: MediaLimits, user_id: uuid.UUID
) -> None:
    """At most ``rate_avatar_change_user`` picture changes per account (D123). Runs before any
    lookup."""
    await limiter.hit(f"avatarchange:u:{user_id}", limits.rate_avatar_change_user)


async def enforce_sign_limit(limiter: RateLimiter, limits: MediaLimits, user_id: uuid.UUID) -> None:
    await limiter.hit(f"avatarsign:u:{user_id}", limits.rate_avatar_sign_user)


async def lock_owner(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Serialise every change of one person's live picture."""
    await advisory_xact_lock(session, "media_avatar", str(user_id))


async def _live(session: AsyncSession, user_id: uuid.UUID, *, lock: bool = False) -> Photo | None:
    """The owner's live picture (``uq_photos_live_avatar``), if any."""
    stmt = select(Photo).where(
        Photo.uploader_user_id == user_id,
        Photo.purpose == PhotoPurpose.AVATAR,
        Photo.status == PhotoStatus.READY,
        Photo.takedown == PhotoTakedown.NONE,
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(stmt)


async def has_live(session: AsyncSession, user_id: uuid.UUID) -> bool:
    return await _live(session, user_id) is not None


def ref_of(photo: Photo, version: int | None = None) -> str:
    """The reference of a picture's current (or given) variant version."""
    if version is None:
        version = _keys.key_version(_keys.variant_keys_of(photo.variant_keys)["avatar_md"])
    return _keys.avatar_ref(photo.id, version)


def changed(
    session: AsyncSession,
    user_id: uuid.UUID,
    ref: str | None,
    reason: AvatarChangeReason,
    now: datetime,
) -> None:
    emit(session, AvatarChanged(user_id=user_id, avatar_ref=ref, changed_at=now, reason=reason))


async def close_open_cases(session: AsyncSession, photo_id: uuid.UUID, now: datetime) -> None:
    """An open report case on a picture that is gone ends without a decision."""
    await session.execute(
        update(PhotoReportCase)
        .where(
            PhotoReportCase.photo_id == photo_id, PhotoReportCase.status == ReportCaseStatus.OPEN
        )
        .values(status=ReportCaseStatus.CLOSED, resolved_at=now)
        .execution_options(synchronize_session=False)
    )


def delete_row(photo: Photo, by: uuid.UUID | None, now: datetime) -> list[str]:
    """Mark a picture deleted; returns every object key to delete after commit."""
    photo.status = PhotoStatus.DELETED
    photo.deleted_at = now
    photo.deleted_by = by
    photo.object_move = None
    return _keys.all_object_keys(photo.staging_key, photo.variant_keys)


# --------------------------------------------------- cooldown after an admin removal (Q95)


async def cooldown_until(
    session: AsyncSession, user_id: uuid.UUID, limits: MediaLimits
) -> datetime | None:
    """When the owner may set a new picture after their latest admin removal (still removed,
    i.e. not restored). None when there is no such removal."""
    removed_at = await session.scalar(
        select(func.max(Photo.removed_at)).where(
            Photo.uploader_user_id == user_id,
            Photo.purpose == PhotoPurpose.AVATAR,
            Photo.takedown == PhotoTakedown.ADMIN_REMOVED,
        )
    )
    if removed_at is None:
        return None
    return removed_at + timedelta(hours=limits.avatar_reupload_cooldown_hours)


async def in_cooldown(
    session: AsyncSession, user_id: uuid.UUID, limits: MediaLimits, now: datetime
) -> bool:
    """Whether ``now`` falls in the wait after the owner's latest admin removal. Checked when a
    picture is queued (``set_avatar``) and again when it would go live (the worker), so an
    upload queued before the removal can't skip the wait (SEC6-L3)."""
    until = await cooldown_until(session, user_id, limits)
    return until is not None and now < until


# --------------------------------------------------- P2: set a new picture


async def _read_upright_size(
    storage: ObjectStorage, photo: Photo, limits: MediaLimits
) -> tuple[int, int] | None:
    try:
        data = await storage.get_bytes(photo.staging_key, max_bytes=limits.upload_max_bytes)
    except Exception:
        return None  # the worker decides (too large, missing)
    return await asyncio.to_thread(_pipeline.upright_size, data, photo.declared_type.value)


async def set_avatar(
    session: AsyncSession,
    storage: ObjectStorage,
    user_id: uuid.UUID,
    data: AvatarSet,
    *,
    limits: MediaLimits,
    now: datetime | None = None,
) -> None:
    """Queue a completed avatar upload with its crop (202). The live picture stays until the
    new one is ready (D116)."""
    now = now or utcnow()
    ensure_avatars_enabled(limits)
    await lock_owner(session, user_id)
    photo = await session.scalar(
        select(Photo)
        .where(Photo.id == data.photo_id, Photo.uploader_user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if photo is None or photo.purpose != PhotoPurpose.AVATAR:
        raise photo_not_found()
    if photo.status in _IN_WORK:
        return  # already queued: a retry of the same request
    if photo.status == PhotoStatus.PENDING_UPLOAD:
        raise Conflict("The photo hasn't finished uploading.", code="upload_not_complete")
    if photo.status != PhotoStatus.UPLOADED:
        raise photo_not_found()

    until = await cooldown_until(session, user_id, limits)
    if until is not None and now < until:
        raise Conflict(
            "You can add a new photo a little later.",
            code="cooldown",
            extra={"available_at": until.isoformat()},
        )

    crop = _pipeline.AvatarCrop(x=data.crop.x, y=data.crop.y, size=data.crop.size)
    size = await _read_upright_size(storage, photo, limits)
    if size is not None:
        try:
            _pipeline.crop_box(crop, size[0], size[1], limits.avatar_min_side_px)
        except _pipeline.CropTooSmall:
            raise Unprocessable(
                f"This photo is too small: pick one at least {limits.avatar_min_side_px} × "  # noqa: RUF001
                f"{limits.avatar_min_side_px}.",
                code="image_too_small",
            ) from None
        except _pipeline.CropInvalid:
            raise Unprocessable(
                "The crop doesn't fit the photo. Try again.", code="crop_invalid"
            ) from None
    # An unreadable header is left to the worker, which rejects the file with its reason (P3).

    photo.avatar_crop = {"x": crop.x, "y": crop.y, "size": crop.size}
    photo.attached_at = now
    photo.status = PhotoStatus.QUEUED
    await session.flush()


# --------------------------------------------------- the worker: going live


async def make_room(
    session: AsyncSession, photo: Photo, now: datetime
) -> tuple[bool, list[tuple[uuid.UUID, list[str]]]]:
    """Called by the worker with the owner locked (``lock_owner``) and the new row locked, before
    the row is marked ``ready``. Deletes the live picture (its open report case closes) and
    frees the live slot, so the new one can take it. A picture set before the live one (two
    uploads racing) never replaces it: ``(True, [])`` says the new one is superseded. Returns
    ``(superseded, [(photo_id, keys to delete after commit)])``."""
    owner = photo.uploader_user_id
    live = await session.scalar(
        select(Photo)
        .where(
            Photo.uploader_user_id == owner,
            Photo.purpose == PhotoPurpose.AVATAR,
            Photo.status == PhotoStatus.READY,
            Photo.takedown == PhotoTakedown.NONE,
            Photo.id != photo.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if live is None:
        return False, []
    if (
        live.attached_at is not None
        and photo.attached_at is not None
        and live.attached_at > photo.attached_at
    ):
        return True, []
    await close_open_cases(session, live.id, now)
    keys = delete_row(live, owner, now)
    await session.flush()  # free the live slot before the new row takes it
    return False, [(live.id, keys)]


def went_live(session: AsyncSession, photo: Photo, now: datetime) -> None:
    """The new picture is ``ready`` and live: tell identity (D117)."""
    changed(session, photo.uploader_user_id, ref_of(photo), "ready", now)


# --------------------------------------------------- P4: owner removal


async def remove_avatar(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> list[tuple[uuid.UUID, list[str]]]:
    """Delete the live picture at once, and drop any upload still processing (so it can't
    become live afterwards). Idempotent. Returns ``(photo_id, keys)`` to delete after commit.
    A picture an admin removed stays held (Q95: restorable for 90 days)."""
    now = now or utcnow()
    await lock_owner(session, user_id)
    rows = list(
        await session.scalars(
            select(Photo)
            .where(
                Photo.uploader_user_id == user_id,
                Photo.purpose == PhotoPurpose.AVATAR,
                Photo.takedown == PhotoTakedown.NONE,
                Photo.status.in_((PhotoStatus.READY, *_IN_WORK)),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    purge: list[tuple[uuid.UUID, list[str]]] = []
    had_live = False
    for photo in rows:
        if photo.status == PhotoStatus.READY:
            had_live = True
            await close_open_cases(session, photo.id, now)
        purge.append((photo.id, delete_row(photo, user_id, now)))
    if had_live:
        changed(session, user_id, None, "removed_by_owner", now)
    await session.flush()
    return purge


async def purge_now(
    session: AsyncSession,
    storage: ObjectStorage,
    items: list[tuple[uuid.UUID, list[str]]],
    now: datetime,
) -> None:
    """After commit: delete the objects so every URL already issued stops working at once
    (D118). A failure is left for ``media.purge_deleted``."""
    for photo_id, keys in items:
        try:
            for key in dict.fromkeys(keys):
                await storage.delete(key)
        except Exception:
            continue
        await session.execute(
            update(Photo)
            .where(Photo.id == photo_id, Photo.objects_purged_at.is_(None))
            .values(
                objects_purged_at=now,
                staging_deleted_at=func.coalesce(Photo.staging_deleted_at, now),
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()


# --------------------------------------------------- P3: my picture


async def my_avatar(
    session: AsyncSession,
    storage: ObjectStorage,
    user_id: uuid.UUID,
    *,
    limits: MediaLimits,
    now: datetime | None = None,
) -> MyAvatarOut:
    now = now or utcnow()
    live = await _live(session, user_id)
    current = None
    if live is not None and live_avatar_version(live) is not None:
        current = sign_avatar(storage, limits, _keys.variant_keys_of(live.variant_keys), now=now)

    latest = await session.scalar(
        select(Photo)
        .where(
            Photo.uploader_user_id == user_id,
            Photo.purpose == PhotoPurpose.AVATAR,
            Photo.attached_at.is_not(None),
        )
        .order_by(Photo.attached_at.desc(), Photo.id.desc())
        .limit(1)
    )
    pending = None
    if latest is not None and (live is None or latest.id != live.id):
        if latest.status in _IN_WORK:
            pending = AvatarPending(status="processing")
        elif latest.status == PhotoStatus.REJECTED:
            reason = latest.reject_reason.value if latest.reject_reason else None
            pending = AvatarPending(status="rejected", reject_reason=reason)
        elif latest.status == PhotoStatus.FAILED:
            pending = AvatarPending(status="failed")

    removed = await session.scalar(
        select(Photo)
        .where(
            Photo.uploader_user_id == user_id,
            Photo.purpose == PhotoPurpose.AVATAR,
            Photo.takedown == PhotoTakedown.ADMIN_REMOVED,
            Photo.removed_at.is_not(None),
        )
        .order_by(Photo.removed_at.desc())
        .limit(1)
    )
    notice = None
    if removed is not None and removed.removed_at is not None:
        newer = latest is not None and (
            latest.attached_at is not None and latest.attached_at > removed.removed_at
        )
        if not newer:
            notice = AvatarRemovedNotice(
                removed_at=removed.removed_at,
                reason_label=await _removal_label(session, removed.id),
                upload_after=removed.removed_at
                + timedelta(hours=limits.avatar_reupload_cooldown_hours),
            )
    return MyAvatarOut(current=current, pending=pending, removed_notice=notice)


async def _removal_label(session: AsyncSession, photo_id: uuid.UUID) -> str:
    reason = await session.scalar(
        select(PhotoReportCase.removal_reason)
        .where(
            PhotoReportCase.photo_id == photo_id,
            PhotoReportCase.decision == CaseDecision.REMOVED,
        )
        .order_by(PhotoReportCase.resolved_at.desc())
        .limit(1)
    )
    return _REMOVAL_LABELS.get(reason or PhotoReportReason.OTHER, "Other")


# --------------------------------------------------- P6: the picture to report


async def report_target(
    session: AsyncSession, handle: str, viewer_id: uuid.UUID
) -> AvatarReportTarget:
    """The live picture of an active person other than the viewer; 404 otherwise."""
    not_found = NotFound("We couldn't find that profile picture.", code="avatar_not_found")
    user = await identity.get_user_by_handle(session, handle)
    if user is None or user.id == viewer_id or not await identity.is_active(session, user.id):
        raise not_found
    live = await _live(session, user.id)
    if live is None or live_avatar_version(live) is None:
        raise not_found
    return AvatarReportTarget(photo_id=live.id)
