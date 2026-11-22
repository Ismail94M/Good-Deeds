"""QA7: M4a boundaries and abuse cases not covered by the developer tests (docs/M4-DESIGN.md §8,
§3.4, D50 to D70, PO Q45 to Q58).

- IST midnight to the second (points day, pair month, both daily caps, ``reporter_today``);
- the 24 h window edge for edits and photo evidence (server/DB time);
- late and out-of-order handlers after a withdrawal; a deed with no ward;
- a shared #1 where only one of the tied users meets the ward minimum;
- a month with zero deeds reads as final once its cutoff has passed;
- privacy of every public shape, signed-out access, per-account limits, admin conflict rules,
  and the owner-only Self-log Deeds total.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit
from app.core.runtime_config import publish
from app.modules.deeds import _edits
from app.modules.deeds import service as deeds
from app.modules.deeds._limits import load_limits
from app.modules.deeds.events import DeedVerified
from app.modules.deeds.models import Area, Deed
from app.modules.deeds.schemas import DeedEdit, DeedOut
from app.modules.identity.models import Role, User
from app.modules.scoring import _finalize, _leaderboard, _reads
from app.modules.scoring._evidence import record_photo_ready
from app.modules.scoring._finalize import Outcome, finalize_month
from app.modules.scoring._ledger import reconcile_deed
from app.modules.scoring._limits import load_leaderboard, load_scoring
from app.modules.scoring.models import (
    HeroTitle,
    LedgerKind,
    LedgerReason,
    LedgerRole,
    ScoringEvidence,
)
from tests.deeds.helpers import Member, make_member, members, scan_and_report
from tests.deeds.invite_helpers import create_invite, withdraw
from tests.deeds.test_self_logs import _create as create_self_log
from tests.scoring.board_helpers import city, counted, entries, ist, month, ward
from tests.scoring.helpers import active, ledger, points, run_scoring

DOER, REPORTER = LedgerRole.DOER, LedgerRole.REPORTER
IST = ZoneInfo("Asia/Kolkata")
SPAM = "Helped someone today, follow me for more"
PRIVATE_KEYS = {
    "points",
    "doer_points",
    "reporter_points",
    "rank_score",
    "total_points",
    "provisional_points",
    "self_logged",
    "breakdown",
}


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def _ist(y: int, m: int, d: int, h: int, mi: int, s: int = 0, us: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, s, us, tzinfo=IST).astimezone(UTC)


def _verified(out: DeedOut) -> datetime:
    assert out.verified_at is not None
    return out.verified_at


async def _db_now(session: AsyncSession) -> datetime:
    """The database's ``now()``: the time ``deed_revisions.created_at`` gets in this test."""
    value = await session.scalar(text("SELECT now()"))
    assert isinstance(value, datetime)
    return value


async def _edit(session: AsyncSession, reporter: Member, deed_id: uuid.UUID, **fields: str) -> None:
    _, limits = await load_limits(session)
    await _edits.edit_deed(
        session, reporter.user_id, deed_id, DeedEdit.model_validate(fields), limits=limits
    )


async def _role(session: AsyncSession, member: Member, role: Role) -> None:
    await session.execute(update(User).where(User.id == member.user_id).values(role=role))
    await session.commit()


# --------------------------------------------------- IST midnight to the second (D52, D58, Q47)


async def test_day_and_pair_month_flip_at_ist_midnight_to_the_second(
    session: AsyncSession,
) -> None:
    """Three reports of one pair in October, the 3rd at 31 Oct 23:59:59 IST; the next one at
    1 Nov 00:00:00 IST is ordinal 1 of November (100 %), not the 4th (75 %)."""
    reporter, doer = await members(session, 2)
    times = [
        _ist(2026, 10, 29, 12, 0),
        _ist(2026, 10, 30, 12, 0),
        _ist(2026, 10, 31, 23, 59, 59),
        _ist(2026, 11, 1, 0, 0, 0),
    ]
    # Far apart, so the last two (1 s apart) are separate acts.
    outs = [
        await scan_and_report(session, reporter, doer, at=t, north_m=i * 500.0)
        for i, t in enumerate(times)
    ]
    await run_scoring(session, *(o.id for o in outs))

    rows = [await active(session, o.id, DOER) for o in outs]
    assert all(r is not None for r in rows)
    got = [(r.earned_on, r.period_month, r.breakdown["pair_ordinal"], r.points) for r in rows if r]
    assert got == [
        (date(2026, 10, 29), date(2026, 10, 1), 1, 10),
        (date(2026, 10, 30), date(2026, 10, 1), 2, 10),
        (date(2026, 10, 31), date(2026, 10, 1), 3, 10),
        (date(2026, 11, 1), date(2026, 11, 1), 1, 10),
    ]
    reporter_rows = [await active(session, o.id, REPORTER) for o in outs]
    assert [r.period_month for r in reporter_rows if r] == [date(2026, 10, 1)] * 3 + [
        date(2026, 11, 1)
    ]


async def test_pair_ordinal_7_to_8_boundary_and_reporter_rounding(session: AsyncSession) -> None:
    """Ordinals 7 and 8 in one month: doer 8 then 5, reporter 2 (1.5 rounded up) then 1."""
    reporter, doer = await members(session, 2)
    start = _ist(2026, 10, 1, 9, 0)
    outs = [
        await scan_and_report(session, reporter, doer, at=start + timedelta(days=i))
        for i in range(8)
    ]
    await run_scoring(session, *(o.id for o in outs))
    assert [await points(session, o.id, DOER) for o in outs[2:]] == [10, 8, 8, 8, 8, 5]
    assert [await points(session, o.id, REPORTER) for o in outs[2:]] == [2, 2, 2, 2, 2, 1]
    eighth = await active(session, outs[7].id, DOER)
    assert eighth is not None
    assert (eighth.breakdown["pair_ordinal"], eighth.breakdown["pair_percent"]) == (8, 50)


async def test_reporter_daily_cap_resets_at_ist_midnight(session: AsyncSession) -> None:
    """10 awards by 23:54:59, the 11th at 23:59:59 IST is a cap decision, the 12th at 00:00:00
    IST the next day is a full award again; ``reporter_today`` follows the server's IST day."""
    reporter, *doers = await members(session, 13)
    last_second = _ist(2026, 10, 10, 23, 59, 59)
    first_ten = [
        await scan_and_report(
            session, reporter, d, at=last_second - (10 - i) * timedelta(minutes=5)
        )
        for i, d in enumerate(doers[:10])
    ]
    eleventh = await scan_and_report(session, reporter, doers[10], at=last_second)
    twelfth = await scan_and_report(
        session, reporter, doers[11], at=last_second + timedelta(seconds=1)
    )
    for out in [*first_ten, eleventh, twelfth]:
        await run_scoring(session, out.id)

    assert [await points(session, o.id, REPORTER) for o in first_ten] == [2] * 10
    capped = await active(session, eleventh.id, REPORTER)
    assert capped is not None and (capped.kind, capped.reason, capped.earned_on) == (
        LedgerKind.DECISION,
        LedgerReason.REPORTER_DAILY_CAP,
        date(2026, 10, 10),
    )
    fresh = await active(session, twelfth.id, REPORTER)
    assert fresh is not None and (fresh.kind, fresh.points, fresh.earned_on) == (
        LedgerKind.AWARD,
        2,
        date(2026, 10, 11),
    )
    # The doers are untouched by the reporter's cap.
    assert await points(session, eleventh.id, DOER) == 10

    late = await _reads.my_scoring(session, reporter.user_id, now=last_second)
    assert late.reporter_today.used == 10  # 11 entries that day, shown capped at the cap
    next_day = await _reads.my_scoring(
        session, reporter.user_id, now=last_second + timedelta(seconds=1)
    )
    assert next_day.reporter_today.used == 1


async def test_doer_daily_cap_resets_at_ist_midnight(session: AsyncSession) -> None:
    doer, *reporters = await members(session, 13)
    start = _ist(2026, 10, 10, 0, 5)
    first_ten = [
        await scan_and_report(session, r, doer, at=start + i * timedelta(minutes=125))
        for i, r in enumerate(reporters[:10])
    ]
    eleventh = await scan_and_report(
        session, reporters[10], doer, at=_ist(2026, 10, 10, 23, 59, 59)
    )
    twelfth = await scan_and_report(
        session, reporters[11], doer, at=_ist(2026, 10, 11, 0, 0, 0), north_m=500.0
    )
    for out in [*first_ten, eleventh, twelfth]:
        await run_scoring(session, out.id)
    assert all([await deeds_counts(session, o.id) for o in [*first_ten, eleventh, twelfth]])

    assert [await points(session, o.id, DOER) for o in first_ten] == [10] * 10
    capped = await active(session, eleventh.id, DOER)
    assert capped is not None and (capped.kind, capped.deed_units) == (LedgerKind.DECISION, 0)
    fresh = await active(session, twelfth.id, DOER)
    assert fresh is not None
    assert (fresh.kind, fresh.points, fresh.earned_on) == (LedgerKind.AWARD, 10, date(2026, 10, 11))


async def deeds_counts(session: AsyncSession, deed_id: uuid.UUID) -> bool:
    inputs = await deeds.scoring_inputs(session, deed_id)
    assert inputs is not None
    return inputs.counts_for_points


# --------------------------------------------------- the 24 h window edge (D56, D51, §3.4 #9)


async def test_edit_one_second_inside_the_window_rescores_one_second_after_does_not(
    session: AsyncSession,
) -> None:
    """Edit time is the revision's DB time; the window is ``verified_at + 24 h``."""
    r1, r2, d1, d2 = await members(session, 4)
    now = await _db_now(session)
    inside = await scan_and_report(session, r1, d1, at=now - timedelta(hours=24, seconds=-1))
    outside = await scan_and_report(session, r2, d2, at=now - timedelta(hours=24, seconds=1))
    await run_scoring(session, inside.id, outside.id)

    await _edit(session, r1, inside.id, description=SPAM)
    await _edit(session, r2, outside.id, description=SPAM)
    await run_scoring(session, inside.id, outside.id)

    assert await points(session, inside.id, DOER) == 4  # spam: min_points, re-scored
    assert await points(session, outside.id, DOER) == 10  # text changed, points didn't
    assert len(await ledger(session, outside.id)) == 2
    # A later re-score for any reason still reads the text as of the window's end.
    assert await reconcile_deed(session, outside.id) == []
    assert await reconcile_deed(session, inside.id) == []


async def test_photo_evidence_edges(session: AsyncSession) -> None:
    """Attached at exactly +24 h after the report is still in the photo window, +24 h 1 s is
    not; ready 1 s before the withdrawal window ends gives the bonus, 1 s after records the
    evidence without moving points."""
    r1, r2, r3, d1, d2, d3 = await members(session, 6)
    base = datetime.now(UTC) - timedelta(hours=30)
    a = await scan_and_report(session, r1, d1, at=base)
    b = await scan_and_report(session, r2, d2, at=base, north_m=3000.0)
    c = await scan_and_report(session, r3, d3, at=base, north_m=6000.0)
    await run_scoring(session, a.id, b.id, c.id)
    window = timedelta(hours=24)

    # a: attached at exactly reported_at + 24 h, ready 1 s before the withdrawal window ends.
    assert await record_photo_ready(
        session,
        deed_id=a.id,
        photo_id=uuid.uuid4(),
        attached_at=a.reported_at + window,
        now=_verified(a) + window - timedelta(seconds=1),
    )
    # b: attached 1 s after the photo window: not evidence at all.
    assert not await record_photo_ready(
        session,
        deed_id=b.id,
        photo_id=uuid.uuid4(),
        attached_at=b.reported_at + window + timedelta(seconds=1),
        now=_verified(b) + timedelta(hours=1),
    )
    # c: attached in time but ready 1 s after the withdrawal window: recorded, no bonus.
    assert await record_photo_ready(
        session,
        deed_id=c.id,
        photo_id=uuid.uuid4(),
        attached_at=c.reported_at + timedelta(hours=1),
        now=_verified(c) + window + timedelta(seconds=1),
    )
    for out in (a, b, c):
        await reconcile_deed(session, out.id)

    assert await points(session, a.id, DOER) == 12
    assert await points(session, b.id, DOER) == 10
    assert await session.get(ScoringEvidence, b.id) is None
    assert await points(session, c.id, DOER) == 10
    assert await session.get(ScoringEvidence, c.id) is not None
    # Reporter points never depend on photos.
    assert [await points(session, o.id, REPORTER) for o in (a, b, c)] == [2, 2, 2]


# --------------------------------------------------- order and idempotency (D49)


async def test_withdrawal_handled_before_the_late_verified_handler_writes_nothing(
    session: AsyncSession,
) -> None:
    """Verified and withdrawn before the worker ran: whatever order the handlers run in, and
    however often, the deed never gets a ledger row (no award-then-reversal noise)."""
    reporter, doer = await members(session, 2)
    out = await scan_and_report(session, reporter, doer)
    inputs = await deeds.scoring_inputs(session, out.id)
    assert inputs is not None and inputs.same_act_group_id is not None
    await withdraw(session, reporter, out.id)
    await run_scoring(session, out.id, reverse=True)  # DeedWithdrawn first
    assert await ledger(session, out.id) == []
    # A duplicated DeedVerified delivered late changes nothing either.
    emit(
        session,
        DeedVerified(
            deed_id=out.id,
            reporter_id=reporter.user_id,
            doer_id=doer.user_id,
            method="qr_scan",
            verified_at=_verified(out),
            same_act_group_id=inputs.same_act_group_id,
            counts_for_points=True,
            prize_rank_eligible=True,
            limits_version=1,
        ),
    )
    await session.flush()
    await run_scoring(session, out.id)
    assert await ledger(session, out.id) == []
    assert await reconcile_deed(session, out.id) == []


async def test_withdrawal_after_scoring_mirrors_each_entry_exactly_once(
    session: AsyncSession,
) -> None:
    reporter, doer = await members(session, 2)
    out = await scan_and_report(session, reporter, doer)
    await run_scoring(session, out.id)
    await withdraw(session, reporter, out.id)
    for _ in range(2):
        await run_scoring(session, out.id)
        await reconcile_deed(session, out.id)
    rows = await ledger(session, out.id)
    awards = {r.id: r for r in rows if r.kind == LedgerKind.AWARD}
    reversals = [r for r in rows if r.kind == LedgerKind.REVERSAL]
    assert len(awards) == len(reversals) == 2
    for rev in reversals:
        orig = awards[rev.reverses_entry_id]  # type: ignore[index]
        assert (rev.points, rev.deed_units) == (-orig.points, -orig.deed_units)
        assert (rev.region_id, rev.earned_on, rev.period_month, rev.verified_at) == (
            orig.region_id,
            orig.earned_on,
            orig.period_month,
            orig.verified_at,
        )
        assert rev.counts_for_prize_rank == orig.counts_for_prize_rank
        assert rev.user_id == orig.user_id and rev.role == orig.role


# --------------------------------------------------- regions (D66)


async def test_a_deed_without_a_ward_counts_on_the_city_board_only(session: AsyncSession) -> None:
    r1, r2, nowhere_doer, ward_doer = await members(session, 4)
    no_ward = await scan_and_report(session, r1, nowhere_doer)
    in_ward = await scan_and_report(session, r2, ward_doer, north_m=3000.0)
    area = await session.scalar(select(Area).order_by(Area.id).limit(1))
    assert area is not None
    await session.execute(update(Deed).where(Deed.id == no_ward.id).values(area_id=None))
    await session.execute(update(Deed).where(Deed.id == in_ward.id).values(area_id=area.id))
    await run_scoring(session, no_ward.id, in_ward.id)

    bangalore = await city(session)
    row = await active(session, no_ward.id, DOER)
    assert row is not None and row.region_id == bangalore
    ward_row = await active(session, in_ward.id, DOER)
    assert ward_row is not None and ward_row.region_id == area.region_id

    _, config = await load_leaderboard(session)
    boards = await _leaderboard.compute_boards(session, row.period_month, config)
    on_board = {region: {b.user_id for b in rows} for region, (_level, rows) in boards.items()}
    assert {nowhere_doer.user_id, ward_doer.user_id} <= on_board[bangalore]
    assert ward_doer.user_id in on_board[area.region_id]
    wards_with_nowhere = [
        region
        for region, users in on_board.items()
        if region != bangalore and nowhere_doer.user_id in users
    ]
    assert wards_with_nowhere == []


# --------------------------------------------------- titles at the minimum (Q54, Q58)


async def test_shared_ward_first_place_gives_the_title_only_to_who_meets_the_minimum(
    session: AsyncSession,
) -> None:
    """a: 3 deeds (10 each) from 2 reporters on 2 days = 33. b: 2 deeds (15 + 16) from 2
    reporters on 2 days = 33. Same tie-break keys, so a shared #1 (dense), but only a has the
    3 deeds a ward Hero needs; b never gets it, and nobody else is promoted into the place."""
    a, b, c, r1, r2 = await members(session, 5)
    zone = await ward(session, "QA1")
    await counted(session, a, r1, region_id=zone, verified_at=ist(2026, 9, 3, 9))
    await counted(session, a, r2, region_id=zone, verified_at=ist(2026, 9, 3, 15))
    await counted(session, a, r1, region_id=zone, verified_at=ist(2026, 9, 4))
    await counted(session, b, r1, region_id=zone, verified_at=ist(2026, 9, 5), points=15)
    await counted(session, b, r2, region_id=zone, verified_at=ist(2026, 9, 6), points=16)
    # c is #2 and meets the minimum, but a ward has only place 1.
    for day, rep in ((7, r1), (8, r2), (9, r1)):
        await counted(session, c, rep, region_id=zone, verified_at=ist(2026, 9, day), points=1)

    result = await finalize_month(session, month(2026, 9), now=ist(2026, 10, 3))
    assert result.outcome == Outcome.FINALIZED
    snap = await _leaderboard.latest_snapshot(session, zone, month(2026, 9))
    assert snap is not None and snap.final
    titles = set(
        await session.scalars(
            select(HeroTitle.user_id).where(
                HeroTitle.region_id == zone, HeroTitle.period_month == month(2026, 9)
            )
        )
    )
    assert titles == {a.user_id}
    rows = await entries(session, snap.id)
    assert (rows[a.user_id].rank, rows[b.user_id].rank, rows[c.user_id].rank) == (1, 1, 2)


async def test_exactly_at_the_ward_minimum_with_a_raised_config(session: AsyncSession) -> None:
    """The minimums come from config: with 4 deeds / 3 reporters, 4/3 qualifies and 4/2 not."""
    a, b, r1, r2, r3 = await members(session, 5)
    _, config = await load_leaderboard(session)
    levels = tuple(
        lv.model_copy(update={"min_counted_deeds": 4, "min_distinct_reporters": 3})
        if lv.kind == "ward"
        else lv
        for lv in config.levels
    )
    await publish(session, config.model_copy(update={"levels": levels}), actor_id=None, reason="t")
    yes, no = await ward(session, "QA2"), await ward(session, "QA3")
    for day, rep in ((1, r1), (2, r2), (3, r3), (4, r1)):
        await counted(session, a, rep, region_id=yes, verified_at=ist(2026, 9, day))
    for day, rep in ((5, r1), (6, r2), (7, r1), (8, r2)):
        await counted(session, b, rep, region_id=no, verified_at=ist(2026, 9, day))
    await finalize_month(session, month(2026, 9), now=ist(2026, 10, 3))
    held = set(
        await session.execute(
            select(HeroTitle.region_id, HeroTitle.user_id).where(HeroTitle.region_id.in_([yes, no]))
        )
    )
    assert held == {(yes, a.user_id)}


# --------------------------------------------------- a month with zero deeds


async def test_a_past_month_with_zero_deeds_reads_final(session: AsyncSession) -> None:
    (viewer,) = await members(session, 1)
    empty = await _reads.leaderboard(
        session, viewer.user_id, region_code=None, month="2026-07", cursor=None
    )
    assert (empty.final, empty.finalizes_at, empty.items, empty.titles) == (True, None, [], [])
    # Before its cutoff the same empty month is still provisional, with the cutoff shown.
    early = await _reads.leaderboard(
        session,
        viewer.user_id,
        region_code=None,
        month="2026-07",
        cursor=None,
        now=ist(2026, 8, 2, 0, 59),
    )
    assert early.final is False and early.finalizes_at == ist(2026, 8, 2, 1, 0)
    # A month with ledger rows waits for the job, even when this board is empty.
    a, r = await members(session, 2)
    await counted(session, a, r, region_id=await city(session), verified_at=ist(2026, 6, 5))
    june_ward = await _reads.leaderboard(
        session, viewer.user_id, region_code=None, month="2026-06", cursor=None
    )
    assert june_ward.final is False
    assert (await finalize_month(session, month(2026, 6))).outcome == Outcome.FINALIZED
    june = await _reads.leaderboard(
        session, viewer.user_id, region_code=None, month="2026-06", cursor=None
    )
    assert june.final is True and [i.deed_count for i in june.items] == [1]


async def test_an_empty_month_with_a_pending_scoring_event_is_not_final(
    session: AsyncSession,
) -> None:
    """The outbox barrier also guards the zero-deed shortcut: a verified deed whose scoring
    handler hasn't run yet would still land in the month."""
    reporter, doer, viewer = await members(session, 3)
    out = await scan_and_report(session, reporter, doer, at=ist(2026, 5, 20))
    # Not scored yet, so May has no ledger rows; its DeedVerified event is from before the
    # cutoff only if it is back-dated (the outbox time is the real clock).
    await session.execute(
        text("UPDATE outbox_events SET occurred_at = :at WHERE payload->>'deed_id' = :id"),
        {"at": ist(2026, 5, 20), "id": str(out.id)},
    )
    assert await _finalize.closed_without_deeds(session, month(2026, 5), datetime.now(UTC)) is False
    board = await _reads.leaderboard(
        session, viewer.user_id, region_code=None, month="2026-05", cursor=None
    )
    assert board.final is False
    await run_scoring(session, out.id)
    assert await points(session, out.id, DOER) == 10


# --------------------------------------------------- API: privacy, auth, limits, admin


async def test_public_shapes_never_carry_points_even_for_the_owner(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer = await members(session, 2)
    zone = await ward(session, "QA4")
    r2, r3 = await members(session, 2)
    for day, rep in ((3, reporter), (4, r2), (5, r3)):
        await counted(
            session, doer, rep, region_id=zone, verified_at=ist(2026, 9, day), reporter_row=True
        )
    await finalize_month(session, month(2026, 9), now=ist(2026, 10, 3))
    await session.commit()

    for headers in ({}, doer.headers):  # signed out, and the owner on their own public pages
        for path in (f"/api/scoring/users/{doer.handle}", f"/api/users/{doer.handle}/deed-counts"):
            res = await client.get(path, headers=headers)
            assert res.status_code == 200, path
            assert not _keys(res.json()) & PRIVATE_KEYS, path
    for path in (
        "/api/leaderboards?month=2026-09",
        "/api/leaderboards?region=IN-KA-BLR-QA4&month=2026-09",
        "/api/leaderboards/heroes?region=IN-KA-BLR-QA4&year=2026",
        "/api/leaderboards/heroes?year=2026",
        "/api/leaderboards/regions?kind=ward&q=QA4",
    ):
        res = await client.get(path, headers=doer.headers)
        assert res.status_code == 200, path
        assert not _keys(res.json()) & PRIVATE_KEYS, path
    board = (
        await client.get(
            "/api/leaderboards?region=IN-KA-BLR-QA4&month=2026-09", headers=doer.headers
        )
    ).json()
    assert board["me"]["deed_count"] == 3 and set(board["me"]) == {
        "rank",
        "shared",
        "user",
        "tier",
        "deed_count",
    }
    # The reporter's own points never reach a board: reporters aren't ranked at all (D57).
    assert reporter.handle not in {i["user"]["handle"] for i in board["items"]}


async def test_signed_out_visitors_get_only_the_public_profile(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer = await members(session, 2)
    out = await scan_and_report(session, reporter, doer)
    await run_scoring(session, out.id)
    await session.commit()
    for path in (
        "/api/scoring/options",
        "/api/scoring/me",
        "/api/scoring/me/ledger",
        f"/api/scoring/deeds/{out.id}",
        "/api/leaderboards",
        "/api/leaderboards/regions?kind=ward",
        "/api/leaderboards/heroes",
        f"/api/admin/scoring/deeds/{out.id}",
        "/api/admin/scoring/months/2026-09",
    ):
        assert (await client.get(path)).status_code == 401, path
    public = await client.get(f"/api/scoring/users/{doer.handle}")
    assert public.status_code == 200
    # A bad token on the public endpoint is still a 401, never a silent anonymous read.
    bad = await client.get(
        f"/api/scoring/users/{doer.handle}", headers={"Authorization": "Bearer nope"}
    )
    assert bad.status_code == 401


async def test_member_reads_have_a_per_account_limit(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, b = await members(session, 2)
    _, config = await load_scoring(session)
    await publish(
        session, config.model_copy(update={"rate_me_user": "2/60"}), actor_id=None, reason="t"
    )
    await session.commit()
    assert (await client.get("/api/scoring/me", headers=a.headers)).status_code == 200
    assert (await client.get("/api/leaderboards", headers=a.headers)).status_code == 200
    # The limit is shared across #2 to #8 for one account...
    limited = await client.get("/api/scoring/me/ledger", headers=a.headers)
    assert (limited.status_code, limited.json()["code"]) == (429, "rate_limited")
    # ...and per account: another member (same IP) still reads.
    assert (await client.get("/api/scoring/me", headers=b.headers)).status_code == 200


async def test_admin_conflict_rules_cover_the_doer_and_unknown_deeds(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, admin, member = await members(session, 4)
    out = await scan_and_report(session, reporter, doer)
    await run_scoring(session, out.id)
    await _role(session, doer, Role.ADMIN)
    await _role(session, admin, Role.ADMIN)
    path = f"/api/admin/scoring/deeds/{out.id}"

    conflicted = await client.get(path, headers=doer.headers)
    assert (conflicted.status_code, conflicted.json()["code"]) == (404, "deed_not_found")
    assert (await client.get(path, headers=admin.headers)).status_code == 200
    unknown = await client.get(f"/api/admin/scoring/deeds/{uuid.uuid4()}", headers=admin.headers)
    assert unknown.status_code == 404
    assert (
        await client.get("/api/admin/scoring/months/2026-09", headers=member.headers)
    ).status_code == 403
    bad = await client.get("/api/admin/scoring/months/2026-00", headers=admin.headers)
    assert (bad.status_code, bad.json()["code"]) == (400, "invalid_month")


@pytest.mark.usefixtures("invites_on")
async def test_deed_points_are_404_for_pending_deeds_and_strangers(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, stranger = await members(session, 2)
    pending, _token = await create_invite(session, reporter)
    await session.commit()
    res = await client.get(f"/api/scoring/deeds/{pending.id}", headers=reporter.headers)
    assert (res.status_code, res.json()["code"]) == (404, "deed_not_found")
    res = await client.get(f"/api/scoring/deeds/{uuid.uuid4()}", headers=stranger.headers)
    assert res.status_code == 404


async def test_self_log_total_is_shown_only_to_its_owner(
    client: AsyncClient, session: AsyncSession
) -> None:
    owner = await make_member(session)
    other = await make_member(session)
    await create_self_log(client, owner)
    await create_self_log(client, owner)
    own = (await client.get("/api/me/self-logs", headers=owner.headers)).json()
    assert own["total"] == 2
    theirs = (await client.get("/api/me/self-logs", headers=other.headers)).json()
    assert (theirs["total"], theirs["items"]) == (0, [])
    for path in (f"/api/users/{owner.handle}/deed-counts", f"/api/scoring/users/{owner.handle}"):
        body = (await client.get(path, headers=other.headers)).json()
        assert not _keys(body) & {"self_logged", "total", "self_logs"}
