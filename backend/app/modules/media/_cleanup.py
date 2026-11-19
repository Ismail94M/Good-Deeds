"""Cleanup jobs' work (§4.8). Each function handles one batch in short transactions and returns
how many rows it finished; ``jobs.py`` repeats while a batch comes back full.

Rows are claimed with ``FOR UPDATE SKIP LOCKED`` so two runs never work on one row. Storage
calls happen after the row change is committed; a failed delete leaves the row for the next run
(``objects_purged_at`` stays null), so a stuck object never blocks the others for long and an
issued URL is never left working by a half-done purge.
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import ObjectStorage
from app.modules.media import _keys
from app.modules.media._common import utcnow
from app.modules.media._limits import MediaLimits
from app.modules.media.models import (
    Photo,
    PhotoPurpose,
    PhotoStatus,
    PhotoTakedown,
)

log = logging.getLogger(__name__)

BATCH = 100
# Staging objects this old with no row still waiting for them are deleted (catches PUTs that
# arrived after their upload expired). A code constant: it only has to exceed the upload TTL.
STAGING_SWEEP_AGE = timedelta(hours=48)
# Rows whose staging object may still be needed.
_STAGING_IN_USE = (
    PhotoStatus.PENDING_UPLOAD,
    PhotoStatus.UPLOADED,
    PhotoStatus.QUEUED,
    PhotoStatus.PROCESSING,
)


async def delete_all_objects(storage: ObjectStorage, photo: Photo, *, far_future: datetime) -> None:
    """Every object the photo may have: staging, variants, held copies, plus anything else
    under ``photos/{id}/`` or ``held/{id}/`` (e.g. variants a worker wrote after a deletion)."""
    keys = _keys.all_object_keys(photo.staging_key, photo.variant_keys)
    for prefix in (f"{_keys.PHOTOS_PREFIX}{photo.id}/", f"{_keys.HELD_PREFIX}{photo.id}/"):
        async for key in storage.list_older_than(prefix, far_future):
            keys.append(key)
    for key in dict.fromkeys(keys):
        await storage.delete(key)


async def _purge_rows(
    session: AsyncSession, storage: ObjectStorage, photos: Sequence[Photo], now: datetime
) -> int:
    """Delete the objects of rows already committed as deleted or expired-from-hold; mark each
    purged on success."""
    done = 0
    for photo in photos:
        try:
            await delete_all_objects(storage, photo, far_future=now + timedelta(days=1))
        except Exception as exc:
            log.warning("photo %s: purge failed (%s); retried later", photo.id, type(exc))
            continue
        await session.execute(
            update(Photo)
            .where(Photo.id == photo.id, Photo.objects_purged_at.is_(None))
            .values(
                objects_purged_at=now,
                staging_deleted_at=func.coalesce(Photo.staging_deleted_at, now),
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        done += 1
    return done


# --------------------------------------------------- media.expire_uploads


async def expire_uploads(
    session: AsyncSession, storage: ObjectStorage, limits: MediaLimits, now: datetime | None = None
) -> int:
    """Uploads never attached within ``pending_upload_ttl_hours`` -> ``expired``, staging
    deleted. Returns rows expired."""
    now = now or utcnow()
    cutoff = now - timedelta(hours=limits.pending_upload_ttl_hours)
    rows = list(
        await session.scalars(
            select(Photo)
            .where(
                Photo.status.in_((PhotoStatus.PENDING_UPLOAD, PhotoStatus.UPLOADED)),
                Photo.created_at < cutoff,
            )
            .order_by(Photo.created_at)
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        )
    )
    for photo in rows:
        photo.status = PhotoStatus.EXPIRED
    await session.commit()
    for photo in rows:
        try:
            await storage.delete(photo.staging_key)
        except Exception as exc:  # media.sweep_staging deletes it later
            log.warning("photo %s: staging delete failed (%s)", photo.id, type(exc))
            continue
        photo.staging_deleted_at = now
        await session.commit()
    return len(rows)


# --------------------------------------------------- media.purge_detached


async def purge_detached(
    session: AsyncSession, storage: ObjectStorage, now: datetime | None = None
) -> int:
    """Self-log photos whose self-log was deleted (``self_log_id`` set to NULL) -> ``deleted``,
    objects deleted. Returns rows deleted."""
    now = now or utcnow()
    rows = list(
        await session.scalars(
            select(Photo)
            .where(
                Photo.purpose == PhotoPurpose.SELF_LOG,
                Photo.self_log_id.is_(None),
                Photo.attached_at.is_not(None),
                Photo.status.not_in((PhotoStatus.DELETED, PhotoStatus.EXPIRED)),
            )
            .order_by(Photo.attached_at)
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        )
    )
    for photo in rows:
        photo.status = PhotoStatus.DELETED
        photo.deleted_at = now
        photo.deleted_by = None  # the system
    await session.commit()
    await _purge_rows(session, storage, rows, now)
    return len(rows)


# --------------------------------------------------- media.purge_deleted


async def purge_deleted(
    session: AsyncSession, storage: ObjectStorage, now: datetime | None = None
) -> int:
    """``deleted`` rows whose objects aren't purged yet (a failed delete right after a removal,
    or variants a worker wrote after the deletion). Returns rows purged."""
    now = now or utcnow()
    rows = list(
        await session.scalars(
            select(Photo)
            .where(Photo.status == PhotoStatus.DELETED, Photo.objects_purged_at.is_(None))
            .order_by(Photo.deleted_at)
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        )
    )
    await session.commit()
    return await _purge_rows(session, storage, rows, now)


# --------------------------------------------------- media.purge_removed


async def purge_removed(
    session: AsyncSession, storage: ObjectStorage, limits: MediaLimits, now: datetime | None = None
) -> int:
    """Admin-removed photos past ``removed_photo_retention_days`` (Q32): the held variants are
    deleted and ``objects_purged_at`` set, so restore answers 409 ``not_restorable``. The row,
    its cases, decisions and audit rows stay. Returns rows purged."""
    now = now or utcnow()
    cutoff = now - timedelta(days=limits.removed_photo_retention_days)
    rows = list(
        await session.scalars(
            select(Photo)
            .where(
                Photo.takedown == PhotoTakedown.ADMIN_REMOVED,
                Photo.removed_at < cutoff,
                Photo.objects_purged_at.is_(None),
                Photo.object_move.is_(None),
            )
            .order_by(Photo.removed_at)
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        )
    )
    await session.commit()
    return await _purge_rows(session, storage, rows, now)


# --------------------------------------------------- media.sweep_staging


def _photo_id_of(key: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(key.removeprefix(_keys.STAGING_PREFIX))
    except ValueError:
        return None


async def sweep_staging(
    session: AsyncSession, storage: ObjectStorage, now: datetime | None = None
) -> int:
    """Staging objects older than ``STAGING_SWEEP_AGE`` whose row isn't waiting for them
    (missing, or past ``processing``) are deleted. Returns objects deleted."""
    now = now or utcnow()
    keys = [
        key async for key in storage.list_older_than(_keys.STAGING_PREFIX, now - STAGING_SWEEP_AGE)
    ]
    deleted = 0
    for start in range(0, len(keys), BATCH):
        chunk = keys[start : start + BATCH]
        ids = {k: _photo_id_of(k) for k in chunk}
        wanted = set(
            await session.scalars(
                select(Photo.id).where(
                    Photo.id.in_([i for i in ids.values() if i is not None]),
                    Photo.status.in_(_STAGING_IN_USE),
                )
            )
        )
        done: list[uuid.UUID] = []
        for key, photo_id in ids.items():
            if photo_id is not None and photo_id in wanted:
                continue
            try:
                await storage.delete(key)
            except Exception as exc:
                log.warning("staging sweep: delete failed (%s)", type(exc))
                continue
            deleted += 1
            if photo_id is not None:
                done.append(photo_id)
        if done:
            await session.execute(
                update(Photo)
                .where(Photo.id.in_(done), Photo.staging_deleted_at.is_(None))
                .values(staging_deleted_at=now)
                .execution_options(synchronize_session=False)
            )
        await session.commit()
    return deleted
