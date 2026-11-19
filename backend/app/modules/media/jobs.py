"""Background jobs of the media module (Procrastinate). Organised by section.

Jobs run on the ``media`` queue, so photo processing can get its own worker later; in M3 the one
worker listens to every queue.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.jobs import job_app
from app.core.storage import ObjectStorage, get_storage
from app.modules.media import _cleanup, _moves, _worker
from app.modules.media._limits import MediaLimits, load_limits

# --------------------------------------------------- photo processing (B20, §4.4)


@job_app.periodic(cron="* * * * * */5", periodic_id="media_process_queued")
@job_app.task(name="media.process_queued", queue="media", queueing_lock="media_process_queued")
async def process_queued(timestamp: int) -> int:
    """Every 5 seconds: process queued photos, one claimed batch at a time, until a batch comes
    back short. Concurrent runs never share a photo (``FOR UPDATE SKIP LOCKED``)."""
    storage = get_storage()
    total = 0
    while True:
        async with SessionLocal() as session:
            count = await _worker.process_batch(session, storage)
        total += count
        if count < _worker.CLAIM_BATCH:
            return total


# --------------------------------------------------- cleanup (B21, §4.8)


async def _repeat(
    work: Callable[[AsyncSession, ObjectStorage, MediaLimits], Awaitable[int]],
) -> int:
    """Run batches in short transactions until one comes back short."""
    storage = get_storage()
    total = 0
    while True:
        async with SessionLocal() as session:
            _, limits = await load_limits(session)
            count = await work(session, storage, limits)
        total += count
        if count < _cleanup.BATCH:
            return total


@job_app.periodic(cron="*/30 * * * *", periodic_id="media_expire_uploads")
@job_app.task(name="media.expire_uploads", queue="media", queueing_lock="media_expire_uploads")
async def expire_uploads(timestamp: int) -> int:
    """Every 30 minutes: uploads never attached within the TTL -> expired, staging deleted."""
    return await _repeat(lambda s, st, lim: _cleanup.expire_uploads(s, st, lim))


@job_app.periodic(cron="7 * * * *", periodic_id="media_purge_detached")
@job_app.task(name="media.purge_detached", queue="media", queueing_lock="media_purge_detached")
async def purge_detached(timestamp: int) -> int:
    """Hourly: photos of deleted self-logs -> deleted, objects deleted."""
    return await _repeat(lambda s, st, _: _cleanup.purge_detached(s, st))


@job_app.periodic(cron="*/5 * * * *", periodic_id="media_purge_deleted")
@job_app.task(name="media.purge_deleted", queue="media", queueing_lock="media_purge_deleted")
async def purge_deleted(timestamp: int) -> int:
    """Every 5 minutes: retry object deletes of deleted photos (and sweep photos/{id}/)."""
    return await _repeat(lambda s, st, _: _cleanup.purge_deleted(s, st))


@job_app.periodic(cron="* * * * *", periodic_id="media_move_objects")
@job_app.task(name="media.move_objects", queue="media", queueing_lock="media_move_objects")
async def move_objects(timestamp: int) -> int:
    """Every minute: finish admin hold / restore moves left by a failed request. One batch per
    run, so a move that keeps failing can't make the job spin."""
    async with SessionLocal() as session:
        return await _moves.move_pending(session, get_storage())


@job_app.periodic(cron="23 2 * * *", periodic_id="media_purge_removed")
@job_app.task(name="media.purge_removed", queue="media", queueing_lock="media_purge_removed")
async def purge_removed(timestamp: int) -> int:
    """Nightly: held variants of admin-removed photos past the retention period (Q32)."""
    return await _repeat(lambda s, st, lim: _cleanup.purge_removed(s, st, lim))


@job_app.periodic(cron="53 2 * * *", periodic_id="media_sweep_staging")
@job_app.task(name="media.sweep_staging", queue="media", queueing_lock="media_sweep_staging")
async def sweep_staging(timestamp: int) -> int:
    """Nightly: staging objects older than 48 h that no row is waiting for."""
    storage = get_storage()
    async with SessionLocal() as session:
        return await _cleanup.sweep_staging(session, storage)
