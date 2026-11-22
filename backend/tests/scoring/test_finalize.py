"""Monthly finalization and Kindness Hero titles (docs/M4-DESIGN.md D64, D67, D70, §4.6, PO Q50,
Q54, Q58; §8 "Leaderboards, finalization, titles").

Months here are in the past (September 2026), so the only outbox events from before a cutoff
are the ones a test back-dates on purpose."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent, emit
from app.modules.deeds.events import DeedWithdrawn
from app.modules.scoring import _finalize, _leaderboard
from app.modules.scoring._finalize import Outcome, finalize_due, finalize_month
from app.modules.scoring.models import (
    HeroTitle,
    LeaderboardSnapshot,
    MonthFinalization,
    PointsLedger,
)
from tests.deeds.helpers import Member, members, scan_and_report
from tests.deeds.invite_helpers import withdraw
from tests.scoring.board_helpers import IST, city, counted, entries, ist, month, reverse, ward
from tests.scoring.helpers import run_scoring

SEP, OCT = month(2026, 9), month(2026, 10)
# September finalizes at 2 October 01:00 IST (month end + 24 h window + 60 min margin).
SEP_CUTOFF = ist(2026, 10, 2, 1, 0)
AFTER = SEP_CUTOFF + timedelta(minutes=1)


async def _titles(
    session: AsyncSession, period: date = SEP
) -> set[tuple[int, int, str, uuid.UUID]]:
    rows = await session.scalars(select(HeroTitle).where(HeroTitle.period_month == period))
    return {(t.region_id, t.place, t.title_key, t.user_id) for t in rows}


async def _events(session: AsyncSession, event_type: str) -> list[OutboxEvent]:
    return list(
        await session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == event_type))
    )


async def _deeds(
    session: AsyncSession, doer: Member, reporters: list[Member], region_id: int, start: int = 1
) -> None:
    """One counted deed per reporter entry, on consecutive September days."""
    for i, reporter in enumerate(reporters):
        await counted(
            session, doer, reporter, region_id=region_id, verified_at=ist(2026, 9, start + i)
        )


# --------------------------------------------------- the cutoff and the outbox barrier


async def test_cutoff_is_month_end_plus_window_plus_margin(session: AsyncSession) -> None:
    assert await _finalize.cutoff_for(session, SEP) == SEP_CUTOFF
    nov = await _finalize.cutoff_for(session, month(2026, 11))
    assert nov.astimezone(IST) == datetime(2026, 12, 2, 1, 0, tzinfo=IST)


async def test_not_finalized_before_the_cutoff(session: AsyncSession) -> None:
    a, r = await members(session, 2)
    await counted(session, a, r, region_id=await city(session), verified_at=ist(2026, 9, 3))

    early = await finalize_month(session, SEP, now=SEP_CUTOFF - timedelta(seconds=1))
    assert early.outcome == Outcome.TOO_EARLY
    assert await session.get(MonthFinalization, SEP) is None

    done = await finalize_month(session, SEP, now=SEP_CUTOFF)
    assert done.outcome == Outcome.FINALIZED


async def test_waits_while_a_scoring_event_from_before_the_cutoff_is_unprocessed(
    session: AsyncSession,
) -> None:
    a, r = await members(session, 2)
    deed_id = await counted(
        session, a, r, region_id=await city(session), verified_at=ist(2026, 9, 3)
    )
    row = emit(
        session,
        DeedWithdrawn(
            deed_id=deed_id,
            reporter_id=r.user_id,
            doer_id=a.user_id,
            previous_status="verified",
            same_act_group_id=None,
        ),
    )
    row.occurred_at = SEP_CUTOFF - timedelta(minutes=5)
    await session.flush()

    waiting = await finalize_month(session, SEP, now=AFTER)
    assert (waiting.outcome, waiting.pending_events) == (Outcome.EVENTS_PENDING, 1)
    assert await session.get(MonthFinalization, SEP) is None

    assert await run_scoring(session, deed_id) >= 1
    assert (await finalize_month(session, SEP, now=AFTER)).outcome == Outcome.FINALIZED


async def test_a_withdrawal_at_the_window_boundary_is_in_the_final_board(
    session: AsyncSession,
) -> None:
    """A deed verified at 23:30 IST on the last day, withdrawn 23 h later (inside its window,
    on the next month's first day): finalization waits for the withdrawal to be scored, so the
    final board never counts it."""
    reporter, doer, other_reporter, other = await members(session, 4)
    out = await scan_and_report(session, reporter, doer, at=ist(2026, 9, 30, 23, 30))
    kept = await scan_and_report(session, other_reporter, other, at=ist(2026, 9, 29, 10))
    await run_scoring(session, out.id, kept.id)
    assert out.verified_at is not None

    await withdraw(session, reporter, out.id, at=out.verified_at + timedelta(hours=23))
    withdrawn = await session.scalars(
        select(OutboxEvent).where(
            OutboxEvent.event_type == DeedWithdrawn.event_type,
            OutboxEvent.payload["deed_id"].astext == str(out.id),
        )
    )
    for event in withdrawn:
        event.occurred_at = out.verified_at + timedelta(hours=23)
    await session.flush()

    assert (await finalize_month(session, SEP, now=AFTER)).outcome == Outcome.EVENTS_PENDING
    await run_scoring(session, out.id)
    assert (await finalize_month(session, SEP, now=AFTER)).outcome == Outcome.FINALIZED

    bangalore = await city(session)
    final = await _leaderboard.latest_snapshot(session, bangalore, SEP)
    assert final is not None and final.final
    assert set(await entries(session, final.id)) == {other.user_id}


# --------------------------------------------------- idempotent, frozen


async def test_finalize_is_idempotent_and_the_final_board_never_changes(
    session: AsyncSession,
) -> None:
    a, b, r1, r2 = await members(session, 4)
    bangalore = await city(session)
    await _deeds(session, a, [r1, r2], bangalore)
    late = await counted(session, b, r1, region_id=bangalore, verified_at=ist(2026, 9, 10))

    first = await finalize_month(session, SEP, now=AFTER)
    assert (first.outcome, first.snapshots) == (Outcome.FINALIZED, 1)
    assert first.titles == 2  # city: a #1 Hero, b #2 Top 3 (no ward boards)
    row = await session.get(MonthFinalization, SEP)
    assert row is not None and (row.snapshot_count, row.titles_awarded) == (1, 2)
    titles = await _titles(session)

    again = await finalize_month(session, SEP, now=AFTER + timedelta(hours=1))
    assert again.outcome == Outcome.ALREADY_FINALIZED
    assert await _titles(session) == titles
    assert len(await _events(session, "scoring.month_finalized")) == 1
    finals = await session.scalar(
        select(func.count())
        .select_from(LeaderboardSnapshot)
        .where(LeaderboardSnapshot.period_month == SEP, LeaderboardSnapshot.final.is_(True))
    )
    assert finals == 1

    # A later M5-style rejection writes a reversal; the final board and titles stay.
    await reverse(session, late)
    final = await _leaderboard.latest_snapshot(session, bangalore, SEP)
    assert final is not None and final.final
    assert set(await entries(session, final.id)) == {a.user_id, b.user_id}
    # The refresh job leaves a finalized month alone.
    assert SEP not in await _leaderboard.refresh_due(session, now=AFTER + timedelta(days=1))
    assert await _titles(session) == titles


async def test_the_job_finalizes_oldest_first_and_a_blocked_month_holds_later_ones(
    session: AsyncSession,
) -> None:
    a, r = await members(session, 2)
    bangalore = await city(session)
    await counted(session, a, r, region_id=bangalore, verified_at=ist(2026, 8, 20))
    await counted(session, a, r, region_id=bangalore, verified_at=ist(2026, 9, 20))
    now = ist(2026, 10, 3)

    first = await finalize_due(session, now=now)
    assert first is not None and (first.month, first.outcome) == (month(2026, 8), Outcome.FINALIZED)
    second = await finalize_due(session, now=now)
    assert second is not None and (second.month, second.outcome) == (SEP, Outcome.FINALIZED)
    assert await finalize_due(session, now=now) is None  # October is still open


# --------------------------------------------------- titles and minimums (PO Q54)


async def test_ward_minimum_three_deeds_from_two_reporters(session: AsyncSession) -> None:
    a, b, c, d, r1, r2 = await members(session, 6)
    qualifies = await ward(session, "FZ1")
    one_reporter = await ward(session, "FZ2")
    too_few = await ward(session, "FZ3")
    await _deeds(session, a, [r1, r1, r2], qualifies)  # 3 deeds, 2 reporters: Hero
    await _deeds(session, b, [r1, r1, r1], one_reporter)  # 3 deeds, 1 reporter: none
    await _deeds(session, c, [r1, r2], too_few)  # 2 deeds, 2 reporters: none
    # d is #2 in the qualifying ward: a ward has only place 1, never passed down.
    await _deeds(session, d, [r1, r2], qualifies, start=10)

    result = await finalize_month(session, SEP, now=AFTER)
    assert result.outcome == Outcome.FINALIZED
    ward_titles = {
        (region, place, key, user)
        for region, place, key, user in await _titles(session)
        if region in (qualifies, one_reporter, too_few)
    }
    assert ward_titles == {(qualifies, 1, "hero", a.user_id)}
    # Boards stay visible where nobody qualified.
    for region in (one_reporter, too_few):
        snap = await _leaderboard.latest_snapshot(session, region, SEP)
        assert snap is not None and snap.final and snap.entry_count == 1


async def test_no_ward_hero_when_the_leader_misses_the_minimum(session: AsyncSession) -> None:
    a, b, r1, r2 = await members(session, 4)
    quiet = await ward(session, "FZ4")
    await _deeds(session, a, [r1, r1, r1, r1], quiet)  # #1 but 1 reporter
    await _deeds(session, b, [r1, r2, r1], quiet, start=10)  # qualifies but #2
    await finalize_month(session, SEP, now=AFTER)
    assert {t for t in await _titles(session) if t[0] == quiet} == set()


async def test_shared_places_all_get_the_title_with_dense_numbering(
    session: AsyncSession,
) -> None:
    """Q58 = B: a and b share #1 (both City Hero), c is #2 and d #3 (both Top 3), e #4 none."""
    a, b, c, d, e, r1, r2 = await members(session, 7)
    bangalore = await city(session)
    kora = await ward(session, "FZ5")
    for doer in (a, b):
        await _deeds(session, doer, [r1, r2, r1, r2], kora)
    await _deeds(session, c, [r1, r2, r1], bangalore)
    await _deeds(session, d, [r1, r2], bangalore)
    await _deeds(session, e, [r1], bangalore)

    result = await finalize_month(session, SEP, now=AFTER)
    titles = await _titles(session)
    assert {t for t in titles if t[0] == bangalore} == {
        (bangalore, 1, "hero", a.user_id),
        (bangalore, 1, "hero", b.user_id),
        (bangalore, 2, "top3", c.user_id),
        (bangalore, 3, "top3", d.user_id),
    }
    # a and b also share the ward: a user can hold a ward and the city title in one month.
    assert {t for t in titles if t[0] == kora} == {
        (kora, 1, "hero", a.user_id),
        (kora, 1, "hero", b.user_id),
    }
    assert result.titles == len(titles) == 6
    awarded = await _events(session, "scoring.hero_title_awarded")
    assert len(awarded) == 6
    assert {"user_id", "period_month", "region_id", "level_kind", "place", "title_key"} <= set(
        awarded[0].payload
    )


async def test_titles_need_counted_deeds_of_that_month(session: AsyncSession) -> None:
    a, r = await members(session, 2)
    bangalore = await city(session)
    await counted(session, a, r, region_id=bangalore, verified_at=ist(2026, 10, 3))
    result = await finalize_month(session, SEP, now=AFTER)
    assert (result.outcome, result.snapshots, result.titles) == (Outcome.FINALIZED, 0, 0)
    assert (
        await session.scalar(
            select(func.count()).select_from(PointsLedger).where(PointsLedger.period_month == OCT)
        )
        == 1
    )
    assert datetime.now(UTC) > AFTER  # the test month is really in the past
