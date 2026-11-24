"""The photo worker: claim queued photos, run the pipeline, store the variants (§4.4).

Flow per batch (``process_batch``):
1. Rows stuck in ``processing`` longer than ``processing_stuck_minutes`` go back to ``queued``,
   or to ``failed`` once they have used ``processing_max_attempts``.
2. Claim up to ``CLAIM_BATCH`` queued rows with ``FOR UPDATE SKIP LOCKED`` (concurrent workers
   never take the same row), mark them ``processing`` and commit.
3. Each claimed photo, outside any transaction: download the raw upload, hash it, refuse a file
   the doer removed from this deed (Q29), run ``_pipeline`` in a thread, upload the variants,
   then commit ``ready`` (or ``rejected`` / back to ``queued`` / ``failed``).
4. After the commit the staging object is deleted and ``staging_deleted_at`` set, so no
   unblurred copy is kept (D33, Q31). A failed delete is left for the cleanup jobs.

Every result is written only if the row is still the one this run claimed (same status and
``processing_started_at``). If the photo was deleted while it was processing, the variants
this run uploaded are deleted again.

Logs name the photo ID and the error type only: never a URL, key contents or image data.
"""

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import ObjectNotFound, ObjectStorage, ObjectTooLarge
from app.modules.media import _keys, _pipeline
from app.modules.media._limits import MediaLimits, load_limits
from app.modules.media.models import (
    Photo,
    PhotoPurpose,
    PhotoRejectReason,
    PhotoStatus,
    PhotoTakedown,
)

log = logging.getLogger(__name__)

CLAIM_BATCH = 10


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Claimed:
    id: uuid.UUID
    purpose: PhotoPurpose
    deed_id: uuid.UUID | None
    declared_type: str
    staging_key: str
    blur_regions: tuple[_pipeline.BlurRegion, ...]
    attempts: int
    started_at: datetime


def pipeline_config(limits: MediaLimits) -> _pipeline.PipelineConfig:
    return _pipeline.PipelineConfig(
        max_image_pixels=limits.max_image_pixels,
        min_image_side_px=limits.min_image_side_px,
        variants=tuple(_pipeline.VariantSpec(v.name, v.max_side_px) for v in limits.variants),
        webp_quality=limits.webp_quality,
    )


def regions_from_json(value: list[Any]) -> tuple[_pipeline.BlurRegion, ...]:
    return tuple(
        _pipeline.BlurRegion(x=float(r["x"]), y=float(r["y"]), w=float(r["w"]), h=float(r["h"]))
        for r in value
    )


# --------------------------------------------------- stuck rows and claiming


async def recover_stuck(session: AsyncSession, limits: MediaLimits, now: datetime) -> list[str]:
    """Stuck ``processing`` rows -> ``queued``, or ``failed`` after the last attempt. Returns the
    staging keys of rows that failed (to delete after commit)."""
    cutoff = now - timedelta(minutes=limits.processing_stuck_minutes)
    out_of_attempts = Photo.attempts >= limits.processing_max_attempts
    rows = await session.execute(
        update(Photo)
        .where(Photo.status == PhotoStatus.PROCESSING, Photo.processing_started_at < cutoff)
        .values(
            status=case(
                (out_of_attempts, PhotoStatus.FAILED.value), else_=PhotoStatus.QUEUED.value
            ),
            processing_started_at=None,
            processed_at=case((out_of_attempts, now), else_=None),
        )
        .returning(Photo.id, Photo.status, Photo.staging_key)
        .execution_options(synchronize_session=False)
    )
    failed = []
    for photo_id, status, key in rows:
        log.warning("photo %s was stuck in processing; now %s", photo_id, status)
        if status == PhotoStatus.FAILED:
            failed.append(key)
    return failed


async def claim(
    session: AsyncSession,
    now: datetime,
    batch: int = CLAIM_BATCH,
    *,
    only: Collection[uuid.UUID] | None = None,
) -> list[Claimed]:
    """Mark up to ``batch`` queued rows ``processing`` (oldest attach first). The caller
    commits. ``SKIP LOCKED``: rows another worker is claiming are skipped, never shared.

    The locking select is a ``MATERIALIZED`` CTE, so it runs exactly once (an ``IN`` subquery
    with ``LIMIT`` may be re-evaluated by the planner). ``only`` restricts the claim to given
    rows; the worker never passes it (tests use it to stay clear of other runs' rows)."""
    ids = select(Photo.id).where(Photo.status == PhotoStatus.QUEUED)
    if only is not None:
        ids = ids.where(Photo.id.in_(list(only)))
    picked = (
        ids.order_by(Photo.attached_at, Photo.id)
        .limit(batch)
        .with_for_update(skip_locked=True)
        .cte("claim")
        .prefix_with("MATERIALIZED")
    )
    rows = await session.execute(
        update(Photo)
        .where(Photo.id == picked.c.id)
        .values(
            status=PhotoStatus.PROCESSING,
            processing_started_at=now,
            attempts=Photo.attempts + 1,
        )
        .returning(
            Photo.id,
            Photo.purpose,
            Photo.deed_id,
            Photo.declared_type,
            Photo.staging_key,
            Photo.blur_regions,
            Photo.attempts,
        )
        .execution_options(synchronize_session=False)
    )
    return [
        Claimed(
            id=r.id,
            purpose=PhotoPurpose(r.purpose),
            deed_id=r.deed_id,
            declared_type=str(r.declared_type),
            staging_key=r.staging_key,
            blur_regions=regions_from_json(r.blur_regions),
            attempts=r.attempts,
            started_at=now,
        )
        for r in rows
    ]


# --------------------------------------------------- one photo


async def _removed_by_doer(session: AsyncSession, claimed: Claimed, sha: str) -> bool:
    """Whether the doer removed a photo with the same bytes from this deed (Q29)."""
    if claimed.purpose != PhotoPurpose.DEED or claimed.deed_id is None:
        return False
    found = await session.scalar(
        select(
            exists().where(
                Photo.deed_id == claimed.deed_id,
                Photo.takedown == PhotoTakedown.DOER_REMOVED,
                Photo.content_sha256 == sha,
                Photo.id != claimed.id,
            )
        )
    )
    return bool(found)


async def _locked_if_ours(session: AsyncSession, claimed: Claimed) -> Photo | None:
    """The row, locked, if it is still in the state this run claimed it in."""
    photo = await session.scalar(
        select(Photo)
        .where(Photo.id == claimed.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        photo is None
        or photo.status != PhotoStatus.PROCESSING
        or photo.processing_started_at != claimed.started_at
    ):
        return None
    return photo


async def _delete_staging(
    session: AsyncSession, storage: ObjectStorage, claimed: Claimed, now: datetime
) -> None:
    """Delete the raw upload after a final result was committed, then record it."""
    try:
        await storage.delete(claimed.staging_key)
    except Exception as exc:
        log.warning("photo %s: staging delete failed (%s); left for cleanup", claimed.id, type(exc))
        return
    await session.execute(
        update(Photo)
        .where(Photo.id == claimed.id, Photo.staging_deleted_at.is_(None))
        .values(staging_deleted_at=now)
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def _delete_keys(storage: ObjectStorage, photo_id: uuid.UUID, keys: list[str]) -> bool:
    ok = True
    for key in keys:
        try:
            await storage.delete(key)
        except Exception as exc:
            log.warning("photo %s: variant delete failed (%s)", photo_id, type(exc))
            ok = False
    return ok


async def _finish_rejected(
    session: AsyncSession,
    storage: ObjectStorage,
    claimed: Claimed,
    reason: PhotoRejectReason,
    sha: str | None,
    now: datetime,
) -> None:
    photo = await _locked_if_ours(session, claimed)
    if photo is None:
        await session.commit()
        return
    photo.status = PhotoStatus.REJECTED
    photo.reject_reason = reason
    photo.content_sha256 = sha
    photo.processed_at = now
    await session.commit()
    log.info("photo %s rejected: %s", claimed.id, reason.value)
    await _delete_staging(session, storage, claimed, now)


async def _retry_or_fail(
    session: AsyncSession,
    storage: ObjectStorage,
    claimed: Claimed,
    limits: MediaLimits,
    now: datetime,
    *,
    permanent: bool = False,
) -> None:
    photo = await _locked_if_ours(session, claimed)
    if photo is None:
        await session.commit()
        return
    if permanent or claimed.attempts >= limits.processing_max_attempts:
        photo.status = PhotoStatus.FAILED
        photo.processed_at = now
        photo.processing_started_at = None
        await session.commit()
        log.warning("photo %s failed after %d attempts", claimed.id, claimed.attempts)
        await _delete_staging(session, storage, claimed, now)
        return
    photo.status = PhotoStatus.QUEUED
    photo.processing_started_at = None
    await session.commit()


async def _finish_ready(
    session: AsyncSession,
    storage: ObjectStorage,
    claimed: Claimed,
    variants: dict[str, _pipeline.EncodedVariant],
    keys: dict[str, str],
    sha: str,
    now: datetime,
) -> None:
    photo = await _locked_if_ours(session, claimed)
    if photo is None:
        # Deleted, expired or taken over while we worked. Remove what we uploaded unless the
        # row now points at exactly these objects (another run finished the same work) or
        # another run is still working on it (it writes the same keys).
        current = await session.scalar(select(Photo).where(Photo.id == claimed.id))
        await session.commit()
        keep = current is not None and (
            current.status in (PhotoStatus.QUEUED, PhotoStatus.PROCESSING)
            or (
                current.status == PhotoStatus.READY
                and _keys.variant_keys_of(current.variant_keys) == keys
            )
        )
        if not keep and not await _delete_keys(storage, claimed.id, list(keys.values())):
            # Leave the leftovers to media.purge_deleted, which also lists photos/{id}/.
            await session.execute(
                update(Photo)
                .where(Photo.id == claimed.id, Photo.status == PhotoStatus.DELETED)
                .values(objects_purged_at=None)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
        return
    display = variants["display"]
    photo.status = PhotoStatus.READY
    photo.variant_keys = keys
    photo.width = display.width
    photo.height = display.height
    photo.content_sha256 = sha
    photo.processed_at = now
    await session.commit()
    await _delete_staging(session, storage, claimed, now)


async def process_one(
    session: AsyncSession,
    storage: ObjectStorage,
    limits: MediaLimits,
    claimed: Claimed,
    *,
    now: datetime | None = None,
) -> PhotoStatus | None:
    """Process one claimed photo. Returns the status it was left in by this run (None when the
    row had changed under it)."""

    def clock() -> datetime:
        return now if now is not None else _utcnow()

    try:
        data = await storage.get_bytes(claimed.staging_key, max_bytes=limits.upload_max_bytes)
    except ObjectTooLarge:
        await _finish_rejected(
            session, storage, claimed, PhotoRejectReason.TOO_LARGE, None, clock()
        )
        return PhotoStatus.REJECTED
    except ObjectNotFound:
        # The raw upload is gone: nothing to retry.
        await _retry_or_fail(session, storage, claimed, limits, clock(), permanent=True)
        return PhotoStatus.FAILED
    except Exception as exc:
        log.warning("photo %s: download failed (%s)", claimed.id, type(exc))
        await _retry_or_fail(session, storage, claimed, limits, clock())
        return None

    sha = hashlib.sha256(data).hexdigest()
    if await _removed_by_doer(session, claimed, sha):
        await _finish_rejected(
            session, storage, claimed, PhotoRejectReason.REMOVED_BY_DOER, sha, clock()
        )
        return PhotoStatus.REJECTED
    await session.commit()  # end the read transaction before the CPU work

    job = _pipeline.PhotoJob(
        data=data,
        declared_type=claimed.declared_type,
        owner_kind=claimed.purpose.value,
        regions=claimed.blur_regions,
        config=pipeline_config(limits),
    )
    del data
    try:
        variants = await asyncio.to_thread(_pipeline.run, job)
    except _pipeline.PhotoRejected as rejected:
        await _finish_rejected(session, storage, claimed, rejected.reason, sha, clock())
        return PhotoStatus.REJECTED
    except Exception as exc:
        log.warning("photo %s: processing error (%s)", claimed.id, type(exc))
        await _retry_or_fail(session, storage, claimed, limits, clock())
        return None

    keys = {name: _keys.variant_key(claimed.id, name) for name in variants}
    try:
        for name, variant in variants.items():
            await storage.put_bytes(
                keys[name], variant.data, variant.content_type, _keys.CACHE_CONTROL
            )
    except Exception as exc:
        log.warning("photo %s: variant upload failed (%s)", claimed.id, type(exc))
        await _delete_keys(storage, claimed.id, list(keys.values()))
        await _retry_or_fail(session, storage, claimed, limits, clock())
        return None

    await _finish_ready(session, storage, claimed, variants, keys, sha, clock())
    return PhotoStatus.READY


async def process_batch(
    session: AsyncSession,
    storage: ObjectStorage,
    *,
    now: datetime | None = None,
    batch: int = CLAIM_BATCH,
) -> int:
    """Recover stuck rows, claim a batch and process it. Returns how many were claimed."""
    _, limits = await load_limits(session)
    started = now or _utcnow()
    failed_staging = await recover_stuck(session, limits, started)
    claimed = await claim(session, started, batch)
    await session.commit()
    deleted: list[str] = []
    for key in failed_staging:
        try:
            await storage.delete(key)
        except Exception as exc:
            log.warning("staging delete failed (%s); left for cleanup", type(exc))
        else:
            deleted.append(key)
    if deleted:
        await session.execute(
            update(Photo)
            .where(
                and_(
                    Photo.staging_key.in_(deleted),
                    Photo.status == PhotoStatus.FAILED,
                    Photo.staging_deleted_at.is_(None),
                )
            )
            .values(staging_deleted_at=started)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    for item in claimed:
        await process_one(session, storage, limits, item, now=now)
    return len(claimed)
