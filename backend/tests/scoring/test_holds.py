"""Prize holds in scoring (docs/M5-DESIGN.md D79, D93, D94, D95; §8 "Holds, scoring and
finalization"). A hold (placed only by an admin confirming a case) keeps the deed's points but
takes it off boards and prize rank: ``reconcile_deed`` writes a reversal and a new award with
the same points and ``counts_for_prize_rank`` flipped. Holds are written directly here
(``hold_helpers``), as the review endpoints will."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent, emit
from app.modules.deeds import _self_logs
from app.modules.deeds import service as deeds
from app.modules.deeds.events import DeedVerified
from app.modules.deeds.models import Deed
from app.modules.identity.models import User
from app.modules.scoring import _badges, _finalize, _leaderboard, _reads
from app.modules.scoring._finalize import Outcome, finalize_month
from app.modules.scoring._ledger import reconcile_deed
from app.modules.scoring._limits import load_leaderboard
from app.modules.scoring.models import LedgerKind, LedgerReason, LedgerRole, PointsLedger
from app.modules.scoring.service import badges_for, final_snapshot, titles_for
from tests.deeds.helpers import Member, members, scan_and_report
from tests.fraud.helpers import run_fraud
from tests.scoring.board_helpers import city, counted, entries, ist, month
from tests.scoring.helpers import active, ledger, points, run_scoring
from tests.scoring.hold_helpers import hold, release

pytestmark = pytest.mark.usefixtures("city_board_on")

SEP, OCT = month(2026, 9), month(2026, 10)
SEP_CUTOFF = ist(2026, 10, 2, 1, 0)
AFTER = SEP_CUTOFF + timedelta(minutes=1)


async def _scored(
    session: AsyncSession, reporter: Member, doer: Member, day: int = 10
) -> uuid.UUID:
    out = await scan_and_report(session, reporter, doer, at=ist(2026, 9, day))
    assert out.status == "verified"
    await run_scoring(session, out.id)
    return out.id


async def _on_city_board(session: AsyncSession, user: Member) -> bool:
    _, config = await load_leaderboard(session)
    boards = await _leaderboard.compute_boards(session, SEP, config)
    city_id = await city(session)
    return city_id in boards and user.user_id in {r.user_id for r in boards[city_id][1]}


async def _featured(session: AsyncSession, period: date = SEP) -> set[uuid.UUID]:
    _, config = await load_leaderboard(session)
    rows = await _leaderboard.featured_city_rows(session, period, config.featured_cities[0], config)
    return {r.user_id for r in rows}


async def _in_bengaluru(session: AsyncSession, *people: Member) -> None:
    await session.execute(
        update(User).where(User.id.in_([p.user_id for p in people])).values(city="Bengaluru")
    )


async def _points_changed(session: AsyncSession, deed_id: uuid.UUID) -> list[dict[str, object]]:
    rows = await session.scalars(
        select(OutboxEvent)
        .where(
            OutboxEvent.event_type == "scoring.points_changed",
            OutboxEvent.payload["deed_id"].astext == str(deed_id),
        )
        .order_by(OutboxEvent.occurred_at, OutboxEvent.id)
    )
    return [r.payload for r in rows]


# --------------------------------------------------- a hold flips prize rank only


async def test_hold_keeps_points_and_takes_the_deed_off_boards_and_featured_city(
    session: AsyncSession,
) -> None:
    reporter, doer, admin, other_reporter, control = await members(session, 5)
    await _in_bengaluru(session, doer, control)
    deed_id = await _scored(session, reporter, doer)
    control_deed = await _scored(session, other_reporter, control, day=11)
    doer_points = await points(session, deed_id, LedgerRole.DOER)
    reporter_rows = [r.id for r in await ledger(session, deed_id) if r.role == LedgerRole.REPORTER]
    assert await _on_city_board(session, doer)
    assert doer.user_id in await _featured(session)

    await hold(session, deed_id, admin)
    assert await run_scoring(session, deed_id) == 1  # rescore_held_deed

    # One test transaction gives every row the same created_at, so compare order-free.
    doer_rows = [r for r in await ledger(session, deed_id) if r.role == LedgerRole.DOER]
    assert sorted(
        (r.kind.value, r.counts_for_prize_rank, r.reason.value, r.points) for r in doer_rows
    ) == [
        ("award", False, "prize_rank_changed", doer_points),
        ("award", True, "verified", doer_points),
        ("reversal", True, "prize_rank_changed", -doer_points),
    ]
    assert await points(session, deed_id, LedgerRole.DOER) == doer_points
    # Reporter rows are untouched.
    assert [r.id for r in await ledger(session, deed_id) if r.role == LedgerRole.REPORTER] == (
        reporter_rows
    )
    # The PointsChanged for the flip is a zero delta that M6 must never notify (D95); the
    # first award's is notified as before. (One transaction: compare order-free.)
    changed = await _points_changed(session, deed_id)
    assert sorted((c["role"], c["delta"], c["reason"], c["notify"]) for c in changed) == sorted(
        [
            ("doer", doer_points, "verified", True),
            ("reporter", 2, "verified", True),
            ("doer", 0, "prize_rank_changed", False),
        ]
    )

    # The next board refresh drops the deed; Featured City (open month) drops it.
    version, config = await load_leaderboard(session)
    await _leaderboard.refresh_month(
        session, SEP, config=config, config_version=version, now=datetime.now(UTC)
    )
    snap = await _leaderboard.latest_snapshot(session, await city(session), SEP)
    assert snap is not None
    assert set(await entries(session, snap.id)) == {control.user_id}
    assert not await _on_city_board(session, doer)
    assert await _featured(session) == {control.user_id}

    # Profile Verified Deeds and badges are unchanged: the deed still counts as a deed.
    view = await deeds.get_deed(session, deed_id)
    assert view is not None and view.status == "verified"
    assert (await _self_logs.deed_counts(session, doer.handle)).verified == 1
    await _badges.award_badges(session, now=ist(2026, 9, 20))
    badges = await badges_for(session, [doer.user_id, control.user_id])
    assert badges[doer.user_id] == badges[control.user_id]
    assert control_deed != deed_id


async def test_release_puts_the_deed_back(session: AsyncSession) -> None:
    reporter, doer, admin = await members(session, 3)
    await _in_bengaluru(session, doer)
    deed_id = await _scored(session, reporter, doer)
    case_id = await hold(session, deed_id, admin)
    await run_scoring(session, deed_id)
    assert not await _on_city_board(session, doer)

    await release(session, deed_id, case_id)
    assert await run_scoring(session, deed_id) == 1
    entry = await active(session, deed_id, LedgerRole.DOER)
    assert entry is not None
    assert (entry.kind, entry.counts_for_prize_rank, entry.reason) == (
        LedgerKind.AWARD,
        True,
        LedgerReason.PRIZE_RANK_CHANGED,
    )
    assert await _on_city_board(session, doer)
    assert doer.user_id in await _featured(session)
    # Running the handler again (or another hold event) changes nothing.
    rows_before = len(await ledger(session, deed_id))
    assert await run_scoring(session, deed_id) == 0
    assert len(await ledger(session, deed_id)) == rows_before


async def test_a_deed_in_two_cases_stays_held_until_both_release(session: AsyncSession) -> None:
    reporter, doer, admin = await members(session, 3)
    deed_id = await _scored(session, reporter, doer)
    first = await hold(session, deed_id, admin)
    second = await hold(session, deed_id, admin)
    await run_scoring(session, deed_id)
    rows = len(await ledger(session, deed_id))
    entry = await active(session, deed_id, LedgerRole.DOER)
    assert entry is not None and not entry.counts_for_prize_rank

    await release(session, deed_id, first)
    await run_scoring(session, deed_id)
    assert len(await ledger(session, deed_id)) == rows  # still held by the second case
    assert not await _on_city_board(session, doer)

    await release(session, deed_id, second)
    await run_scoring(session, deed_id)
    entry = await active(session, deed_id, LedgerRole.DOER)
    assert entry is not None and entry.counts_for_prize_rank
    assert await _on_city_board(session, doer)


async def _set_eligible(session: AsyncSession, deed_id: uuid.UUID, eligible: bool) -> None:
    """Flip ``deeds.prize_rank_eligible`` (the M2 invite rule's flag) and re-score, as the
    ``DeedPrizeRankChanged`` handler does."""
    await session.execute(
        update(Deed).where(Deed.id == deed_id).values(prize_rank_eligible=eligible)
    )
    await reconcile_deed(session, deed_id)


async def test_prize_rank_needs_eligible_and_not_held(session: AsyncSession) -> None:
    """``counts_for_prize_rank = prize_rank_eligible AND NOT held``: a deed not eligible stays
    out while held and released; it is back only when eligible and released."""
    reporter, doer, admin = await members(session, 3)
    deed_id = await _scored(session, reporter, doer)
    await _set_eligible(session, deed_id, False)
    rows = len(await ledger(session, deed_id))

    case_id = await hold(session, deed_id, admin)
    await run_scoring(session, deed_id)
    assert len(await ledger(session, deed_id)) == rows  # not eligible + held: nothing to flip
    await _set_eligible(session, deed_id, True)
    assert len(await ledger(session, deed_id)) == rows  # eligible but still held
    entry = await active(session, deed_id, LedgerRole.DOER)
    assert entry is not None and not entry.counts_for_prize_rank

    await release(session, deed_id, case_id)
    await run_scoring(session, deed_id)
    entry = await active(session, deed_id, LedgerRole.DOER)
    assert entry is not None and entry.counts_for_prize_rank  # eligible + released


# --------------------------------------------------- owner views hide the flip (D95)


async def test_owner_ledger_and_deed_points_hide_the_hold_and_release(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, admin = await members(session, 3)
    deed_id = await _scored(session, reporter, doer)
    case_id = await hold(session, deed_id, admin)
    await run_scoring(session, deed_id)
    await session.commit()

    res = await client.get("/api/scoring/me/ledger", headers=doer.headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert [(i["kind"], i["reason"]) for i in items] == [("award", "verified")]
    mine = await client.get(f"/api/scoring/deeds/{deed_id}", headers=doer.headers)
    assert mine.status_code == 200
    assert (mine.json()["reason"], mine.json()["points"]) == (
        "verified",
        await points(session, deed_id, LedgerRole.DOER),
    )

    await release(session, deed_id, case_id)
    await run_scoring(session, deed_id)
    await session.commit()
    items = (await client.get("/api/scoring/me/ledger", headers=doer.headers)).json()["items"]
    assert [(i["kind"], i["reason"]) for i in items] == [("award", "verified")]
    # The admin's deed view still shows every row.
    assert len([r for r in await ledger(session, deed_id) if r.role == LedgerRole.DOER]) == 5


async def test_a_prize_rank_change_with_different_points_stays_in_the_ledger(
    session: AsyncSession,
) -> None:
    """Only equal-points pairs are hidden: a reversal and award with the reason but different
    points (never written by a hold, written here directly) are shown."""
    reporter, doer = await members(session, 2)
    deed_id = await counted(
        session, doer, reporter, region_id=await city(session), verified_at=ist(2026, 9, 3)
    )
    award = await session.scalar(select(PointsLedger).where(PointsLedger.deed_id == deed_id))
    assert award is not None
    for kind, pts, reverses in ((LedgerKind.REVERSAL, -10, award.id), (LedgerKind.AWARD, 12, None)):
        session.add(
            PointsLedger(
                id=uuid.uuid4(),
                user_id=doer.user_id,
                deed_id=deed_id,
                role=LedgerRole.DOER,
                kind=kind,
                points=pts,
                deed_units=-1 if kind == LedgerKind.REVERSAL else 1,
                counts_for_prize_rank=kind == LedgerKind.REVERSAL,
                region_id=award.region_id,
                reason=LedgerReason.PRIZE_RANK_CHANGED,
                breakdown={},
                counterpart_user_id=reporter.user_id,
                reverses_entry_id=reverses,
                verified_at=award.verified_at,
                earned_on=award.earned_on,
                period_month=award.period_month,
                scoring_config_version=1,
            )
        )
        await session.flush()
    page = await _reads.ledger_page(session, doer.user_id, role=None, cursor=None)
    assert len(page.items) == 3


# --------------------------------------------------- finalization barrier (D93)


async def test_barrier_waits_for_the_fraud_handler_on_deed_verified(
    session: AsyncSession,
) -> None:
    reporter, doer = await members(session, 2)
    deed_id = await counted(
        session, doer, reporter, region_id=await city(session), verified_at=ist(2026, 9, 3)
    )
    row = emit(
        session,
        DeedVerified(
            deed_id=deed_id,
            reporter_id=reporter.user_id,
            doer_id=doer.user_id,
            method="qr_scan",
            verified_at=ist(2026, 9, 3),
            same_act_group_id=uuid.uuid4(),
            counts_for_points=True,
            prize_rank_eligible=True,
            limits_version=1,
        ),
    )
    row.occurred_at = SEP_CUTOFF - timedelta(minutes=5)
    await session.flush()

    await run_scoring(session, deed_id)  # scoring done, fraud not yet
    waiting = await finalize_month(session, SEP, now=AFTER)
    assert waiting.outcome == Outcome.EVENTS_PENDING
    status = await _reads.admin_month(session, "2026-09")
    assert (
        status.pending_scoring_events,
        status.pending_fraud_events,
        status.pending_hold_events,
    ) == (0, 1, 0)

    assert await run_fraud(session, deed_id) == 1
    assert (await finalize_month(session, SEP, now=AFTER)).outcome == Outcome.FINALIZED


async def test_barrier_waits_for_an_unscored_hold_from_after_the_cutoff(
    session: AsyncSession,
) -> None:
    reporter, doer, admin, other_reporter, other = await members(session, 5)
    deed_id = await _scored(session, reporter, doer)
    await _scored(session, other_reporter, other, day=11)
    await hold(session, deed_id, admin)  # occurred now: long after the cutoff

    waiting = await finalize_month(session, SEP, now=AFTER)
    assert (waiting.outcome, waiting.pending_events) == (Outcome.EVENTS_PENDING, 1)
    status = await _reads.admin_month(session, "2026-09")
    assert (
        status.pending_scoring_events,
        status.pending_fraud_events,
        status.pending_hold_events,
    ) == (0, 0, 1)

    await run_scoring(session, deed_id)
    assert (await _finalize.barrier(session, SEP, SEP_CUTOFF)).total == 0
    assert (await finalize_month(session, SEP, now=AFTER)).outcome == Outcome.FINALIZED
    final = await final_snapshot(session, await city(session), SEP)
    assert final is not None
    assert {e.user_id for e in final.entries} == {other.user_id}


async def test_hold_events_from_before_the_month_do_not_block_it(session: AsyncSession) -> None:
    reporter, doer, admin = await members(session, 3)
    deed_id = await counted(
        session, doer, reporter, region_id=await city(session), verified_at=ist(2026, 9, 3)
    )
    await hold(session, deed_id, admin)
    event = await session.scalar(
        select(OutboxEvent).where(OutboxEvent.event_type == "fraud.deed_hold_changed")
    )
    assert event is not None
    event.occurred_at = ist(2026, 8, 31, 23)
    await session.flush()
    assert (await _finalize.barrier(session, SEP, SEP_CUTOFF)).holds == 0
    assert (await _finalize.barrier(session, month(2026, 8), SEP_CUTOFF)).holds == 1


# --------------------------------------------------- after final (D93 d, D94)


async def test_a_hold_after_finalization_changes_the_ledger_not_the_final_board(
    session: AsyncSession,
) -> None:
    reporter, doer, admin, other_reporter, other = await members(session, 5)
    await _in_bengaluru(session, doer, other)
    deed_id = await _scored(session, reporter, doer)
    await _scored(session, other_reporter, other, day=11)
    # Finalize at the real time, so every row written so far is at or before ``finalized_at``.
    done = await finalize_month(session, SEP, now=datetime.now(UTC))
    assert done.outcome == Outcome.FINALIZED
    city_id = await city(session)
    final = await final_snapshot(session, city_id, SEP)
    assert final is not None
    assert {e.user_id for e in final.entries} == {doer.user_id, other.user_id}
    assert await _featured(session) == {doer.user_id, other.user_id}

    await hold(session, deed_id, admin)
    assert await run_scoring(session, deed_id) == 1
    flip = [
        r for r in await ledger(session, deed_id) if r.reason == LedgerReason.PRIZE_RANK_CHANGED
    ]
    assert len(flip) == 2 and all(r.period_month == SEP for r in flip)

    # Final boards and titles never move; refreshes skip a finalized month.
    titles = await titles_for(session, [doer.user_id, other.user_id])
    assert SEP not in await _leaderboard.refresh_due(session, now=datetime.now(UTC))
    again = await final_snapshot(session, city_id, SEP)
    assert again is not None and again.entries == final.entries
    latest = await _leaderboard.latest_snapshot(session, city_id, SEP)
    assert latest is not None and latest.id == final.id
    assert await titles_for(session, [doer.user_id, other.user_id]) == titles


def _late_reversal(award: PointsLedger, created_at: datetime) -> PointsLedger:
    """The reversal half of a hold's flip, as a later transaction would write it."""
    return PointsLedger(
        id=uuid.uuid4(),
        user_id=award.user_id,
        deed_id=award.deed_id,
        role=LedgerRole.DOER,
        kind=LedgerKind.REVERSAL,
        points=-award.points,
        deed_units=-award.deed_units,
        counts_for_prize_rank=award.counts_for_prize_rank,
        region_id=award.region_id,
        reason=LedgerReason.PRIZE_RANK_CHANGED,
        breakdown={},
        counterpart_user_id=award.counterpart_user_id,
        reverses_entry_id=award.id,
        verified_at=award.verified_at,
        earned_on=award.earned_on,
        period_month=award.period_month,
        scoring_config_version=1,
        created_at=created_at,
    )


async def test_featured_city_reads_a_final_month_as_of_its_finalization(
    session: AsyncSession,
) -> None:
    """D94: a ledger row created after ``finalized_at`` (a later transaction's hold, override or
    rejection, dated in the final month) doesn't move the final month's Featured City. The rows
    are written with an explicit ``created_at``: inside one test transaction every row gets the
    same ``now()``."""
    reporter, doer, other = await members(session, 3)
    await _in_bengaluru(session, doer, other)
    city_id = await city(session)
    deed_id = await counted(session, doer, reporter, region_id=city_id, verified_at=ist(2026, 9, 3))
    await counted(session, other, reporter, region_id=city_id, verified_at=ist(2026, 9, 4))
    finalized_at = datetime.now(UTC)
    assert (await finalize_month(session, SEP, now=finalized_at)).outcome == Outcome.FINALIZED

    award = await session.scalar(select(PointsLedger).where(PointsLedger.deed_id == deed_id))
    assert award is not None
    session.add(_late_reversal(award, finalized_at + timedelta(hours=1)))
    await session.flush()
    assert await _featured(session) == {doer.user_id, other.user_id}

    # The same late row in an open month moves it (open months stay live).
    oct_deed = await counted(
        session, doer, reporter, region_id=city_id, verified_at=ist(2026, 10, 3)
    )
    await counted(session, other, reporter, region_id=city_id, verified_at=ist(2026, 10, 4))
    oct_award = await session.scalar(select(PointsLedger).where(PointsLedger.deed_id == oct_deed))
    assert oct_award is not None
    session.add(_late_reversal(oct_award, finalized_at + timedelta(hours=1)))
    await session.flush()
    assert await _featured(session, OCT) == {other.user_id}
