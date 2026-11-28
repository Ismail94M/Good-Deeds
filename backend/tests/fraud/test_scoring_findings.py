"""Cases from scoring's events (docs/M5-DESIGN.md D85, D86; §8 "Detectors": concentration and
content). Scoring emits ``ReporterConcentrationExceeded`` and ``DeedNeedsReview``; fraud's
handlers turn them into ``concentration`` and ``content`` cases through ``raise_flag``. Opening
a case holds nothing (PO principle 1)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent, emit, handlers_for, run_handler
from app.modules.deeds import _edits
from app.modules.deeds._limits import load_limits
from app.modules.deeds.schemas import DeedEdit
from app.modules.fraud.models import FlagCaseStatus, FlagCaseType, FlagCaseUser, PrizeHold
from app.modules.fraud.service import DeedHoldChanged
from app.modules.media.service import DeedPhotoReady
from app.modules.scoring._concentration import check_concentration
from app.modules.scoring.models import ImpactEvaluation
from app.modules.scoring.service import DeedNeedsReview
from tests.deeds.helpers import OUTBOX_WRITE_ORDER, Member, members, scan_and_report
from tests.fraud.helpers import FRAUD_PREFIX, case_deeds, cases_for, publish_fraud, run_fraud
from tests.scoring.board_helpers import city, counted, ist
from tests.scoring.helpers import run_scoring

SPAM = "Helped someone today, follow me for more"


async def _run_fraud_on(session: AsyncSession, event_type: str) -> int:
    """Run fraud's handlers for every outbox event of ``event_type``."""
    rows = await session.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.event_type == event_type)
        .order_by(*OUTBOX_WRITE_ORDER)
    )
    ran = 0
    for row in list(rows):
        for name in handlers_for(row.event_type):
            if name.startswith(FRAUD_PREFIX) and await run_handler(session, row.id, name):
                ran += 1
    return ran


async def _count(session: AsyncSession, model: type, *where: object) -> int:
    return int(
        await session.scalar(select(func.count()).select_from(model).where(*where))  # type: ignore[arg-type]
        or 0
    )


async def _hold_events(session: AsyncSession) -> int:
    return await _count(session, OutboxEvent, OutboxEvent.event_type == DeedHoldChanged.event_type)


# --------------------------------------------------- concentration


async def _concentrated(session: AsyncSession) -> tuple[Member, Member, list[uuid.UUID]]:
    """A doer with 60 prize-rank points in September, 20 of them from Priya (33 %)."""
    doer, priya, *others = await members(session, 6)
    region = await city(session)
    deeds = []
    for i, reporter in enumerate([priya, priya, *others]):
        deeds.append(
            await counted(
                session, doer, reporter, region_id=region, verified_at=ist(2026, 9, 1 + i)
            )
        )
    return doer, priya, deeds[:2]


async def test_concentration_opens_one_case_for_doer_and_reporter_without_a_hold(
    session: AsyncSession,
) -> None:
    doer, priya, priya_deeds = await _concentrated(session)
    await check_concentration(session)
    assert await _run_fraud_on(session, "scoring.reporter_concentration_exceeded") >= 1

    (case,) = await cases_for(session, FlagCaseType.CONCENTRATION, doer, priya)
    assert case.status == FlagCaseStatus.OPEN
    assert case.signal == {"period_month": "2026-09", "share_bp": 3333, "doer_points": 60}
    assert await case_deeds(session, case.id) == set(priya_deeds)
    assert case.held_count == 0
    assert await _count(session, PrizeHold, PrizeHold.case_id == case.id) == 0
    assert await _hold_events(session) == 0

    # The job again: no new event, so no second case.
    await check_concentration(session)
    assert await _run_fraud_on(session, "scoring.reporter_concentration_exceeded") == 0
    assert len(await cases_for(session, FlagCaseType.CONCENTRATION, doer, priya)) == 1


async def test_concentration_case_switched_off_opens_nothing(session: AsyncSession) -> None:
    """Even an event already emitted opens nothing once the type is switched off."""
    doer, priya, _ = await _concentrated(session)
    await check_concentration(session)
    await publish_fraud(session, detectors_enabled={"concentration": False})
    await _run_fraud_on(session, "scoring.reporter_concentration_exceeded")
    assert await cases_for(session, FlagCaseType.CONCENTRATION, doer, priya) == []


# --------------------------------------------------- content (spam_or_empty)


async def _spam_deed(session: AsyncSession, reporter: Member, doer: Member) -> uuid.UUID:
    """A verified deed, then edited inside its window to text the rules flag as spam (real
    times: the revision is stamped by the database clock)."""
    at = datetime.now(UTC) - timedelta(hours=2)
    out = await scan_and_report(session, reporter, doer, at=at)
    await run_scoring(session, out.id)
    _, limits = await load_limits(session)
    await _edits.edit_deed(
        session,
        reporter.user_id,
        out.id,
        DeedEdit.model_validate({"description": SPAM}),
        limits=limits,
        now=datetime.now(UTC),
    )
    await run_scoring(session, out.id)
    return out.id


async def _needs_review(session: AsyncSession, deed_id: uuid.UUID) -> int:
    return await _count(
        session,
        OutboxEvent,
        OutboxEvent.event_type == DeedNeedsReview.event_type,
        OutboxEvent.payload["deed_id"].astext == str(deed_id),
    )


async def _needs_review_events(session: AsyncSession, deed_id: uuid.UUID) -> list[OutboxEvent]:
    rows = await session.scalars(
        select(OutboxEvent).where(
            OutboxEvent.event_type == DeedNeedsReview.event_type,
            OutboxEvent.payload["deed_id"].astext == str(deed_id),
        )
    )
    return list(rows)


async def case_users(session: AsyncSession, case_id: uuid.UUID) -> list[FlagCaseUser]:
    rows = await session.scalars(select(FlagCaseUser).where(FlagCaseUser.case_id == case_id))
    return list(rows)


async def test_spam_deed_opens_one_content_case_and_a_rescore_does_not_repeat(
    session: AsyncSession,
) -> None:
    reporter, doer = await members(session, 2)
    deed_id = await _spam_deed(session, reporter, doer)
    assert await _needs_review(session, deed_id) == 1
    await run_fraud(session, deed_id)

    # Subjects are the doer and the reporter (who wrote the text), not the doer alone.
    assert await cases_for(session, FlagCaseType.CONTENT, doer) == []
    (case,) = await cases_for(session, FlagCaseType.CONTENT, doer, reporter)
    assert {u.user_id for u in await case_users(session, case.id)} == {
        doer.user_id,
        reporter.user_id,
    }
    (event,) = await _needs_review_events(session, deed_id)
    assert (event.payload["doer_id"], event.payload["reporter_id"]) == (
        str(doer.user_id),
        str(reporter.user_id),
    )
    assert case.signal == {"text_check": "spam_or_empty"}
    assert await case_deeds(session, case.id) == {deed_id}
    assert case.held_count == 0 and await _hold_events(session) == 0

    # A re-score inside the window (a photo bonus) writes a new flagged evaluation, but the deed
    # was flagged before: no second event, no second case.
    emit(
        session,
        DeedPhotoReady(
            photo_id=uuid.uuid4(),
            deed_id=deed_id,
            uploader_id=reporter.user_id,
            attached_at=datetime.now(UTC) - timedelta(minutes=30),
            ready_at=datetime.now(UTC) - timedelta(minutes=20),
        ),
    )
    await session.flush()
    await run_scoring(session, deed_id)
    flagged = await _count(
        session,
        ImpactEvaluation,
        ImpactEvaluation.deed_id == deed_id,
        ImpactEvaluation.human_review_required.is_(True),
    )
    assert flagged == 2
    assert await _needs_review(session, deed_id) == 1
    await run_fraud(session, deed_id)
    assert len(await cases_for(session, FlagCaseType.CONTENT, doer, reporter)) == 1


async def test_an_ordinary_deed_needs_no_review(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    out = await scan_and_report(session, reporter, doer, at=ist(2026, 9, 10))
    await run_scoring(session, out.id)
    assert await _needs_review(session, out.id) == 0
    await run_fraud(session, out.id)
    assert await cases_for(session, FlagCaseType.CONTENT, doer, reporter) == []


async def test_content_switched_off_opens_nothing(session: AsyncSession) -> None:
    await publish_fraud(session, detectors_enabled={"content": False})
    reporter, doer = await members(session, 2)
    deed_id = await _spam_deed(session, reporter, doer)
    assert await _needs_review(session, deed_id) == 1  # scoring still flags it
    await run_fraud(session, deed_id)
    assert await cases_for(session, FlagCaseType.CONTENT, doer, reporter) == []
