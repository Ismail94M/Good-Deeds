"""QA5/QA6 (M3) interleavings for photo reports and removals (test plan §8 "Reports and admin",
"Media"; §3.3 #9, #10).

Committed for real in separate sessions; the fixture deletes everything afterwards. See
``tests/feed/test_qa_races.py`` for the pause-before-lock technique.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal, advisory_xact_lock
from app.core.errors import AppError
from app.modules.media import _owners, _reports
from app.modules.media._limits import load_limits
from app.modules.media.models import (
    Photo,
    PhotoPurpose,
    PhotoReport,
    PhotoReportCase,
    PhotoStatus,
    PhotoType,
    ReportCaseStatus,
)
from app.modules.media.schemas import ReportCreate
from tests.deeds.helpers import Member
from tests.feed.test_qa_races import Committed, PauseBeforeLock, cleanup


@pytest.fixture
async def committed(migrated_db: None) -> AsyncIterator[Committed]:
    tracker = Committed()
    yield tracker
    await cleanup(tracker)


async def _ready_photo(tracker: Committed) -> tuple[Member, Member, uuid.UUID, uuid.UUID]:
    """reporter (uploader), doer, deed id, photo id: a committed ``ready`` photo row (no objects
    are needed for these service calls)."""
    reporter, doer = await tracker.member(), await tracker.member()
    deed_id = await tracker.deed(reporter, doer)
    photo_id = uuid.uuid4()
    async with SessionLocal() as s:
        s.add(
            Photo(
                id=photo_id,
                uploader_user_id=reporter.user_id,
                purpose=PhotoPurpose.DEED,
                status=PhotoStatus.READY,
                declared_type=PhotoType.JPEG,
                declared_bytes=1000,
                staging_key=f"staging/{photo_id}",
                deed_id=deed_id,
                position=1,
                variant_keys={
                    "thumb": f"photos/{photo_id}/thumb-1.webp",
                    "display": f"photos/{photo_id}/display-1.webp",
                },
                width=1280,
                height=960,
            )
        )
        await s.commit()
    return reporter, doer, deed_id, photo_id


async def _open_cases(photo_id: uuid.UUID) -> list[PhotoReportCase]:
    async with SessionLocal() as s:
        return list(
            await s.scalars(
                select(PhotoReportCase).where(
                    PhotoReportCase.photo_id == photo_id,
                    PhotoReportCase.status == ReportCaseStatus.OPEN,
                )
            )
        )


async def test_concurrent_reports_of_one_photo_open_one_case(committed: Committed) -> None:
    """Members and visitors (two from one IP) report at once: one case, duplicates dropped,
    priority the most serious (Q44: child = 1)."""
    reporter, _, _, photo_id = await _ready_photo(committed)
    members = [await committed.member() for _ in range(3)]
    calls: list[tuple[uuid.UUID | None, str, str]] = [
        (members[0].user_id, "other", "198.51.100.1"),
        (members[1].user_id, "inappropriate", "198.51.100.1"),
        (members[2].user_id, "child_no_guardian_consent", "198.51.100.2"),
        (members[0].user_id, "private_details", "198.51.100.1"),  # same member again
        (None, "other", "203.0.113.9"),
        (None, "not_consented", "203.0.113.9"),  # same visitor IP again
    ]
    barrier = asyncio.Barrier(len(calls))

    async def report(member_id: uuid.UUID | None, reason: str, ip: str) -> None:
        async with SessionLocal() as s:
            _, limits = await load_limits(s)
            await barrier.wait()
            await _reports.file_report(
                s,
                photo_id,
                ReportCreate.model_validate({"reason": reason}),
                reporter_id=member_id,
                uploader_id=reporter.user_id,
                ip=ip,
                limits=limits,
            )
            await s.commit()

    async with asyncio.timeout(60):
        await asyncio.gather(*(report(*c) for c in calls))
    [case] = await _open_cases(photo_id)
    assert (case.report_count, case.priority) == (4, 1)
    async with SessionLocal() as s:
        rows = await s.scalar(
            select(func.count()).select_from(PhotoReport).where(PhotoReport.photo_id == photo_id)
        )
    assert rows == 4


@pytest.mark.parametrize("remover", ["uploader", "doer"])
async def test_report_racing_a_removal_never_leaves_an_open_case(
    committed: Committed, monkeypatch: pytest.MonkeyPatch, remover: str
) -> None:
    reporter, doer, _, photo_id = await _ready_photo(committed)
    member = await committed.member()
    pause = PauseBeforeLock(advisory_xact_lock)
    monkeypatch.setattr(_reports, "advisory_xact_lock", pause)
    outcome: dict[str, Any] = {}

    async def report(s: Any) -> None:
        _, limits = await load_limits(s)
        try:
            _, uploader_id = await _reports.reportable_photo(s, photo_id)
            await _reports.file_report(
                s,
                photo_id,
                ReportCreate.model_validate({"reason": "private_details"}),
                reporter_id=member.user_id,
                uploader_id=uploader_id,
                ip="198.51.100.3",
                limits=limits,
            )
            await s.commit()
            outcome["result"] = "accepted"
        except AppError as exc:
            await s.rollback()
            outcome["result"] = str(exc.code)

    who = reporter if remover == "uploader" else doer
    async with asyncio.timeout(60), SessionLocal() as a, SessionLocal() as b:
        pause.target = a
        task = asyncio.create_task(report(a))
        await pause.reached.wait()  # the report found the photo reportable
        await _owners.remove_photo(b, who.user_id, photo_id)
        await b.commit()
        pause.go.set()
        await task

    async with SessionLocal() as s:
        photo = await s.get(Photo, photo_id)
        assert photo is not None and photo.status == PhotoStatus.DELETED
    assert outcome["result"] in ("accepted", "photo_not_found")
    assert await _open_cases(photo_id) == []
