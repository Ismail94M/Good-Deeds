"""Photos on their owners (§3.3 #5 to #9): attach to a deed or a self-log, owner reads with
outcomes, and removal by the uploader (any time, Q30) or the doer (final, Q29).

Lock order: the ``media_owner`` advisory lock (deed or self-log), then photo rows. Deed and
self-log details come from ``deeds.service`` only. Non-owners always get 404.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.db import advisory_xact_lock
from app.core.errors import Conflict
from app.core.events import emit
from app.core.storage import ObjectStorage
from app.modules.deeds import service as deeds
from app.modules.identity import service as identity
from app.modules.media import _keys
from app.modules.media._common import (
    Invalid,
    deed_not_found,
    photo_not_found,
    self_log_not_found,
    utcnow,
)
from app.modules.media._limits import MediaLimits
from app.modules.media._uploads import ensure_enabled
from app.modules.media.events import PhotoRemovedByDoer
from app.modules.media.models import (
    Photo,
    PhotoDecision,
    PhotoDecisionAction,
    PhotoPurpose,
    PhotoReportCase,
    PhotoStatus,
    PhotoTakedown,
    ReportCaseStatus,
)
from app.modules.media.schemas import (
    AttachItem,
    DeedPhotosOut,
    Outcome,
    OwnerPhoto,
    SelfLogPhotosOut,
    UserRef,
)
from app.modules.media.service import sign_variants

type OwnerStatus = Literal["processing", "ready", "failed"]
type Decision = Literal["removed", "kept", "removed_by_doer"]

# Rows that hold a carousel slot and count toward ``photos_per_owner``.
LIVE = (PhotoStatus.QUEUED, PhotoStatus.PROCESSING, PhotoStatus.READY)
# Rows the owner sees (rejected after attach shows as "failed": the file couldn't be used).
_OWNER_VISIBLE = (*LIVE, PhotoStatus.FAILED, PhotoStatus.REJECTED)
# Deeds that still take photos.
_EDITABLE = (deeds.DeedStatus.PENDING, deeds.DeedStatus.VERIFIED)


# --------------------------------------------------- attach (#5, #6)


def _check_regions(items: list[AttachItem], limits: MediaLimits, *, allowed: bool) -> None:
    for item in items:
        regions = item.blur_regions
        if regions and not allowed:
            raise Invalid("Private photos can't have blur regions.", code="blur_not_allowed")
        if len(regions) > limits.blur_max_regions:
            raise Invalid("Too many blur regions on one photo.", code="invalid_blur_regions")
        for r in regions:
            if (
                r.x + r.w > 1
                or r.y + r.h > 1
                or r.w < limits.blur_min_region
                or r.h < limits.blur_min_region
            ):
                raise Invalid(
                    "A blur region is outside the photo or too small.", code="invalid_blur_regions"
                )


def _check_count(items: list[AttachItem], limits: MediaLimits) -> None:
    ids = [i.photo_id for i in items]
    if len(set(ids)) != len(ids):
        raise Invalid("The same photo is listed twice.", code="duplicate_photo")
    if len(items) > limits.photos_per_owner:
        raise Conflict(
            f"You can add up to {limits.photos_per_owner} photos.", code="too_many_photos"
        )


async def _attach(
    session: AsyncSession,
    user_id: uuid.UUID,
    items: list[AttachItem],
    *,
    purpose: PhotoPurpose,
    owner_id: uuid.UUID,
    limits: MediaLimits,
    now: datetime,
) -> list[Photo]:
    owner_col = Photo.deed_id if purpose == PhotoPurpose.DEED else Photo.self_log_id
    await advisory_xact_lock(session, "media_owner", str(owner_id))
    live = await session.scalar(
        select(func.count())
        .select_from(Photo)
        .where(owner_col == owner_id, Photo.status.in_(LIVE), Photo.takedown == PhotoTakedown.NONE)
    )
    if (live or 0) + len(items) > limits.photos_per_owner:
        raise Conflict(
            f"You can add up to {limits.photos_per_owner} photos.", code="too_many_photos"
        )

    ids = [i.photo_id for i in items]
    rows = await session.scalars(
        select(Photo).where(Photo.id.in_(ids)).order_by(Photo.id).with_for_update()
    )
    found = {p.id: p for p in rows}
    for item in items:
        photo = found.get(item.photo_id)
        if (
            photo is None
            or photo.uploader_user_id != user_id
            or photo.purpose != purpose
            or photo.status != PhotoStatus.UPLOADED
        ):
            raise Conflict(
                "A photo isn't ready to add. Upload it again.", code="photo_not_uploaded"
            )

    # Positions only grow (never reused), so the carousel keeps upload order.
    top = await session.scalar(select(func.max(Photo.position)).where(owner_col == owner_id))
    position = top or 0
    attached = []
    for item in items:
        photo = found[item.photo_id]
        position += 1
        if purpose == PhotoPurpose.DEED:
            photo.deed_id = owner_id
        else:
            photo.self_log_id = owner_id
        photo.position = position
        photo.blur_regions = [r.model_dump() for r in item.blur_regions]
        photo.attached_at = now
        photo.status = PhotoStatus.QUEUED
        attached.append(photo)
    await session.flush()
    return attached


def add_until(deed: deeds.DeedView, limits: MediaLimits) -> datetime:
    return deed.reported_at + timedelta(hours=limits.photo_edit_window_hours)


async def attach_to_deed(
    session: AsyncSession,
    storage: ObjectStorage,
    user_id: uuid.UUID,
    deed_id: uuid.UUID,
    items: list[AttachItem],
    *,
    limits: MediaLimits,
    now: datetime | None = None,
) -> list[OwnerPhoto]:
    now = now or utcnow()
    deed = await deeds.get_deed(session, deed_id)
    if deed is None or deed.reporter_id != user_id:
        raise deed_not_found()
    ensure_enabled(limits)
    if deed.status not in _EDITABLE:
        raise Conflict("Photos can't be added to this deed.", code="deed_not_editable")
    if now > add_until(deed, limits):
        raise Conflict(
            "Photos can only be added for a while after sending.", code="photo_window_closed"
        )
    _check_count(items, limits)
    _check_regions(items, limits, allowed=True)
    photos = await _attach(
        session,
        user_id,
        items,
        purpose=PhotoPurpose.DEED,
        owner_id=deed_id,
        limits=limits,
        now=now,
    )
    return [_owner_photo(p, storage, limits, user_id, doer_id=None, now=now) for p in photos]


async def attach_to_self_log(
    session: AsyncSession,
    storage: ObjectStorage,
    user_id: uuid.UUID,
    self_log_id: uuid.UUID,
    items: list[AttachItem],
    *,
    limits: MediaLimits,
    now: datetime | None = None,
) -> list[OwnerPhoto]:
    """Like a deed, without a time window (self-logs are editable at any time, M2 D3) and
    without blur (private photos have no blur tool)."""
    now = now or utcnow()
    if await deeds.self_log_owner(session, self_log_id) != user_id:
        raise self_log_not_found()
    ensure_enabled(limits)
    _check_count(items, limits)
    _check_regions(items, limits, allowed=False)
    photos = await _attach(
        session,
        user_id,
        items,
        purpose=PhotoPurpose.SELF_LOG,
        owner_id=self_log_id,
        limits=limits,
        now=now,
    )
    return [_owner_photo(p, storage, limits, user_id, doer_id=None, now=now) for p in photos]


# --------------------------------------------------- owner reads (#7, #8)


def _owner_photo(
    photo: Photo,
    storage: ObjectStorage,
    limits: MediaLimits,
    viewer_id: uuid.UUID,
    *,
    doer_id: uuid.UUID | None,
    now: datetime,
) -> OwnerPhoto:
    """``doer_id`` is the caller when they view as the doer of a verified deed."""
    status: OwnerStatus
    thumb = display = None
    if photo.status == PhotoStatus.READY and photo.object_move is None:
        status = "ready"
        signed = sign_variants(storage, limits, _keys.variant_keys_of(photo.variant_keys), now=now)
        thumb, display = signed.thumb_url, signed.display_url
    elif photo.status in (PhotoStatus.FAILED, PhotoStatus.REJECTED):
        status = "failed"
    else:
        status = "processing"
    can_remove = photo.uploader_user_id == viewer_id or (
        doer_id == viewer_id and photo.status == PhotoStatus.READY
    )
    assert photo.position is not None
    return OwnerPhoto(
        id=photo.id,
        position=photo.position,
        status=status,
        thumb_url=thumb,
        display_url=display,
        blurred_count=len(photo.blur_regions),
        can_remove=can_remove,
    )


async def _visible_photos(
    session: AsyncSession, *, deed_id: uuid.UUID | None = None, self_log_id: uuid.UUID | None = None
) -> list[Photo]:
    owner = Photo.deed_id == deed_id if deed_id is not None else Photo.self_log_id == self_log_id
    rows = await session.scalars(
        select(Photo)
        .where(owner, Photo.status.in_(_OWNER_VISIBLE), Photo.takedown == PhotoTakedown.NONE)
        .order_by(Photo.position, Photo.id)
    )
    return list(rows)


_DECISION_OUTCOME: dict[PhotoDecisionAction, Decision] = {
    PhotoDecisionAction.KEEP: "kept",
    PhotoDecisionAction.REMOVE: "removed",
    PhotoDecisionAction.RESTORE: "kept",
}


@dataclass
class _Event:
    decision: Decision
    removal_reason: str | None
    removed_by: uuid.UUID | None
    at: datetime


async def _outcomes(
    session: AsyncSession, deed_id: uuid.UUID, limits: MediaLimits, now: datetime
) -> list[Outcome]:
    """Final outcomes on this deed's photos within ``report_outcome_display_days``: the latest
    admin decision per photo (a restore reads "kept"), and doer removals (Q29). Built from
    decisions only, so an open report changes nothing here."""
    since = now - timedelta(days=limits.report_outcome_display_days)
    latest: dict[uuid.UUID, _Event] = {}

    decisions = await session.execute(
        select(
            PhotoDecision.photo_id,
            PhotoDecision.action,
            PhotoDecision.removal_reason,
            PhotoDecision.created_at,
        )
        .join(Photo, Photo.id == PhotoDecision.photo_id)
        .where(Photo.deed_id == deed_id)
        .order_by(PhotoDecision.created_at, PhotoDecision.id)
    )
    for photo_id, action, reason, at in decisions:
        latest[photo_id] = _Event(
            decision=_DECISION_OUTCOME[PhotoDecisionAction(action)],
            removal_reason=reason.value
            if action == PhotoDecisionAction.REMOVE and reason
            else None,
            removed_by=None,
            at=at,
        )
    doer_removed = await session.execute(
        select(Photo.id, Photo.deleted_by, Photo.deleted_at).where(
            Photo.deed_id == deed_id,
            Photo.takedown == PhotoTakedown.DOER_REMOVED,
            Photo.deleted_at.is_not(None),
        )
    )
    for photo_id, by, removed_at in doer_removed:
        if removed_at is None:
            continue
        prior = latest.get(photo_id)
        if prior is None or removed_at >= prior.at:
            latest[photo_id] = _Event("removed_by_doer", None, by, removed_at)

    groups: dict[tuple[Decision, str | None, uuid.UUID | None], list[_Event]] = {}
    for event in latest.values():
        if event.at < since:
            continue
        groups.setdefault((event.decision, event.removal_reason, event.removed_by), []).append(
            event
        )
    people = await identity.public_summaries(
        session, {key[2] for key in groups if key[2] is not None}
    )
    out = []
    for (decision, why, by), events in groups.items():
        summary = people.get(by) if by is not None else None
        out.append(
            Outcome(
                decision=decision,
                removal_reason=why,
                removed_by=(
                    UserRef(
                        handle=summary.handle,
                        display_name=summary.display_name,
                        avatar=summary.avatar_ref,
                    )
                    if summary is not None
                    else None
                ),
                decided_at=max(e.at for e in events),
                photo_count=len(events),
            )
        )
    out.sort(key=lambda o: o.decided_at, reverse=True)
    return out


async def deed_photos(
    session: AsyncSession,
    storage: ObjectStorage,
    user_id: uuid.UUID,
    deed_id: uuid.UUID,
    *,
    limits: MediaLimits,
    now: datetime | None = None,
) -> DeedPhotosOut:
    """The reporter, or the doer of a verified deed (M2 D24); everyone else 404."""
    now = now or utcnow()
    deed = await deeds.get_deed(session, deed_id)
    if deed is None:
        raise deed_not_found()
    is_reporter = deed.reporter_id == user_id
    is_doer = deed.status == deeds.DeedStatus.VERIFIED and deed.doer_id == user_id
    if not (is_reporter or is_doer):
        raise deed_not_found()

    photos = await _visible_photos(session, deed_id=deed_id)
    doer_id = user_id if is_doer else None
    views = [_owner_photo(p, storage, limits, user_id, doer_id=doer_id, now=now) for p in photos]
    if not is_reporter:
        return DeedPhotosOut(photos=views, can_add=False, add_until=None, outcomes=[])

    live = sum(1 for p in photos if p.status in LIVE)
    editable = deed.status in _EDITABLE and limits.photos_enabled
    until = add_until(deed, limits) if editable else None
    can_add = until is not None and now <= until and live < limits.photos_per_owner
    return DeedPhotosOut(
        photos=views,
        can_add=can_add,
        add_until=until,
        outcomes=await _outcomes(session, deed_id, limits, now),
    )


async def self_log_photos(
    session: AsyncSession,
    storage: ObjectStorage,
    user_id: uuid.UUID,
    self_log_id: uuid.UUID,
    *,
    limits: MediaLimits,
    now: datetime | None = None,
) -> SelfLogPhotosOut:
    now = now or utcnow()
    if await deeds.self_log_owner(session, self_log_id) != user_id:
        raise self_log_not_found()
    photos = await _visible_photos(session, self_log_id=self_log_id)
    return SelfLogPhotosOut(
        photos=[_owner_photo(p, storage, limits, user_id, doer_id=None, now=now) for p in photos]
    )


# --------------------------------------------------- remove (#9)

# The uploader may remove their photo in any of these states (an unattached upload is
# discarded with DELETE /api/media/uploads/{id} instead, but this works too).
_REMOVABLE = (
    PhotoStatus.PENDING_UPLOAD,
    PhotoStatus.UPLOADED,
    *_OWNER_VISIBLE,
)


async def remove_photo(
    session: AsyncSession,
    user_id: uuid.UUID,
    photo_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Delete a photo for everyone. Returns every object key to delete right after commit
    (so issued URLs stop working at once, §4.7).

    - The uploader: their own photo, any time (Q30), unless an admin removed it.
    - The doer of a verified deed: any ``ready`` photo on it, final (Q29): ``doer_removed``,
      ``audit_log`` and ``PhotoRemovedByDoer``.
    - Anyone else: 404. Never touches the deed, feed, counts, verification, points or prizes.
    """
    now = now or utcnow()
    photo = await session.scalar(select(Photo).where(Photo.id == photo_id).with_for_update())
    if (
        photo is None
        or photo.status not in _REMOVABLE
        or photo.takedown != PhotoTakedown.NONE
        # A profile picture is removed with DELETE /media/me/avatar, which tells identity.
        or photo.purpose == PhotoPurpose.AVATAR
    ):
        raise photo_not_found()

    by_doer = False
    deed: deeds.DeedView | None = None
    if photo.uploader_user_id != user_id:
        if photo.purpose != PhotoPurpose.DEED or photo.deed_id is None:
            raise photo_not_found()
        deed = await deeds.get_deed(session, photo.deed_id)
        if (
            deed is None
            or deed.status != deeds.DeedStatus.VERIFIED
            or deed.doer_id != user_id
            or photo.status != PhotoStatus.READY
        ):
            raise photo_not_found()
        by_doer = True

    photo.status = PhotoStatus.DELETED
    photo.deleted_at = now
    photo.deleted_by = user_id
    # A pending admin hold or restore no longer applies: every object is deleted below
    # (SEC2 L6), and a move must never copy a deleted photo's variants back.
    photo.object_move = None
    if by_doer:
        photo.takedown = PhotoTakedown.DOER_REMOVED

    # An open report case on the photo ends without a decision.
    await session.execute(
        update(PhotoReportCase)
        .where(
            PhotoReportCase.photo_id == photo.id, PhotoReportCase.status == ReportCaseStatus.OPEN
        )
        .values(status=ReportCaseStatus.CLOSED, resolved_at=now)
        .execution_options(synchronize_session=False)
    )

    if by_doer:
        assert photo.deed_id is not None
        record_audit(
            session,
            entity_type="photo",
            entity_id=str(photo.id),
            action="photo.doer_removed",
            actor_id=user_id,
            new_value={
                "deed_id": str(photo.deed_id),
                "position": photo.position,
                "uploader_id": str(photo.uploader_user_id),
            },
        )
        emit(
            session,
            PhotoRemovedByDoer(
                photo_id=photo.id,
                deed_id=photo.deed_id,
                uploader_id=photo.uploader_user_id,
                doer_id=user_id,
            ),
        )
    await session.flush()
    return _keys.all_object_keys(photo.staging_key, photo.variant_keys)


async def purge_objects(
    session: AsyncSession,
    storage: ObjectStorage,
    photo_id: uuid.UUID,
    keys: list[str],
    *,
    now: datetime | None = None,
) -> bool:
    """After the removal committed: delete every object of the photo, then record it. On any
    failure the row stays unpurged and ``media.purge_deleted`` retries (B21)."""
    now = now or utcnow()
    try:
        for key in keys:
            await storage.delete(key)
    except Exception:
        return False
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
    return True
