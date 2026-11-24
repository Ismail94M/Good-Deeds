"""Daily caps in strict verification order (docs/M4-DESIGN.md D52, PO QA3, 2026-11-22).

The first ``cap`` deeds of a user's IST day by ``verified_at`` (ties by deed id) earn points,
whatever order the worker scores them in. When a deed takes a place ahead of one that already
has points, the later deed's award is reversed (reason ``*_daily_cap``) in the same transaction,
and the earlier deed is awarded with its own region.

"An earlier verification shows up late" is simulated by moving a new deed's ``verified_at``
before the others, as when a verification commits after a later one has been scored.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import service as deeds
from app.modules.deeds.models import Deed
from app.modules.scoring._ledger import reconcile_deed
from app.modules.scoring.models import LedgerKind, LedgerReason, LedgerRole
from tests.deeds.helpers import Member, members, scan_and_report
from tests.deeds.invite_helpers import withdraw
from tests.scoring.helpers import active, ledger, points, run_scoring, user_points

DOER, REPORTER = LedgerRole.DOER, LedgerRole.REPORTER
# 10 November 2026 00:10 IST.
START = datetime(2026, 11, 9, 18, 40, tzinfo=UTC)
# Two hours apart, so each report is its own act (same_act_window_minutes = 120).
GAP = timedelta(minutes=125)


async def _deeds(
    session: AsyncSession, doer: Member, reporters: list[Member], *, gap: timedelta = GAP
) -> list[uuid.UUID]:
    return [
        (await scan_and_report(session, r, doer, at=START + i * gap)).id
        for i, r in enumerate(reporters)
    ]


async def _set_verified_at(session: AsyncSession, deed_id: uuid.UUID, at: datetime) -> None:
    await session.execute(update(Deed).where(Deed.id == deed_id).values(verified_at=at))
    await session.flush()


async def _kind(session: AsyncSession, deed_id: uuid.UUID, role: LedgerRole) -> LedgerKind:
    row = await active(session, deed_id, role)
    assert row is not None
    return row.kind


async def _awards_that_day(session: AsyncSession, ids: list[uuid.UUID], role: LedgerRole) -> int:
    return sum([await _kind(session, d, role) == LedgerKind.AWARD for d in ids])


async def test_scoring_order_does_not_matter(session: AsyncSession) -> None:
    """Ahmed has 12 deeds on one day; the worker scores them newest first."""
    doer, *reporters = await members(session, 13)
    ids = await _deeds(session, doer, reporters)
    for deed_id in reversed(ids):
        await run_scoring(session, deed_id)

    assert [await _kind(session, d, DOER) for d in ids] == [LedgerKind.AWARD] * 10 + [
        LedgerKind.DECISION
    ] * 2
    # No award was ever given and taken back: the order is read from the deeds.
    rows = [r for d in ids for r in await ledger(session, d)]
    assert all(r.kind != LedgerKind.REVERSAL for r in rows)
    assert await user_points(session, doer.user_id, DOER) == 100


async def test_an_earlier_verification_seen_late_takes_the_place_of_the_latest(
    session: AsyncSession,
) -> None:
    doer, *reporters = await members(session, 12)
    ids = await _deeds(session, doer, reporters[:10])
    await run_scoring(session, *ids)
    assert await _awards_that_day(session, ids, DOER) == 10
    tenth = await active(session, ids[9], DOER)
    assert tenth is not None

    # An 11th deed whose verification is earlier than all of them (committed late).
    late = (await scan_and_report(session, reporters[10], doer, at=START + 10 * GAP)).id
    await _set_verified_at(session, late, START - timedelta(minutes=5))
    written = await reconcile_deed(session, late)

    # The late deed gets points; the 10th by time is bumped, with the reversal in the ledger.
    assert await _kind(session, late, DOER) == LedgerKind.AWARD
    bumped = await active(session, ids[9], DOER)
    assert bumped is not None and bumped.kind == LedgerKind.DECISION
    assert bumped.reason == LedgerReason.DOER_DAILY_CAP
    reversal = next(
        r for r in await ledger(session, ids[9]) if r.kind == LedgerKind.REVERSAL and r.role == DOER
    )
    assert (reversal.reverses_entry_id, reversal.points, reversal.reason) == (
        tenth.id,
        -tenth.points,
        LedgerReason.DOER_DAILY_CAP,
    )
    # Ward credit: the reversal takes points back from the bumped deed's region, and the new
    # award goes to the late deed's own region.
    assert reversal.region_id == tenth.region_id
    late_award = await active(session, late, DOER)
    late_region = await deeds.deed_region(session, late)
    assert late_award is not None and late_region is not None
    assert late_award.region_id == late_region.id
    assert {r.deed_id for r in written} == {late, ids[9]}

    # Never more than 10 counted deeds, and the doer's total is unchanged.
    assert await _awards_that_day(session, [late, *ids], DOER) == 10
    assert await user_points(session, doer.user_id, DOER) == 100
    # The doer cap never touches the reporters.
    assert await points(session, ids[9], REPORTER) == 2


async def test_settling_is_idempotent(session: AsyncSession) -> None:
    doer, *reporters = await members(session, 12)
    ids = await _deeds(session, doer, reporters[:10])
    await run_scoring(session, *ids)
    late = (await scan_and_report(session, reporters[10], doer, at=START + 10 * GAP)).id
    await _set_verified_at(session, late, START - timedelta(minutes=5))
    await reconcile_deed(session, late)

    before = [len(await ledger(session, d)) for d in [late, *ids]]
    for deed_id in [late, *ids]:
        assert await reconcile_deed(session, deed_id) == []
    await run_scoring(session, late, *ids)
    assert [len(await ledger(session, d)) for d in [late, *ids]] == before


async def test_identical_verification_times_are_ordered_by_deed_id(session: AsyncSession) -> None:
    doer, *reporters = await members(session, 12)
    ids = await _deeds(session, doer, reporters[:11])
    same = START + 9 * GAP
    await _set_verified_at(session, ids[9], same)
    await _set_verified_at(session, ids[10], same)
    # Score the two tied deeds in both orders on two runs: the lower id always wins.
    await run_scoring(session, *ids[:9], ids[10], ids[9])
    winner, loser = sorted([ids[9], ids[10]])
    assert await _kind(session, winner, DOER) == LedgerKind.AWARD
    assert await _kind(session, loser, DOER) == LedgerKind.DECISION
    assert await _awards_that_day(session, ids, DOER) == 10


async def test_a_withdrawal_keeps_its_place(session: AsyncSession) -> None:
    """D52 is unchanged: withdrawing one of the first ten never moves the 11th up."""
    doer, *reporters = await members(session, 12)
    ids = await _deeds(session, doer, reporters[:11])
    await run_scoring(session, *ids)
    await withdraw(session, reporters[3], ids[3], at=START + 3 * GAP + timedelta(hours=1))
    await run_scoring(session, *ids)
    assert await points(session, ids[3], DOER) == 0
    assert await _kind(session, ids[10], DOER) == LedgerKind.DECISION
    assert await user_points(session, doer.user_id, DOER) == 90


async def test_reporter_cap_is_strict_too(session: AsyncSession) -> None:
    reporter, *doers = await members(session, 12)
    ids = [
        (await scan_and_report(session, reporter, d, at=START + i * timedelta(minutes=5))).id
        for i, d in enumerate(doers[:10])
    ]
    await run_scoring(session, *ids)
    late = (
        await scan_and_report(session, reporter, doers[10], at=START + 10 * timedelta(minutes=5))
    ).id
    await _set_verified_at(session, late, START - timedelta(minutes=1))
    await reconcile_deed(session, late)

    assert await _kind(session, late, REPORTER) == LedgerKind.AWARD
    bumped = await active(session, ids[9], REPORTER)
    assert bumped is not None
    assert (bumped.kind, bumped.reason) == (LedgerKind.DECISION, LedgerReason.REPORTER_DAILY_CAP)
    assert await _awards_that_day(session, [late, *ids], REPORTER) == 10
    # The reporter cap never touches the doers.
    assert await points(session, ids[9], DOER) == 10
