"""``DeedPhotoReady`` (docs/M4-DESIGN.md §5, D51): emitted once, in the worker's ``ready``
commit, for deed photos only; never for a self-log photo or a rejected file."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent
from app.core.storage import MemoryStorage
from app.modules.media import service as media
from app.modules.media.models import PhotoStatus
from tests.deeds.helpers import make_member, scan_and_report
from tests.media import images
from tests.media.helpers import fetch, process_all, queued_photo, ready_photo, self_log


async def _events_for(session: AsyncSession, photo_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(OutboxEvent.payload).where(
            OutboxEvent.event_type == media.DeedPhotoReady.event_type,
            OutboxEvent.payload["photo_id"].astext == str(photo_id),
        )
    )
    return list(rows)


async def test_deed_photo_ready_emits_one_event_with_ids_only(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, doer = await make_member(session), await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    await session.commit()

    photo = await ready_photo(session, storage, reporter, deed_id=deed.id)
    # A second worker pass finds nothing to do and emits nothing more.
    await process_all(session, storage)

    row = await fetch(session, photo.id)
    assert row.attached_at is not None
    events = await _events_for(session, photo.id)
    assert len(events) == 1
    assert set(events[0]) == {"photo_id", "deed_id", "uploader_id", "attached_at"}
    assert media.DeedPhotoReady.model_validate(events[0]) == media.DeedPhotoReady(
        photo_id=photo.id,
        deed_id=deed.id,
        uploader_id=reporter.user_id,
        attached_at=row.attached_at,
    )


async def test_self_log_photo_ready_emits_nothing(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    log_id = await self_log(session, owner)
    photo = await ready_photo(session, storage, owner, self_log_id=log_id)
    assert await _events_for(session, photo.id) == []


async def test_rejected_deed_photo_emits_nothing(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, doer = await make_member(session), await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    await session.commit()
    tiny = images.to_bytes(images.noise((100, 100)), "JPEG")
    photo = await queued_photo(session, storage, reporter, deed_id=deed.id, data=tiny)

    await process_all(session, storage)

    assert (await fetch(session, photo.id)).status == PhotoStatus.REJECTED
    assert await _events_for(session, photo.id) == []
