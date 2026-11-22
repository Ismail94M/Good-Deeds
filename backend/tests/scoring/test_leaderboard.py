"""Leaderboards (docs/M4-DESIGN.md D59, D66, D67, §4.5, §8 "Leaderboards"): a deed counts once
per level from its frozen region, rank score from config weights, the Q50 tie-break, dense or
competition numbering (Q58), provisional snapshots and their refresh."""

import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import User, UserStatus
from app.modules.scoring import _leaderboard
from app.modules.scoring._limits import RankWeights, load_leaderboard
from app.modules.scoring.models import LeaderboardSnapshot
from tests.deeds.helpers import members
from tests.scoring.board_helpers import city, counted, ist, month, publish_board, reverse, ward

SEP = month(2026, 9)


async def _boards(session: AsyncSession, period=SEP):  # type: ignore[no-untyped-def]
    _, config = await load_leaderboard(session)
    return await _leaderboard.compute_boards(session, period, config)


def _ranks(rows: list[_leaderboard.BoardRow]) -> dict[uuid.UUID, int]:
    return {r.user_id: r.rank for r in rows}


async def test_a_deed_counts_once_per_level_from_its_region(session: AsyncSession) -> None:
    a, b, r = await members(session, 3)
    kora = await ward(session, "LB1")
    other = await ward(session, "LB2")
    bangalore = await city(session)
    await counted(session, a, r, region_id=kora, verified_at=ist(2026, 9, 3))
    await counted(session, a, r, region_id=other, verified_at=ist(2026, 9, 4))
    # No ward: frozen at the city, so it counts only on the city board (D66).
    await counted(session, b, r, region_id=bangalore, verified_at=ist(2026, 9, 5))

    boards = await _boards(session)
    level, kora_rows = boards[kora]
    assert level.kind == "ward"
    assert [(row.user_id, row.deed_count) for row in kora_rows] == [(a.user_id, 1)]
    assert [(row.user_id, row.deed_count) for row in boards[other][1]] == [(a.user_id, 1)]
    city_rows = {row.user_id: row for row in boards[bangalore][1]}
    assert boards[bangalore][0].kind == "city"
    assert city_rows[a.user_id].deed_count == 2  # each deed once on the city board
    assert city_rows[b.user_id].deed_count == 1
    # State and country are disabled: no boards for them.
    assert {lvl.kind for lvl, _ in boards.values()} == {"ward", "city"}


async def test_only_standing_prize_rank_doer_rows_of_the_month_count(
    session: AsyncSession,
) -> None:
    a, b, r = await members(session, 3)
    bangalore = await city(session)
    await counted(
        session, a, r, region_id=bangalore, verified_at=ist(2026, 9, 3), reporter_row=True
    )
    withdrawn = await counted(session, a, r, region_id=bangalore, verified_at=ist(2026, 9, 4))
    await reverse(session, withdrawn)
    # Not prize-rank eligible (an invite deed with one report).
    await counted(session, b, r, region_id=bangalore, verified_at=ist(2026, 9, 5), prize=False)
    # Another month.
    await counted(session, b, r, region_id=bangalore, verified_at=ist(2026, 10, 1, 0, 5))

    rows = (await _boards(session))[bangalore][1]
    # The reporter's own row never puts them on a board (D57); b's only September deed doesn't
    # count for prize rank.
    assert [(row.user_id, row.deed_count, row.doer_points) for row in rows] == [(a.user_id, 1, 10)]


async def test_rank_score_uses_the_configured_weights(session: AsyncSession) -> None:
    a, b, r1, r2 = await members(session, 4)
    bangalore = await city(session)
    # a: 2 deeds, 20 points. b: 1 deed, 30 points.
    for day in (3, 4):
        await counted(session, a, r1, region_id=bangalore, verified_at=ist(2026, 9, day))
    await counted(session, b, r2, region_id=bangalore, verified_at=ist(2026, 9, 3), points=30)

    rows = (await _boards(session))[bangalore][1]
    by_user = {row.user_id: row for row in rows}
    assert (by_user[a.user_id].rank_score, by_user[b.user_id].rank_score) == (22, 31)
    assert _ranks(rows) == {b.user_id: 1, a.user_id: 2}

    await publish_board(session, weights=RankWeights(deeds=20, points=0))
    rows = (await _boards(session))[bangalore][1]
    assert _ranks(rows) == {a.user_id: 1, b.user_id: 2}
    assert {row.user_id: row.rank_score for row in rows} == {a.user_id: 40, b.user_id: 20}


async def test_tie_break_reporters_then_days_then_a_dense_shared_rank(
    session: AsyncSession,
) -> None:
    a, b, c, d, e, r1, r2 = await members(session, 7)
    bangalore = await city(session)
    # Every doer has 2 deeds, 20 points (same rank score).
    # a: 2 reporters, 2 days. b: 1 reporter, 2 days. c: 1 reporter, 1 day.
    # d: same as c (shared). e: lower score.
    await counted(session, a, r1, region_id=bangalore, verified_at=ist(2026, 9, 3))
    await counted(session, a, r2, region_id=bangalore, verified_at=ist(2026, 9, 4))
    await counted(session, b, r1, region_id=bangalore, verified_at=ist(2026, 9, 3))
    await counted(session, b, r1, region_id=bangalore, verified_at=ist(2026, 9, 4))
    for doer in (c, d):
        await counted(session, doer, r1, region_id=bangalore, verified_at=ist(2026, 9, 6, 9))
        await counted(session, doer, r2, region_id=bangalore, verified_at=ist(2026, 9, 6, 10))
    await counted(session, e, r1, region_id=bangalore, verified_at=ist(2026, 9, 7))

    rows = (await _boards(session))[bangalore][1]
    # c and d: 2 reporters but 1 day; a: 2 reporters, 2 days; b: 1 reporter.
    assert _ranks(rows) == {a.user_id: 1, c.user_id: 2, d.user_id: 2, b.user_id: 3, e.user_id: 4}


async def test_dense_and_competition_numbering(session: AsyncSession) -> None:
    a, b, c, r = await members(session, 4)
    bangalore = await city(session)
    for doer in (a, b):
        await counted(session, doer, r, region_id=bangalore, verified_at=ist(2026, 9, 3))
        await counted(session, doer, r, region_id=bangalore, verified_at=ist(2026, 9, 4))
    await counted(session, c, r, region_id=bangalore, verified_at=ist(2026, 9, 3))

    rows = (await _boards(session))[bangalore][1]
    assert _ranks(rows) == {a.user_id: 1, b.user_id: 1, c.user_id: 2}  # Q58 = B: 1, 1, 2

    await publish_board(session, shared_rank_style="competition")
    rows = (await _boards(session))[bangalore][1]
    assert _ranks(rows) == {a.user_id: 1, b.user_id: 1, c.user_id: 3}


async def test_inactive_accounts_are_left_off_boards(session: AsyncSession) -> None:
    a, b, r = await members(session, 3)
    bangalore = await city(session)
    await counted(session, a, r, region_id=bangalore, verified_at=ist(2026, 9, 3))
    await counted(session, b, r, region_id=bangalore, verified_at=ist(2026, 9, 3))
    user = await session.get(User, b.user_id)
    assert user is not None
    user.status = UserStatus.SUSPENDED
    await session.flush()

    rows = (await _boards(session))[bangalore][1]
    assert [row.user_id for row in rows] == [a.user_id]


async def test_refresh_writes_provisional_snapshots_when_due_and_prunes(
    session: AsyncSession,
) -> None:
    a, r = await members(session, 2)
    kora = await ward(session, "LB3")
    bangalore = await city(session)
    now = ist(2026, 9, 20)
    deed = await counted(session, a, r, region_id=kora, verified_at=ist(2026, 9, 3))
    _, config = await load_leaderboard(session)

    assert await _leaderboard.refresh_due(session, now=now) == [SEP]
    snap = await _leaderboard.latest_snapshot(session, kora, SEP)
    assert snap is not None and snap.final is False and snap.entry_count == 1
    city_snap = await _leaderboard.latest_snapshot(session, bangalore, SEP)
    assert city_snap is not None and city_snap.entry_count == 1

    # Not due again until refresh_seconds have passed.
    soon = now + timedelta(seconds=config.refresh_seconds - 1)
    assert await _leaderboard.refresh_due(session, now=soon) == []

    # The deed is withdrawn: the next refresh shows an empty board, not a stale one.
    await reverse(session, deed)
    for i in range(1, 4):
        later = now + timedelta(seconds=config.refresh_seconds * i)
        assert await _leaderboard.refresh_due(session, now=later) == [SEP]
    snap = await _leaderboard.latest_snapshot(session, kora, SEP)
    assert snap is not None and snap.entry_count == 0
    kept = await session.scalar(
        select(func.count())
        .select_from(LeaderboardSnapshot)
        .where(LeaderboardSnapshot.region_id == kora, LeaderboardSnapshot.period_month == SEP)
    )
    assert kept == _leaderboard.KEEP_SNAPSHOTS


async def test_refresh_covers_the_current_and_previous_open_month(session: AsyncSession) -> None:
    a, r = await members(session, 2)
    bangalore = await city(session)
    await counted(session, a, r, region_id=bangalore, verified_at=ist(2026, 9, 30, 23, 50))
    await counted(session, a, r, region_id=bangalore, verified_at=ist(2026, 10, 1, 0, 10))

    done = await _leaderboard.refresh_due(session, now=ist(2026, 10, 1, 1))
    assert done == [SEP, month(2026, 10)]
    for period in done:
        snap = await _leaderboard.latest_snapshot(session, bangalore, period)
        assert snap is not None and snap.entry_count == 1
