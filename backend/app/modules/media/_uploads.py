"""Upload-then-confirm (D32, §3.3 #1 to #4): options, create, complete, discard.

Layers (§4.4): create checks the declared type and size and the pending cap; the presigned PUT
signs ``Content-Type`` and ``Content-Length`` so the store refuses anything else; complete
checks the stored size (HEAD) and the first 16 bytes against the declared type. The worker
re-checks everything again when it decodes the file.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import advisory_xact_lock
from app.core.errors import AppError, Conflict, Forbidden, TooManyRequests
from app.core.ratelimit import RateLimiter
from app.core.security import keyed_hash
from app.core.storage import ObjectStorage
from app.modules.media import _avatars, _keys
from app.modules.media._common import photo_not_found, utcnow
from app.modules.media._limits import MediaLimits
from app.modules.media._pipeline import magic_matches
from app.modules.media.models import (
    PHOTO_REPORT_PRIORITY,
    Photo,
    PhotoPurpose,
    PhotoRejectReason,
    PhotoReportReason,
    PhotoStatus,
    PhotoType,
)
from app.modules.media.schemas import (
    CompleteOut,
    OptionsOut,
    ReportReasonOut,
    SignedUpload,
    UploadCreate,
    UploadOut,
)

# Copy from the photos canvas (report sheet), in the order shown.
REPORT_REASON_LABELS: dict[PhotoReportReason, str] = {
    PhotoReportReason.NOT_CONSENTED: "I'm in this photo and didn't agree",
    PhotoReportReason.CHILD_NO_GUARDIAN_CONSENT: "A child is shown without a parent's consent",
    PhotoReportReason.PRIVATE_DETAILS: "It shows private details (like a phone number or address)",
    PhotoReportReason.INAPPROPRIATE: "It's inappropriate or harmful",
    PhotoReportReason.OTHER: "Something else",
}

# Bytes read back from storage for the magic-number check (§3.3 #3).
MAGIC_PREFIX_BYTES = 16


class BadUpload(AppError):
    status_code = 400


def options(limits: MediaLimits) -> OptionsOut:
    return OptionsOut(
        photos_enabled=limits.photos_enabled,
        photos_per_owner=limits.photos_per_owner,
        photo_edit_window_hours=limits.photo_edit_window_hours,
        upload_allowed_types=list(limits.upload_allowed_types),
        upload_max_bytes=limits.upload_max_bytes,
        blur_max_regions=limits.blur_max_regions,
        blur_min_region=limits.blur_min_region,
        decision_note_max=limits.decision_note_max,
        report_reasons=[
            ReportReasonOut(id=reason.value, label=label, priority=PHOTO_REPORT_PRIORITY[reason])
            for reason, label in REPORT_REASON_LABELS.items()
        ],
        avatar_report_reasons=_avatars.avatar_report_reasons(),
    )


def ensure_enabled(limits: MediaLimits) -> None:
    if not limits.photos_enabled:
        raise Forbidden("Photos are switched off for now.", code="photos_disabled")


async def enforce_upload_limits(
    limiter: RateLimiter, limits: MediaLimits, user_id: uuid.UUID, ip: str
) -> None:
    """Account and IP limits on creating uploads. Runs before any lookup."""
    await limiter.hit(f"upload:u:{user_id}", limits.rate_upload_user)
    await limiter.hit(f"upload:ip:{ip}", limits.rate_upload_ip)


async def enforce_write_limit(
    limiter: RateLimiter, limits: MediaLimits, user_id: uuid.UUID
) -> None:
    """Attach and remove (``photowrite:u``). Runs before any lookup."""
    await limiter.hit(f"photowrite:u:{user_id}", limits.rate_photo_write_user)


async def create_upload(
    session: AsyncSession,
    storage: ObjectStorage,
    user_id: uuid.UUID,
    data: UploadCreate,
    *,
    limits: MediaLimits,
    ip: str,
    now: datetime | None = None,
) -> UploadOut:
    now = now or utcnow()
    if data.purpose == PhotoPurpose.AVATAR.value:
        _avatars.ensure_avatars_enabled(limits)
    else:
        ensure_enabled(limits)
    if data.content_type not in limits.upload_allowed_types:
        raise BadUpload("Choose a JPG, PNG or WebP photo.", code="unsupported_type")
    if data.size_bytes > limits.upload_max_bytes:
        raise BadUpload("This photo is too large.", code="too_large")

    # Serialise one user's creates, so the pending cap holds under concurrency.
    await advisory_xact_lock(session, "media_uploads", str(user_id))
    pending = await session.scalar(
        select(func.count())
        .select_from(Photo)
        .where(
            Photo.uploader_user_id == user_id,
            Photo.status.in_((PhotoStatus.PENDING_UPLOAD, PhotoStatus.UPLOADED)),
        )
    )
    if (pending or 0) >= limits.max_pending_uploads_per_user:
        raise TooManyRequests(
            "You have too many photos waiting. Send or remove some first.",
            code="too_many_pending_uploads",
        )

    photo_id = uuid.uuid4()
    photo = Photo(
        id=photo_id,
        uploader_user_id=user_id,
        purpose=PhotoPurpose(data.purpose),
        status=PhotoStatus.PENDING_UPLOAD,
        declared_type=PhotoType(data.content_type),
        declared_bytes=data.size_bytes,
        staging_key=_keys.staging_key(photo_id),
        upload_ip_hash=keyed_hash("ip", ip),
        created_at=now,
    )
    session.add(photo)
    await session.flush()
    signed = storage.presign_put(
        photo.staging_key, data.content_type, data.size_bytes, limits.upload_url_ttl_seconds
    )
    return UploadOut(
        photo_id=photo_id,
        upload=SignedUpload(url=signed.url, method="PUT", headers=signed.headers),
        expires_at=now + timedelta(seconds=limits.upload_url_ttl_seconds),
    )


async def _own_upload(session: AsyncSession, user_id: uuid.UUID, photo_id: uuid.UUID) -> Photo:
    photo = await session.scalar(
        select(Photo)
        .where(Photo.id == photo_id, Photo.uploader_user_id == user_id)
        .with_for_update()
    )
    if photo is None:
        raise photo_not_found()
    return photo


async def _reject(
    session: AsyncSession,
    storage: ObjectStorage,
    photo: Photo,
    reason: PhotoRejectReason,
    now: datetime,
) -> None:
    """Record a rejected upload, commit it, and delete the object (the caller then raises)."""
    photo.status = PhotoStatus.REJECTED
    photo.reject_reason = reason
    photo.processed_at = now
    await session.commit()
    await mark_staging_deleted(session, storage, photo.id, photo.staging_key, now)


async def complete_upload(
    session: AsyncSession,
    storage: ObjectStorage,
    user_id: uuid.UUID,
    photo_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> CompleteOut:
    """Check the stored object against what was declared. A rejection is committed here (row
    ``rejected``, object deleted) before the error is raised."""
    now = now or utcnow()
    photo = await _own_upload(session, user_id, photo_id)
    if photo.status == PhotoStatus.UPLOADED:
        return CompleteOut(photo_id=photo.id, status="uploaded")  # idempotent
    if photo.status != PhotoStatus.PENDING_UPLOAD:
        raise photo_not_found()

    info = await storage.head(photo.staging_key)
    if info is None:
        raise Conflict("The photo hasn't finished uploading.", code="upload_missing")
    if info.size != photo.declared_bytes:
        await _reject(session, storage, photo, PhotoRejectReason.SIZE_MISMATCH, now)
        raise BadUpload("The photo didn't upload completely. Try again.", code="size_mismatch")
    head = await storage.get_range(photo.staging_key, 0, MAGIC_PREFIX_BYTES - 1)
    if not magic_matches(head, photo.declared_type):
        await _reject(session, storage, photo, PhotoRejectReason.UNSUPPORTED_TYPE, now)
        raise BadUpload("Choose a JPG, PNG or WebP photo.", code="unsupported_type")

    photo.status = PhotoStatus.UPLOADED
    photo.uploaded_at = now
    return CompleteOut(photo_id=photo.id, status="uploaded")


_DISCARDABLE = (PhotoStatus.PENDING_UPLOAD, PhotoStatus.UPLOADED)


async def discard_upload(
    session: AsyncSession,
    user_id: uuid.UUID,
    photo_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> str | None:
    """Drop an upload that was never attached (row ``expired``). Returns the staging key to
    delete after commit, or None when there is nothing to do (already gone: 204 again)."""
    now = now or utcnow()
    photo = await _own_upload(session, user_id, photo_id)
    if photo.deed_id is not None or photo.self_log_id is not None or photo.attached_at:
        raise Conflict(
            "This photo is already attached. Remove it from the deed instead.",
            code="photo_attached",
        )
    if photo.status not in _DISCARDABLE:
        return None
    photo.status = PhotoStatus.EXPIRED
    return photo.staging_key


async def mark_staging_deleted(
    session: AsyncSession, storage: ObjectStorage, photo_id: uuid.UUID, key: str, now: datetime
) -> None:
    """After commit: delete a staging object and record it. A failure is left for cleanup."""
    try:
        await storage.delete(key)
    except Exception:
        return
    photo = await session.get(Photo, photo_id)
    if photo is not None and photo.staging_deleted_at is None:
        photo.staging_deleted_at = now
        await session.commit()
