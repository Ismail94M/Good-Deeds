"""Opening and joining cases, idempotency, dismissal, switches, and PO principle 1
(docs/M5-DESIGN.md §4.2, D81, D82, D98, §8 Detectors)."""

import uuid
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent, run_handler
from app.modules.deeds.models import Deed
from app.modules.fraud import service as fraud
from app.modules.fraud._detect import run_sweep
from app.modules.fraud.detectors import detector_by_name
from app.modules.fraud.detectors.base import SweepWindow
from app.modules.fraud.models import (
    DetectorRunStatus,
    DetectorRunTrigger,
    FlagCase,
    FlagCaseDeed,
    FlagCaseStatus,
    FlagCaseType,
    FlagCaseUser,
    FraudDetectorRun,
    PrizeHold,
)
from app.modules.scoring._leaderboard import compute_boards
from app.modules.scoring._limits import load_leaderboard
from app.modules.scoring.models import PointsLedger
from tests.deeds.helpers import Member, make_member, members
from tests.fraud.helpers import (
    DAY,
    FRAUD_PREFIX,
    case_deeds,
    cases_for,
    days_ago,
    deed,
    dismiss,
    park,
    publish_fraud,
    run_fraud,
)
from tests.scoring.helpers import run_scoring

RECIPROCAL = FlagCaseType.RECIPROCAL
IST = ZoneInfo("Asia/Kolkata")
HANDLER = FRAUD_PREFIX + "handlers.check_verified_deed"


async def _pair(session: AsyncSession) -> tuple[Member, Member, list[uuid.UUID]]:
    a, b = await members(session, 2)
    t0 = days_ago(10)
    made = [await deed(session, a, b, t0), await deed(session, b, a, t0 + DAY)]
    return a, b, made


async def _snapshot(session: AsyncSession) -> tuple[int, int, int, int]:
    """Counts of every fraud row a raise can write."""

    async def n(model: Any) -> int:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)

    return (await n(FlagCase), await n(FlagCaseUser), await n(FlagCaseDeed), await n(PrizeHold))


def _window() -> SweepWindow:
    return SweepWindow(since=days_ago(30), until=datetime.now(UTC))


async def _sweep(session: AsyncSession, name: str) -> FraudDetectorRun | None:
    detector = detector_by_name(name)
    assert detector is not None
    return await run_sweep(session, detector, _window(), trigger=DetectorRunTrigger.CLI)


async def _verified_event(session: AsyncSession, deed_id: uuid.UUID) -> OutboxEvent:
    row = await session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.event_type == "deeds.deed_verified",
            OutboxEvent.payload["deed_id"].astext == str(deed_id),
        )
    )
    assert row is not None
    return row


# --------------------------------------------------- idempotency


async def test_same_event_handled_twice_changes_nothing(session: AsyncSession) -> None:
    from app.modules.deeds.service import DeedVerified
    from app.modules.fraud.handlers import check_verified_deed

    a, b, made = await _pair(session)
    await run_fraud(session, *made)
    before = await _snapshot(session)
    raised = await session.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.event_type == "fraud.flag_raised")
    )
    # Through the outbox the second run is skipped...
    row = await _verified_event(session, made[1])
    assert await run_handler(session, row.id, HANDLER) is False
    # ...and running the handler body again (a retry after a lost marker) adds nothing either.
    await check_verified_deed(session, DeedVerified.model_validate(row.payload))
    assert await _snapshot(session) == before
    [case] = await cases_for(session, RECIPROCAL, a, b)
    assert case.deed_count == 2
    assert (
        await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.event_type == "fraud.flag_raised")
        )
        == raised
    )


async def test_sweep_after_handler_changes_nothing(session: AsyncSession) -> None:
    a, b, made = await _pair(session)
    await run_fraud(session, *made)
    [case] = await cases_for(session, RECIPROCAL, a, b)
    updated = case.updated_at
    for name in ("reciprocal", "ring"):
        run = await _sweep(session, name)
        assert run is not None and run.status == DetectorRunStatus.DONE
    [same] = await cases_for(session, RECIPROCAL, a, b)
    assert same.id == case.id and same.updated_at == updated
    assert await case_deeds(session, case.id) == set(made)


async def test_sweep_twice_changes_nothing(session: AsyncSession) -> None:
    a, b, made = await _pair(session)
    first = await _sweep(session, "reciprocal")
    assert first is not None and first.cases_opened >= 1 and first.deeds_added >= 2
    [case] = await cases_for(session, RECIPROCAL, a, b)
    assert await case_deeds(session, case.id) == set(made)
    before = await _snapshot(session)
    second = await _sweep(session, "reciprocal")
    assert second is not None
    assert (second.cases_opened, second.deeds_added) == (0, 0)
    assert second.edges_scanned >= 2
    assert await _snapshot(session) == before
    runs = await session.scalar(
        select(func.count())
        .select_from(FraudDetectorRun)
        .where(FraudDetectorRun.id.in_([first.id, second.id]))
    )
    assert runs == 2


# --------------------------------------------------- joining and dismissal


async def test_new_deed_joins_an_open_case_without_a_hold(session: AsyncSession) -> None:
    a, b, made = await _pair(session)
    await run_fraud(session, *made)
    [case] = await cases_for(session, RECIPROCAL, a, b)
    later = await deed(session, a, b, days_ago(2))
    await run_fraud(session, later)
    [same] = await cases_for(session, RECIPROCAL, a, b)
    assert same.id == case.id
    assert same.deed_count == 3 and same.held_count == 0
    assert not await fraud.is_held(session, later)


async def test_new_deed_joins_a_parked_case(session: AsyncSession) -> None:
    a, b, made = await _pair(session)
    await run_fraud(session, *made)
    [case] = await cases_for(session, RECIPROCAL, a, b)
    await park(session, case)
    later = await deed(session, b, a, days_ago(2))
    await run_fraud(session, later)
    [same] = await cases_for(session, RECIPROCAL, a, b)
    assert same.id == case.id and same.status == FlagCaseStatus.PARKED
    assert await case_deeds(session, case.id) == {*made, later}


async def test_after_dismissal_old_deeds_stay_cleared_and_a_new_deed_opens_a_new_case(
    session: AsyncSession,
) -> None:
    admin = await make_member(session)
    a, b, made = await _pair(session)
    await run_fraud(session, *made)
    [case] = await cases_for(session, RECIPROCAL, a, b)
    await dismiss(session, case, admin)

    # Re-checking the old deeds (sweep) raises nothing.
    await _sweep(session, "reciprocal")
    assert [c.id for c in await cases_for(session, RECIPROCAL, a, b)] == [case.id]

    # A new deed opens a new case holding only itself.
    later = await deed(session, a, b, days_ago(2))
    await run_fraud(session, later)
    both = await cases_for(session, RECIPROCAL, a, b)
    [old] = [c for c in both if c.id == case.id]
    [new] = [c for c in both if c.id != case.id]
    assert old.status == FlagCaseStatus.RESOLVED
    assert new.status == FlagCaseStatus.OPEN
    assert await case_deeds(session, new.id) == {later}
    assert await case_deeds(session, old.id) == set(made)


# --------------------------------------------------- switches


async def test_detectors_switched_off_open_nothing(session: AsyncSession) -> None:
    await publish_fraud(session, detectors_enabled={"reciprocal": False, "ring": False})
    people = await members(session, 3)
    t0 = days_ago(10)
    made = [
        await deed(session, people[0], people[1], t0),
        await deed(session, people[1], people[0], t0 + DAY),
        await deed(session, people[1], people[2], t0 + 2 * DAY),
        await deed(session, people[2], people[0], t0 + 3 * DAY),
    ]
    before = await _snapshot(session)
    await run_fraud(session, *made)
    assert await _sweep(session, "reciprocal") is None
    assert await _sweep(session, "ring") is None
    assert await _snapshot(session) == before


async def test_case_type_switched_off_opens_nothing(session: AsyncSession) -> None:
    # The detector may stay on while its case type is off: raise_flag checks the type too.
    from app.modules.fraud._cases import raise_flag
    from app.modules.fraud._limits import load_fraud
    from app.modules.fraud.detectors.base import Finding

    await publish_fraud(session, detectors_enabled={"content": False})
    version, config = await load_fraud(session)
    _, b, made = await _pair(session)
    finding = Finding(FlagCaseType.CONTENT, frozenset({b.user_id}), frozenset(made[:1]))
    assert await raise_flag(session, finding, config, config_version=version) is None
    assert await cases_for(session, FlagCaseType.CONTENT, b) == []


# --------------------------------------------------- PO principle 1


async def _ledger(session: AsyncSession, deed_ids: list[uuid.UUID]) -> list[tuple[Any, ...]]:
    rows = await session.scalars(
        select(PointsLedger)
        .where(PointsLedger.deed_id.in_(deed_ids))
        .order_by(PointsLedger.created_at, PointsLedger.id)
    )
    return [
        (r.id, r.user_id, r.role, r.kind, r.points, r.deed_units, r.counts_for_prize_rank)
        for r in rows
    ]


async def _boards(
    session: AsyncSession, months: set[date], users: set[uuid.UUID]
) -> list[tuple[Any, ...]]:
    _, config = await load_leaderboard(session)
    rows: list[tuple[Any, ...]] = []
    for month in sorted(months):
        for region_id, (_, board) in sorted((await compute_boards(session, month, config)).items()):
            rows.extend((month, region_id, r) for r in board if r.user_id in users)
    return rows


async def _deed_state(session: AsyncSession, deed_ids: list[uuid.UUID]) -> list[tuple[Any, ...]]:
    rows = await session.execute(
        select(Deed.id, Deed.status, Deed.counts_for_points, Deed.prize_rank_eligible)
        .where(Deed.id.in_(deed_ids))
        .order_by(Deed.id)
    )
    return [tuple(r) for r in rows]


async def test_opening_a_case_changes_nothing_for_anyone(session: AsyncSession) -> None:
    people = await members(session, 3)
    a, b, c = people
    t0 = days_ago(0.4)
    hour = DAY / 24
    made = [
        await deed(session, a, b, t0),
        await deed(session, b, a, t0 + 3 * hour),  # reciprocal {a, b}
        await deed(session, b, c, t0 + 6 * hour),
        await deed(session, c, a, t0 + 9 * hour),  # ring {a, b, c}
    ]
    await run_scoring(session, *made)
    users = {m.user_id for m in people}
    verified = await session.scalars(select(Deed.verified_at).where(Deed.id.in_(made)))
    months = {v.astimezone(IST).date().replace(day=1) for v in verified if v is not None}
    ledger, boards, state = (
        await _ledger(session, made),
        await _boards(session, months, users),
        await _deed_state(session, made),
    )
    assert ledger and boards
    events_before = set(await session.scalars(select(OutboxEvent.id)))

    await run_fraud(session, *made)

    assert len(await cases_for(session, RECIPROCAL, a, b)) == 1
    assert len(await cases_for(session, FlagCaseType.RING, a, b, c)) == 1
    assert await session.scalar(select(func.count()).select_from(PrizeHold)) == 0
    assert await fraud.held_deed_ids(session, made) == set()
    assert await _ledger(session, made) == ledger
    assert await _boards(session, months, users) == boards
    assert await _deed_state(session, made) == state
    new_events = await session.scalars(
        select(OutboxEvent.event_type).where(OutboxEvent.id.not_in(events_before))
    )
    # Only the admin-queue event; no hold change, no deed, feed or scoring event.
    assert set(new_events) == {"fraud.flag_raised"}
    # Scoring has nothing new to do.
    assert await run_scoring(session, *made) == 0
    assert await _ledger(session, made) == ledger
    assert await fraud.open_case_user_ids(session, users) == users
