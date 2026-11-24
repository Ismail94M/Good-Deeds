"""Two attaches racing for the last photo slot (§3.3 #5 step 2, §8 "Media"). Committed data in
separate sessions, as in tests/deeds/test_concurrency.py."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import func, select, text

from app.core.db import SessionLocal, advisory_xact_lock, engine
from app.core.errors import AppError, Conflict
from app.core.storage import MemoryStorage
from app.modules.media import _owners
from app.modules.media._limits import load_limits
from app.modules.media.models import Photo, PhotoStatus
from app.modules.media.schemas import AttachItem
from tests.deeds.helpers import scan_and_report
from tests.deeds.test_concurrency import Committed
from tests.deeds.test_concurrency import committed as committed
from tests.media.helpers import uploaded_photo


@pytest.fixture
async def media_committed(committed: Committed) -> AsyncIterator[Committed]:
    """Deletes the photos first (they reference the deeds the deeds fixture deletes)."""
    yield committed
    if committed.user_ids:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM photos WHERE uploader_user_id = ANY(:ids)"),
                {"ids": committed.user_ids},
            )


async def test_two_attaches_for_the_last_slot_one_wins(
    media_committed: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = MemoryStorage()
    reporter, doer = await media_committed.member(), await media_committed.member()
    async with SessionLocal() as s:
        deed = await scan_and_report(s, reporter, doer)
        await s.commit()
        _, limits = await load_limits(s)
        first = [(await uploaded_photo(s, storage, reporter)).id for _ in range(3)]
        await _owners.attach_to_deed(
            s,
            storage,
            reporter.user_id,
            deed.id,
            [AttachItem(photo_id=i) for i in first],
            limits=limits,
        )
        await s.commit()
        a = (await uploaded_photo(s, storage, reporter)).id
        b = (await uploaded_photo(s, storage, reporter)).id

    real = advisory_xact_lock
    barrier = asyncio.Barrier(2)

    async def meet_then_lock(*args: Any, **kwargs: Any) -> None:
        async with asyncio.timeout(10):
            await barrier.wait()
        await real(*args, **kwargs)

    monkeypatch.setattr(_owners, "advisory_xact_lock", meet_then_lock)

    async def attach(photo_id: uuid.UUID) -> object:
        async with SessionLocal() as s:
            try:
                out = await _owners.attach_to_deed(
                    s,
                    storage,
                    reporter.user_id,
                    deed.id,
                    [AttachItem(photo_id=photo_id)],
                    limits=limits,
                )
            except AppError as exc:
                await s.rollback()
                return exc
            await s.commit()
            return out

    results = await asyncio.gather(attach(a), attach(b))
    conflicts = [r for r in results if isinstance(r, Conflict)]
    assert len(conflicts) == 1 and conflicts[0].code == "too_many_photos"
    async with SessionLocal() as s:
        live = await s.scalar(
            select(func.count())
            .select_from(Photo)
            .where(Photo.deed_id == deed.id, Photo.status == PhotoStatus.QUEUED)
        )
    assert live == 4
