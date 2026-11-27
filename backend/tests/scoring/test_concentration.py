"""Reporter concentration (docs/M5-DESIGN.md D85, §4.4; §8 "Detectors": concentration).

``scoring.check_concentration`` signals a reporter whose share of a doer's prize-rank points in
an open month is over ``fraud.concentration.max_reporter_share_pct`` (seed 30 %) once the doer
has ``min_doer_points`` (seed 50). Ledger rows are written directly (``board_helpers.counted``,
10 points each) so the month and reporters are exact. The fraud side (the case it opens) is in
``tests/fraud/test_scoring_findings.py``."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent
from app.modules.scoring._concentration import check_concentration
from app.modules.scoring.models import ConcentrationSignal, MonthFinalization
from app.modules.scoring.service import ReporterConcentrationExceeded
from tests.deeds.helpers import Member, members
from tests.fraud.helpers import publish_fraud
from tests.scoring.board_helpers import city, counted, ist, month

SEP = month(2026, 9)


async def _deeds(
    session: AsyncSession, doer: Member, reporters: list[Member], *, points: int = 10
) -> list[uuid.UUID]:
    region = await city(session)
    return [
        await counted(
            session, doer, r, region_id=region, verified_at=ist(2026, 9, 1 + i), points=points
        )
        for i, r in enumerate(reporters)
    ]


async def _events(session: AsyncSession, doer: Member) -> list[OutboxEvent]:
    rows = await session.scalars(
        select(OutboxEvent).where(
            OutboxEvent.event_type == ReporterConcentrationExceeded.event_type,
            OutboxEvent.payload["doer_id"].astext == str(doer.user_id),
        )
    )
    return list(rows)


async def _signals(session: AsyncSession, doer: Member) -> list[ConcentrationSignal]:
    rows = await session.scalars(
        select(ConcentrationSignal).where(ConcentrationSignal.doer_id == doer.user_id)
    )
    return list(rows)


async def test_a_third_from_one_reporter_signals_once(session: AsyncSession) -> None:
    doer, priya, *others = await members(session, 6)
    priya_deeds = await _deeds(session, doer, [priya, priya])
    await _deeds(session, doer, others)  # 4 x 10 from four others: 60 points, Priya 20 (33 %)

    found = await check_concentration(session)
    mine = [s for s in found if s.doer_id == doer.user_id]
    assert [(s.reporter_id, s.period_month, s.share_bp, s.doer_points) for s in mine] == [
        (priya.user_id, SEP, 3333, 60)
    ]
    (event,) = await _events(session, doer)
    assert event.payload == {
        "doer_id": str(doer.user_id),
        "reporter_id": str(priya.user_id),
        "period_month": "2026-09-01",
        "share_bp": 3333,
        "doer_points": 60,
        "deed_ids": sorted(str(d) for d in priya_deeds),
    }
    (signal,) = await _signals(session, doer)
    assert (signal.reporter_id, signal.share_bp, signal.doer_points) == (priya.user_id, 3333, 60)

    # Re-running finds the signal already recorded: no second event.
    again = await check_concentration(session)
    assert [s for s in again if s.doer_id == doer.user_id] == []
    assert len(await _events(session, doer)) == 1


async def test_below_the_minimum_points_nothing(session: AsyncSession) -> None:
    doer, priya, a, b = await members(session, 4)
    await _deeds(session, doer, [priya, priya, a, b])  # 40 points, Priya 50 %
    await check_concentration(session)
    assert await _signals(session, doer) == []
    assert await _events(session, doer) == []


async def test_exactly_the_limit_is_not_over_it(session: AsyncSession) -> None:
    doer, priya, a, b = await members(session, 4)
    await _deeds(session, doer, [priya, a, b], points=15)
    await _deeds(session, doer, [a], points=5)
    # 50 points: Priya and b exactly 30 % (not over the limit), a 40 %.
    await check_concentration(session)
    assert {s.reporter_id for s in await _signals(session, doer)} == {a.user_id}


async def test_rows_off_prize_rank_do_not_count(session: AsyncSession) -> None:
    """Held (or otherwise not prize-rank) deeds drop out of the measure."""
    doer, priya, *others = await members(session, 6)
    region = await city(session)
    for day in (1, 2):
        await counted(
            session, doer, priya, region_id=region, verified_at=ist(2026, 9, day), prize=False
        )
    await _deeds(session, doer, others)  # 40 prize-rank points
    await check_concentration(session)
    assert await _signals(session, doer) == []


async def test_a_finalized_month_is_not_scanned(session: AsyncSession) -> None:
    doer, priya, *others = await members(session, 6)
    await _deeds(session, doer, [priya, priya, *others])
    session.add(
        MonthFinalization(
            period_month=SEP,
            cutoff_at=ist(2026, 10, 2, 1),
            finalized_at=datetime.now(UTC),
            config_version=1,
            snapshot_count=0,
            titles_awarded=0,
        )
    )
    await session.flush()
    await check_concentration(session)
    assert await _signals(session, doer) == []
    assert await _events(session, doer) == []


async def test_switched_off_in_the_fraud_config_nothing(session: AsyncSession) -> None:
    doer, priya, *others = await members(session, 6)
    await _deeds(session, doer, [priya, priya, *others])
    await publish_fraud(session, detectors_enabled={"concentration": False})
    assert await check_concentration(session) == []
    assert await _signals(session, doer) == []
    assert await _events(session, doer) == []
