"""Shared helpers for media tests (typed, so mypy strict covers them)."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import publish
from app.core.storage import MemoryStorage
from app.modules.deeds.models import SelfLog
from app.modules.media import _keys, _worker
from app.modules.media._limits import MediaLimits, load_limits
from app.modules.media.models import Photo, PhotoPurpose, PhotoStatus, PhotoType
from tests.deeds.helpers import Member
from tests.media import images

OLDER_THAN_ANY_REAL_ROW = timedelta(days=365 * 30)

CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


async def publish_media(session: AsyncSession, **changes: Any) -> MediaLimits:
    _, current = await load_limits(session)
    new = current.model_copy(update=changes)
    await publish(session, new, actor_id=None, reason="test")
    await session.commit()
    return new


async def limits(session: AsyncSession) -> MediaLimits:
    return (await load_limits(session))[1]


async def uploaded_photo(
    session: AsyncSession,
    storage: MemoryStorage,
    owner: Member,
    *,
    data: bytes | None = None,
    content_type: str = "image/jpeg",
    purpose: PhotoPurpose = PhotoPurpose.DEED,
    status: PhotoStatus = PhotoStatus.UPLOADED,
) -> Photo:
    """A photo row in ``uploaded`` with its raw bytes in staging (what complete leaves)."""
    data = data if data is not None else images.to_bytes(images.noise((400, 300)), "JPEG")
    photo_id = uuid.uuid4()
    photo = Photo(
        id=photo_id,
        uploader_user_id=owner.user_id,
        purpose=purpose,
        status=status,
        declared_type=PhotoType(content_type),
        declared_bytes=len(data),
        staging_key=_keys.staging_key(photo_id),
        uploaded_at=datetime.now(UTC),
    )
    session.add(photo)
    await session.flush()
    await storage.put_bytes(photo.staging_key, data, content_type, "private")
    await session.commit()
    return _detached(session, photo)


def _detached(session: AsyncSession, photo: Photo) -> Photo:
    """Detach the returned row, so a later rollback (a failed API request) can't expire it;
    tests read fresh state with ``fetch``."""
    session.expunge(photo)
    return photo


async def queued_photo(
    session: AsyncSession,
    storage: MemoryStorage,
    owner: Member,
    *,
    deed_id: uuid.UUID | None = None,
    self_log_id: uuid.UUID | None = None,
    position: int = 1,
    regions: list[dict[str, float]] | None = None,
    data: bytes | None = None,
    content_type: str = "image/jpeg",
) -> Photo:
    purpose = PhotoPurpose.SELF_LOG if self_log_id else PhotoPurpose.DEED
    photo = await uploaded_photo(
        session, storage, owner, data=data, content_type=content_type, purpose=purpose
    )
    photo = await fetch(session, photo.id)
    photo.deed_id = deed_id
    photo.self_log_id = self_log_id
    photo.position = position
    photo.blur_regions = regions or []
    # Decades in the past (keeping creation order), so this test's rows are claimed before any
    # queued row another test run may have left committed in the test database.
    photo.attached_at = datetime.now(UTC) - OLDER_THAN_ANY_REAL_ROW
    photo.status = PhotoStatus.QUEUED
    await session.commit()
    return _detached(session, photo)


async def ready_photo(
    session: AsyncSession,
    storage: MemoryStorage,
    owner: Member,
    *,
    deed_id: uuid.UUID | None = None,
    self_log_id: uuid.UUID | None = None,
    position: int = 1,
    data: bytes | None = None,
) -> Photo:
    """Attach and run the real worker, so the photo is ``ready`` with real variants."""
    photo = await queued_photo(
        session,
        storage,
        owner,
        deed_id=deed_id,
        self_log_id=self_log_id,
        position=position,
        data=data,
    )
    await process_all(session, storage)
    photo = await fetch(session, photo.id)
    assert photo.status == PhotoStatus.READY, photo.reject_reason
    return _detached(session, photo)


async def process_all(session: AsyncSession, storage: MemoryStorage) -> None:
    while await _worker.process_batch(session, storage) == _worker.CLAIM_BATCH:
        pass


async def drain(work: Callable[[], Awaitable[int]], batch: int) -> None:
    """Run a cleanup batch until one comes back short. Tests assert on their own rows, never on
    counts: rows other test runs left committed may be in the same table."""
    while await work() >= batch:
        pass


async def self_log(session: AsyncSession, owner: Member) -> uuid.UUID:
    log = SelfLog(
        user_id=owner.user_id,
        category="animals",
        description="Fed the street dogs",
        occurred_at=datetime.now(UTC),
    )
    session.add(log)
    await session.commit()
    return log.id


async def fetch(session: AsyncSession, photo_id: uuid.UUID) -> Photo:
    photo = await session.scalar(
        select(Photo).where(Photo.id == photo_id).execution_options(populate_existing=True)
    )
    assert photo is not None
    return photo
