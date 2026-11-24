"""Cleanup jobs and object moves (§4.8, §4.7, Q31, Q32)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import MemoryStorage
from app.modules.deeds.models import SelfLog
from app.modules.media import _cleanup, _keys, _moves, _worker
from app.modules.media.models import ObjectMove, PhotoStatus, PhotoTakedown
from tests.deeds.helpers import make_member, scan_and_report
from tests.media.helpers import (
    drain,
    fetch,
    limits,
    publish_media,
    queued_photo,
    ready_photo,
    self_log,
    uploaded_photo,
)


def _age(storage: MemoryStorage, key: str, by: timedelta) -> None:
    storage.objects[key].last_modified = datetime.now(UTC) - by


# --------------------------------------------------- expire_uploads


async def test_expire_uploads(session: AsyncSession, storage: MemoryStorage) -> None:
    owner = await make_member(session)
    old = await uploaded_photo(session, storage, owner)
    pending = await uploaded_photo(session, storage, owner, status=PhotoStatus.PENDING_UPLOAD)
    young = await uploaded_photo(session, storage, owner)
    lim = await limits(session)
    later = datetime.now(UTC) + timedelta(hours=lim.pending_upload_ttl_hours, minutes=1)
    for photo in (old, pending):
        row = await fetch(session, photo.id)
        row.created_at = datetime.now(UTC) - timedelta(hours=lim.pending_upload_ttl_hours + 1)
    await session.commit()

    await drain(lambda: _cleanup.expire_uploads(session, storage, lim), _cleanup.BATCH)
    for photo in (old, pending):
        row = await fetch(session, photo.id)
        assert row.status == PhotoStatus.EXPIRED and row.staging_deleted_at is not None
        assert photo.staging_key not in storage.objects
    assert (await fetch(session, young.id)).status == PhotoStatus.UPLOADED
    assert young.staging_key in storage.objects
    # The TTL comes from config; once past it the young one expires too.
    await drain(lambda: _cleanup.expire_uploads(session, storage, lim, now=later), _cleanup.BATCH)
    assert (await fetch(session, young.id)).status == PhotoStatus.EXPIRED


# --------------------------------------------------- purge_detached


async def test_deleting_a_self_log_purges_its_photos(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    log_id = await self_log(session, owner)
    photo = await ready_photo(session, storage, owner, self_log_id=log_id)
    keys = list((await fetch(session, photo.id)).variant_keys.values())  # type: ignore[union-attr]
    log = await session.get(SelfLog, log_id)
    await session.delete(log)
    await session.commit()
    assert (await fetch(session, photo.id)).self_log_id is None  # ON DELETE SET NULL

    await drain(lambda: _cleanup.purge_detached(session, storage), _cleanup.BATCH)
    row = await fetch(session, photo.id)
    assert row.status == PhotoStatus.DELETED and row.deleted_by is None
    assert row.objects_purged_at is not None
    assert not any(k in storage.objects for k in keys)


# --------------------------------------------------- purge_deleted


class _FailOnce(MemoryStorage):
    """Fails the first delete of a key containing ``target`` (set once setup is done)."""

    target: str = ""
    failed: bool = False

    async def delete(self, key: str) -> None:
        if self.target and self.target in key and not self.failed:
            self.failed = True
            raise ConnectionError("storage down")
        await super().delete(key)


async def test_purge_deleted_retries_and_sweeps_orphans(session: AsyncSession) -> None:
    storage = _FailOnce()
    owner = await make_member(session)
    photo = await ready_photo(session, storage, owner, self_log_id=await self_log(session, owner))
    row = await fetch(session, photo.id)
    row.status, row.deleted_at = PhotoStatus.DELETED, datetime.now(UTC)
    await session.commit()
    # An orphan a late worker might have written.
    orphan = f"photos/{photo.id}/display-9.webp"
    await storage.put_bytes(orphan, b"x", "image/webp", "")
    storage.target = str(photo.id)

    await _cleanup.purge_deleted(session, storage)  # our row's first delete fails
    assert (await fetch(session, photo.id)).objects_purged_at is None
    await drain(lambda: _cleanup.purge_deleted(session, storage), _cleanup.BATCH)
    assert storage.objects == {}
    assert (await fetch(session, photo.id)).objects_purged_at is not None


async def test_worker_leftovers_after_a_deletion_go_back_to_the_purge_job(
    session: AsyncSession,
) -> None:
    """A photo deleted while processing whose variant cleanup fails is marked unpurged again,
    so media.purge_deleted removes the variants (note 12)."""

    class _NoDeletes(MemoryStorage):
        async def delete(self, key: str) -> None:
            raise ConnectionError("storage down")

    storage = _NoDeletes()
    owner = await make_member(session)
    photo = await queued_photo(session, storage, owner, self_log_id=await self_log(session, owner))
    lim = await limits(session)
    [claimed] = await _worker.claim(session, datetime.now(UTC), batch=1)  # ours is oldest
    await session.commit()
    row = await fetch(session, photo.id)
    row.status, row.deleted_at = PhotoStatus.DELETED, datetime.now(UTC)
    row.objects_purged_at = datetime.now(UTC)  # the delete endpoint purged what it knew of
    await session.commit()
    await _worker.process_one(session, storage, lim, claimed)
    assert (await fetch(session, photo.id)).objects_purged_at is None
    assert any(k.startswith(f"photos/{photo.id}/") for k in storage.objects)
    healthy = MemoryStorage(objects=dict(storage.objects))
    await drain(lambda: _cleanup.purge_deleted(session, healthy), _cleanup.BATCH)
    assert (await fetch(session, photo.id)).objects_purged_at is not None
    assert healthy.objects == {}


# --------------------------------------------------- move_objects


async def _removed(session: AsyncSession, storage: MemoryStorage) -> uuid.UUID:
    reporter, doer = await make_member(session), await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    await session.commit()
    photo = await ready_photo(session, storage, reporter, deed_id=deed.id)
    row = await fetch(session, photo.id)
    row.takedown, row.removed_at = PhotoTakedown.ADMIN_REMOVED, datetime.now(UTC)
    row.object_move = ObjectMove.HOLD
    await session.commit()
    return photo.id


async def test_move_objects_finishes_hold_and_release(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    photo_id = await _removed(session, storage)
    live = [f"photos/{photo_id}/thumb-1.webp", f"photos/{photo_id}/display-1.webp"]
    held = [k.replace("photos/", "held/", 1) for k in live]

    await _moves.run_move(session, storage, photo_id)
    assert sorted(storage.objects) == sorted(held)
    assert (await fetch(session, photo_id)).object_move is None
    assert await _moves.run_move(session, storage, photo_id) is True  # nothing left: no-op
    assert sorted(storage.objects) == sorted(held)

    row = await fetch(session, photo_id)
    row.takedown, row.removed_at, row.object_move = PhotoTakedown.NONE, None, ObjectMove.RELEASE
    await session.commit()
    # The job's batch function (one batch per run; repeated only in case other committed rows
    # with pending moves sort before ours).
    await drain(lambda: _moves.move_pending(session, storage), _moves.MOVE_BATCH)
    row = await fetch(session, photo_id)
    assert row.object_move is None
    assert sorted(storage.objects) == sorted(row.variant_keys.values())  # type: ignore[union-attr]
    assert all(k.endswith("-2.webp") for k in storage.objects)


async def test_moves_are_idempotent_and_retried(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    photo_id = await _removed(session, storage)

    class _Broken(MemoryStorage):
        async def copy(self, src_key: str, dst_key: str) -> bool:
            raise ConnectionError("storage down")

    broken = _Broken(objects=storage.objects)
    assert await _moves.run_move(session, broken, photo_id) is False
    assert (await fetch(session, photo_id)).object_move == ObjectMove.HOLD
    # Half done: one variant already held, the other still live.
    await storage.copy(f"photos/{photo_id}/thumb-1.webp", f"held/{photo_id}/thumb-1.webp")
    await storage.delete(f"photos/{photo_id}/thumb-1.webp")
    assert await _moves.run_move(session, storage, photo_id) is True
    assert sorted(storage.objects) == [
        f"held/{photo_id}/display-1.webp",
        f"held/{photo_id}/thumb-1.webp",
    ]
    # A restore before the hold finished still works (sources may be live or held).
    row = await fetch(session, photo_id)
    row.object_move = ObjectMove.RELEASE
    row.takedown, row.removed_at = PhotoTakedown.NONE, None
    await session.commit()
    await storage.copy(f"held/{photo_id}/thumb-1.webp", f"photos/{photo_id}/thumb-1.webp")
    await storage.delete(f"held/{photo_id}/thumb-1.webp")
    assert await _moves.run_move(session, storage, photo_id) is True
    assert sorted(storage.objects) == [
        f"photos/{photo_id}/display-2.webp",
        f"photos/{photo_id}/thumb-2.webp",
    ]


def test_next_version_key() -> None:
    pid = uuid.uuid4()
    assert _moves.next_version_key(f"photos/{pid}/display-1.webp") == f"photos/{pid}/display-2.webp"
    assert _moves.next_version_key(f"photos/{pid}/thumb-12.webp") == f"photos/{pid}/thumb-13.webp"
    assert _keys.held_key(f"photos/{pid}/thumb-1.webp") == f"held/{pid}/thumb-1.webp"


# --------------------------------------------------- sweep_staging


async def test_sweep_staging(session: AsyncSession, storage: MemoryStorage) -> None:
    owner = await make_member(session)
    waiting = await uploaded_photo(session, storage, owner)
    expired = await uploaded_photo(session, storage, owner)
    row = await fetch(session, expired.id)
    row.status = PhotoStatus.EXPIRED
    await session.commit()
    stray = f"staging/{uuid.uuid4()}"
    junk = "staging/not-a-uuid"
    fresh = f"staging/{uuid.uuid4()}"
    for key in (stray, junk, fresh):
        await storage.put_bytes(key, b"x", "image/jpeg", "")
    for key in (waiting.staging_key, expired.staging_key, stray, junk):
        _age(storage, key, timedelta(hours=49))

    await _cleanup.sweep_staging(session, storage)
    assert sorted(storage.objects) == sorted([waiting.staging_key, fresh])
    assert (await fetch(session, expired.id)).staging_deleted_at is not None


async def test_cleanup_respects_config(session: AsyncSession, storage: MemoryStorage) -> None:
    owner = await make_member(session)
    photo = await uploaded_photo(session, storage, owner)
    lim = await publish_media(session, pending_upload_ttl_hours=1)
    later = datetime.now(UTC) + timedelta(hours=1, minutes=1)
    await drain(lambda: _cleanup.expire_uploads(session, storage, lim, now=later), _cleanup.BATCH)
    assert (await fetch(session, photo.id)).status == PhotoStatus.EXPIRED
