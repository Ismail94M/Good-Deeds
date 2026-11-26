"""Reciprocal detector (docs/M5-DESIGN.md D83, §8 Detectors)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.fraud.models import FlagCaseStatus, FlagCaseType, PrizeHold
from tests.deeds.helpers import members
from tests.deeds.invite_helpers import withdraw
from tests.fraud.helpers import (
    DAY,
    case_deeds,
    cases_for,
    days_ago,
    deed,
    publish_fraud,
    run_fraud,
)

RECIPROCAL = FlagCaseType.RECIPROCAL


async def test_back_report_29_days_later_opens_a_case_with_both_deeds(
    session: AsyncSession,
) -> None:
    a, b = await members(session, 2)
    t0 = days_ago(40)
    ab = await deed(session, a, b, t0)
    ba = await deed(session, b, a, t0 + 29 * DAY)
    await run_fraud(session, ab, ba)

    [case] = await cases_for(session, RECIPROCAL, a, b)
    assert case.status == FlagCaseStatus.OPEN
    assert await case_deeds(session, case.id) == {ab, ba}
    assert case.deed_count == 2 and case.held_count == 0
    assert set(case.signal) == {"a_to_b", "b_to_a", "first_gap_hours"}
    assert case.signal["a_to_b"] + case.signal["b_to_a"] == 2
    assert case.signal["first_gap_hours"] == 29 * 24
    # Nothing held (PO principle 1).
    assert await session.scalar(select(func.count()).select_from(PrizeHold)) == 0


async def test_back_report_31_days_later_opens_nothing(session: AsyncSession) -> None:
    a, b = await members(session, 2)
    t0 = days_ago(40)
    ab = await deed(session, a, b, t0)
    ba = await deed(session, b, a, t0 + 31 * DAY)
    await run_fraud(session, ab, ba)
    assert await cases_for(session, RECIPROCAL, a, b) == []


async def test_window_comes_from_config(session: AsyncSession) -> None:
    await publish_fraud(session, reciprocal={"window_days": 7})
    a, b, c, d = await members(session, 4)
    t0 = days_ago(20)
    near = [await deed(session, a, b, t0), await deed(session, b, a, t0 + 6 * DAY)]
    far = [await deed(session, c, d, t0), await deed(session, d, c, t0 + 8 * DAY)]
    await run_fraud(session, *near, *far)
    assert len(await cases_for(session, RECIPROCAL, a, b)) == 1
    assert await cases_for(session, RECIPROCAL, c, d) == []


async def test_min_deeds_each_way_from_a_published_config(session: AsyncSession) -> None:
    await publish_fraud(session, reciprocal={"min_deeds_each_way": 2})
    a, b = await members(session, 2)
    t0 = days_ago(10)
    first = [
        await deed(session, a, b, t0),
        await deed(session, b, a, t0 + DAY),
        await deed(session, a, b, t0 + 2 * DAY),
    ]
    await run_fraud(session, *first)
    assert await cases_for(session, RECIPROCAL, a, b) == []  # 2 one way, 1 the other

    second = await deed(session, b, a, t0 + 3 * DAY)
    await run_fraud(session, second)
    [case] = await cases_for(session, RECIPROCAL, a, b)
    assert await case_deeds(session, case.id) == {*first, second}


async def test_withdrawn_deed_does_not_count(session: AsyncSession) -> None:
    a, b = await members(session, 2)
    t0 = days_ago(0.5)
    ba = await deed(session, b, a, t0)
    await withdraw(session, b, ba, at=t0 + (DAY / 24))
    ab = await deed(session, a, b, t0 + DAY / 6)
    await run_fraud(session, ba, ab)
    assert await cases_for(session, RECIPROCAL, a, b) == []


async def test_handler_skips_a_deed_withdrawn_before_it_ran(session: AsyncSession) -> None:
    a, b = await members(session, 2)
    t0 = days_ago(0.5)
    ab = await deed(session, a, b, t0)
    ba = await deed(session, b, a, t0 + DAY / 12)
    await withdraw(session, b, ba, at=t0 + DAY / 6)
    await run_fraud(session, ab, ba)
    assert await cases_for(session, RECIPROCAL, a, b) == []
