"""Ring detector (docs/M5-DESIGN.md D84, §8 Detectors)."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.fraud._detect import run_sweep
from app.modules.fraud.detectors import detector_by_name
from app.modules.fraud.detectors.base import SweepWindow
from app.modules.fraud.models import (
    DetectorRunStatus,
    DetectorRunTrigger,
    FlagCaseType,
    PrizeHold,
)
from tests.deeds.helpers import Member, members
from tests.fraud.helpers import (
    DAY,
    case_deeds,
    cases_for,
    cases_touching,
    days_ago,
    deed,
    publish_fraud,
    run_fraud,
)

RING = FlagCaseType.RING


async def _cycle(
    session: AsyncSession, people: list[Member], order: list[int], start: datetime
) -> dict[int, uuid.UUID]:
    """Deeds people[i] -> people[i+1] (wrapping), created in ``order``, a day apart."""
    n = len(people)
    made: dict[int, uuid.UUID] = {}
    for step, i in enumerate(order):
        made[i] = await deed(session, people[i], people[(i + 1) % n], start + step * DAY)
    return made


@pytest.mark.parametrize("order", [[0, 1, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]])
async def test_three_cycle_opens_one_case_whichever_edge_closes_it(
    session: AsyncSession, order: list[int]
) -> None:
    people = await members(session, 3)
    made = await _cycle(session, people, order, days_ago(10))
    await run_fraud(session, *made.values())

    [case] = await cases_for(session, RING, *people)
    assert await case_deeds(session, case.id) == set(made.values())
    assert case.signal == {"size": 3, "internal_share_pct": 100, "edges": 3}
    assert case.held_count == 0
    assert await session.scalar(select(func.count()).select_from(PrizeHold)) == 0
    # No pair recognised each other both ways, so no reciprocal case.
    assert {c.type for c in await cases_touching(session, made.values())} == {RING}

    # A later deed on another edge of the same ring joins the same case.
    again = await deed(session, people[0], people[1], days_ago(1))
    await run_fraud(session, again)
    [same] = await cases_for(session, RING, *people)
    assert same.id == case.id
    assert await case_deeds(session, case.id) == {*made.values(), again}


async def _outsider_reports(
    session: AsyncSession, people: list[Member], n: int, start: datetime
) -> None:
    outsiders = await members(session, n)
    for k, outsider in enumerate(outsiders):
        await deed(session, outsider, people[k % len(people)], start + k * DAY)


async def test_internal_share_at_the_threshold_flags(session: AsyncSession) -> None:
    people = await members(session, 3)
    # 3 internal deeds + 3 from outsiders: exactly 50 %.
    await _outsider_reports(session, people, 3, days_ago(20))
    made = await _cycle(session, people, [0, 1, 2], days_ago(10))
    await run_fraud(session, *made.values())
    [case] = await cases_for(session, RING, *people)
    assert case.signal["internal_share_pct"] == 50
    # Only the cycle's deeds are in the case, not the outsiders'.
    assert await case_deeds(session, case.id) == set(made.values())


async def test_internal_share_below_the_threshold_does_not_flag(session: AsyncSession) -> None:
    people = await members(session, 3)
    # 3 internal of 7 received: 42 %.
    await _outsider_reports(session, people, 4, days_ago(20))
    made = await _cycle(session, people, [0, 1, 2], days_ago(10))
    await run_fraud(session, *made.values())
    assert await cases_for(session, RING, *people) == []


async def test_share_just_below_a_configured_threshold_does_not_flag(
    session: AsyncSession,
) -> None:
    await publish_fraud(session, ring={"min_internal_share_pct": 51})
    people = await members(session, 3)
    await _outsider_reports(session, people, 3, days_ago(20))  # 50 % < 51 %
    made = await _cycle(session, people, [0, 1, 2], days_ago(10))
    await run_fraud(session, *made.values())
    assert await cases_for(session, RING, *people) == []


async def test_six_cycle_is_above_max_size(session: AsyncSession) -> None:
    people = await members(session, 6)
    made = await _cycle(session, people, list(range(6)), days_ago(10))
    await run_fraud(session, *made.values())
    assert await cases_touching(session, made.values()) == []


async def test_six_cycle_flags_when_max_size_allows_it(session: AsyncSession) -> None:
    await publish_fraud(session, ring={"max_size": 6})
    people = await members(session, 6)
    made = await _cycle(session, people, list(range(6)), days_ago(10))
    await run_fraud(session, *made.values())
    [case] = await cases_for(session, RING, *people)
    assert case.signal["size"] == 6


async def test_cycle_outside_the_window_does_not_flag(session: AsyncSession) -> None:
    people = await members(session, 3)
    # First and last edges 32 days apart (window 30).
    made = await _cycle(session, people, [0], days_ago(45))
    made |= await _cycle(session, people, [1, 2], days_ago(14))
    await run_fraud(session, *made.values())
    assert await cases_for(session, RING, *people) == []


async def test_two_cycles_are_reciprocal_only(session: AsyncSession) -> None:
    a, b = await members(session, 2)
    made = [await deed(session, a, b, days_ago(5)), await deed(session, b, a, days_ago(4))]
    await run_fraud(session, *made)
    assert {c.type for c in await cases_touching(session, made)} == {FlagCaseType.RECIPROCAL}


async def test_truncated_search_flags_nothing_and_the_sweep_records_it(
    session: AsyncSession,
) -> None:
    # Closing edge C -> A: the search loads A's out-edge, B's out-edge, then C's: 3 > 2.
    await publish_fraud(session, ring={"max_search_edges": 2})
    people = await members(session, 3)
    made = await _cycle(session, people, [0, 1, 2], days_ago(10))
    await run_fraud(session, *made.values())
    assert await cases_for(session, RING, *people) == []

    ring = detector_by_name("ring")
    assert ring is not None
    run = await run_sweep(
        session,
        ring,
        SweepWindow(since=days_ago(11), until=datetime.now(UTC)),
        trigger=DetectorRunTrigger.CLI,
    )
    assert run is not None and run.status == DetectorRunStatus.TRUNCATED
    assert run.finished_at is not None and run.edges_scanned >= 3
    assert await cases_for(session, RING, *people) == []

    # With room to search, the same sweep finds it.
    await publish_fraud(session, ring={"max_search_edges": 5000})
    run = await run_sweep(
        session,
        ring,
        SweepWindow(since=days_ago(11), until=datetime.now(UTC)),
        trigger=DetectorRunTrigger.CLI,
    )
    assert run is not None and run.status == DetectorRunStatus.DONE
    [case] = await cases_for(session, RING, *people)
    assert await case_deeds(session, case.id) == set(made.values())
