"""The photo worker against the database and in-memory storage (§4.4, §8 "Pipeline privacy",
Q29, Q31)."""

import hashlib
import io
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, engine
from app.core.storage import MemoryStorage, ObjectStorage
from app.modules.media import _keys, _worker
from app.modules.media._limits import load_limits
from app.modules.media.models import (
    Photo,
    PhotoPurpose,
    PhotoStatus,
    PhotoTakedown,
    PhotoType,
)
from tests.deeds.helpers import Member, make_member, scan_and_report
from tests.media import images
from tests.media.helpers import fetch, process_all, publish_media, queued_photo, self_log


async def _deed(session: AsyncSession) -> tuple[uuid.UUID, Member, Member]:
    reporter, doer = await make_member(session), await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    await session.commit()
    return deed.id, reporter, doer


async def test_success_stores_blurred_variants_and_deletes_staging(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    deed_id, reporter, _ = await _deed(session)
    source = images.noise((1600, 1200), seed=11)
    data = images.jpeg_with_metadata(source)
    photo = await queued_photo(
        session,
        storage,
        reporter,
        deed_id=deed_id,
        regions=[{"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}],
        data=data,
    )

    await process_all(session, storage)

    row = await fetch(session, photo.id)
    assert row.status == PhotoStatus.READY
    assert row.attempts == 1
    assert row.variant_keys == {
        "thumb": f"photos/{photo.id}/thumb-1.webp",
        "display": f"photos/{photo.id}/display-1.webp",
    }
    assert (row.width, row.height) == (1280, 960)
    assert row.processed_at is not None and row.staging_deleted_at is not None
    assert row.content_sha256 == hashlib.sha256(data).hexdigest()

    # Q31: only the blurred variants are left; no staging, original or held copy.
    assert sorted(storage.objects) == sorted(row.variant_keys.values())
    for key in row.variant_keys.values():
        obj = storage.objects[key]
        assert obj.content_type == "image/webp"
        assert obj.cache_control == "private, max-age=3600"
        for marker in images.FORBIDDEN_MARKERS:
            assert marker not in obj.data
    display = Image.open(io.BytesIO(storage.objects[row.variant_keys["display"]].data))
    inside = (480, 360, 800, 600)
    expected = source.resize((1280, 960), Image.Resampling.LANCZOS)
    assert images.mean_abs_diff(display.convert("RGB").crop(inside), expected.crop(inside)) > 30


async def test_rejection_records_the_reason_and_deletes_staging(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    log_id = await self_log(session, owner)
    tiny = images.to_bytes(images.noise((100, 100)), "JPEG")
    photo = await queued_photo(session, storage, owner, self_log_id=log_id, data=tiny)

    await process_all(session, storage)

    row = await fetch(session, photo.id)
    assert row.status == PhotoStatus.REJECTED
    assert row.reject_reason == "corrupt"
    assert row.variant_keys is None and row.content_sha256 is not None
    assert row.staging_deleted_at is not None
    assert storage.objects == {}


async def test_same_file_as_a_doer_removed_photo_is_rejected(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    deed_id, reporter, _ = await _deed(session)
    data = images.to_bytes(images.noise((400, 300), seed=21), "JPEG")
    first = await queued_photo(session, storage, reporter, deed_id=deed_id, data=data)
    await process_all(session, storage)
    removed = await fetch(session, first.id)
    removed.status = PhotoStatus.DELETED
    removed.takedown = PhotoTakedown.DOER_REMOVED
    removed.deleted_at = datetime.now(UTC)
    await session.commit()

    again = await queued_photo(session, storage, reporter, deed_id=deed_id, position=2, data=data)
    other = await queued_photo(
        session,
        storage,
        reporter,
        deed_id=deed_id,
        position=3,
        data=images.to_bytes(images.noise((400, 300), seed=22), "JPEG"),
    )
    await process_all(session, storage)
    assert (await fetch(session, again.id)).reject_reason == "removed_by_doer"
    assert (await fetch(session, again.id)).status == PhotoStatus.REJECTED
    assert (await fetch(session, other.id)).status == PhotoStatus.READY


async def test_same_file_on_another_deed_is_fine(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    deed_a, reporter, _ = await _deed(session)
    deed_b, reporter_b, _ = await _deed(session)
    data = images.to_bytes(images.noise((400, 300), seed=23), "JPEG")
    first = await queued_photo(session, storage, reporter, deed_id=deed_a, data=data)
    await process_all(session, storage)
    row = await fetch(session, first.id)
    row.status, row.takedown = PhotoStatus.DELETED, PhotoTakedown.DOER_REMOVED
    row.deleted_at = datetime.now(UTC)
    await session.commit()
    second = await queued_photo(session, storage, reporter_b, deed_id=deed_b, data=data)
    await process_all(session, storage)
    assert (await fetch(session, second.id)).status == PhotoStatus.READY


async def test_staging_object_over_the_limit_is_too_large(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    log_id = await self_log(session, owner)
    photo = await queued_photo(session, storage, owner, self_log_id=log_id)
    await publish_media(session, upload_max_bytes=100)
    await process_all(session, storage)
    row = await fetch(session, photo.id)
    assert (row.status, row.reject_reason) == (PhotoStatus.REJECTED, "too_large")
    assert storage.objects == {}


class _FlakyStorage(MemoryStorage):
    """Fails every variant upload."""

    async def put_bytes(self, key: str, data: bytes, content_type: str, cache_control: str) -> None:
        if key.startswith("photos/"):
            raise ConnectionError("storage down")
        await super().put_bytes(key, data, content_type, cache_control)


async def test_transient_errors_retry_then_fail_and_drop_staging(session: AsyncSession) -> None:
    storage = _FlakyStorage()
    owner = await make_member(session)
    log_id = await self_log(session, owner)
    photo = await queued_photo(session, storage, owner, self_log_id=log_id)
    _, limits = await load_limits(session)

    for attempt in range(1, limits.processing_max_attempts):
        await _worker.process_batch(session, storage)
        row = await fetch(session, photo.id)
        assert (row.status, row.attempts) == (PhotoStatus.QUEUED, attempt)
        assert photo.staging_key in storage.objects

    await _worker.process_batch(session, storage)
    row = await fetch(session, photo.id)
    assert row.status == PhotoStatus.FAILED
    assert row.attempts == limits.processing_max_attempts
    assert row.staging_deleted_at is not None
    assert storage.objects == {}  # no unblurred copy kept, no half-written variant


async def test_missing_staging_object_fails_at_once(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    log_id = await self_log(session, owner)
    photo = await queued_photo(session, storage, owner, self_log_id=log_id)
    await storage.delete(photo.staging_key)
    await process_all(session, storage)
    row = await fetch(session, photo.id)
    assert (row.status, row.attempts) == (PhotoStatus.FAILED, 1)


async def test_stuck_rows_go_back_to_queued_then_fail(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    log_id = await self_log(session, owner)
    photo = await queued_photo(session, storage, owner, self_log_id=log_id)
    _, limits = await load_limits(session)
    long_ago = datetime.now(UTC) - timedelta(minutes=limits.processing_stuck_minutes + 1)

    row = await fetch(session, photo.id)
    row.status, row.processing_started_at, row.attempts = PhotoStatus.PROCESSING, long_ago, 1
    await session.commit()
    failed = await _worker.recover_stuck(session, limits, datetime.now(UTC))
    await session.commit()
    assert failed == []
    assert (await fetch(session, photo.id)).status == PhotoStatus.QUEUED

    # A row that has used every attempt fails, and its staging object is deleted.
    row = await fetch(session, photo.id)
    row.status, row.processing_started_at = PhotoStatus.PROCESSING, long_ago
    row.attempts = limits.processing_max_attempts
    await session.commit()
    await _worker.process_batch(session, storage)
    row = await fetch(session, photo.id)
    assert row.status == PhotoStatus.FAILED
    assert row.staging_deleted_at is not None
    assert photo.staging_key not in storage.objects

    # A row processing for less than the limit is left alone.
    fresh = await queued_photo(session, storage, owner, self_log_id=log_id, position=2)
    row = await fetch(session, fresh.id)
    row.status, row.processing_started_at = PhotoStatus.PROCESSING, datetime.now(UTC)
    await session.commit()
    await _worker.process_batch(session, storage)
    assert (await fetch(session, fresh.id)).status == PhotoStatus.PROCESSING


async def test_photo_deleted_while_processing_leaves_no_objects(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    log_id = await self_log(session, owner)
    photo = await queued_photo(session, storage, owner, self_log_id=log_id)
    _, limits = await load_limits(session)
    now = datetime.now(UTC)
    [claimed] = await _worker.claim(session, now, batch=1)  # ours is oldest
    await session.commit()

    # The uploader deletes it in the meantime (and its staging object goes).
    row = await fetch(session, photo.id)
    row.status, row.deleted_at = PhotoStatus.DELETED, now
    await session.commit()
    data = storage.objects[photo.staging_key]

    late = MemoryStorage(objects={photo.staging_key: data})
    result = await _worker.process_one(session, late, limits, claimed)
    assert result == PhotoStatus.READY  # it did the work ...
    row = await fetch(session, photo.id)
    assert row.status == PhotoStatus.DELETED  # ... but never overwrote the deletion
    assert row.variant_keys is None
    assert not any(k.startswith("photos/") for k in late.objects)  # uploads removed again


async def test_claim_takes_queued_rows_oldest_first(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    log_id = await self_log(session, owner)
    photos = [
        await queued_photo(session, storage, owner, self_log_id=log_id, position=i)
        for i in range(1, 4)
    ]
    claimed = await _worker.claim(session, datetime.now(UTC), batch=50)
    # (Filtered: another test run may have committed queued rows of its own.)
    ours = [c for c in claimed if c.id in {p.id for p in photos}]
    assert [c.id for c in ours] == [p.id for p in photos]
    assert all(c.purpose == PhotoPurpose.SELF_LOG and c.attempts == 1 for c in ours)


# --------------------------------------------------- concurrency (committed data)


@pytest.fixture
async def committed_photos(migrated_db: None) -> AsyncIterator[list[uuid.UUID]]:
    """Five queued photos committed for real (two connections can't see uncommitted rows),
    deleted again afterwards with their uploader."""
    async with SessionLocal() as s:
        owner = await make_member(s, phone_history=False)
        ids: list[uuid.UUID] = []
        for _ in range(5):
            photo_id = uuid.uuid4()
            s.add(
                Photo(
                    id=photo_id,
                    uploader_user_id=owner.user_id,
                    purpose=PhotoPurpose.DEED,
                    status=PhotoStatus.QUEUED,
                    declared_type=PhotoType.JPEG,
                    declared_bytes=10,
                    staging_key=_keys.staging_key(photo_id),
                    # Older than any real row, so these are the first claimable ones.
                    attached_at=datetime(1990, 1, 1, tzinfo=UTC) + timedelta(seconds=len(ids)),
                )
            )
            ids.append(photo_id)
        await s.commit()
    yield ids
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM photos WHERE id = ANY(:ids)"), {"ids": ids})
        await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": owner.user_id})


async def test_two_workers_never_claim_the_same_photo(committed_photos: list[uuid.UUID]) -> None:
    """Session A claims and holds its row locks; session B, at the same time, gets only rows A
    didn't take. Both roll back, so no other test's committed rows are ever changed."""
    now = datetime.now(UTC)
    ours = set(committed_photos)
    async with SessionLocal() as a, SessionLocal() as b:
        first = await _worker.claim(a, now, batch=3)  # holds its row locks (not committed)
        second = await _worker.claim(b, now, batch=3)  # skips them
        await a.rollback()
        await b.rollback()
    first_ids, second_ids = {c.id for c in first}, {c.id for c in second}
    assert first_ids.isdisjoint(second_ids)  # never the same row, ours or anyone's
    # Our rows are the oldest: A takes three of them, B the other two (plus maybe one foreign).
    assert first_ids == set(committed_photos[:3])
    assert second_ids & ours == set(committed_photos[3:])


async def test_process_batch_uses_the_storage_it_is_given(session: AsyncSession) -> None:
    """Type check that the job's storage argument is the ObjectStorage protocol."""
    store: ObjectStorage = MemoryStorage()
    assert await _worker.process_batch(session, store) >= 0
