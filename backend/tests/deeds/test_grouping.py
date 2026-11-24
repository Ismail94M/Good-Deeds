"""Same-act grouping (docs/M2-DESIGN.md §3.4, §7 Same-act grouping)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds._grouping import assign_group
from app.modules.deeds._limits import load_limits
from app.modules.deeds.models import Deed, SameActGroup
from app.modules.deeds.schemas import DeedOut
from tests.deeds.helpers import make_member, members, publish_limits, scan_and_report


async def _group_of(session: AsyncSession, deed: DeedOut) -> SameActGroup:
    row = await session.get(Deed, deed.id)
    assert row is not None and row.same_act_group_id is not None
    group = await session.get(SameActGroup, row.same_act_group_id, populate_existing=True)
    assert group is not None
    return group


async def _counts(session: AsyncSession, deed: DeedOut) -> bool:
    row = await session.get(Deed, deed.id, populate_existing=True)
    assert row is not None
    return row.counts_for_points


def _t0() -> datetime:
    return datetime.now(UTC) - timedelta(hours=6)


async def test_reports_within_radius_join_and_beyond_start_new_group(
    session: AsyncSession,
) -> None:
    doer, a, b, c = await members(session, 4)
    t0 = _t0()
    anchor = await scan_and_report(session, a, doer, at=t0)
    near = await scan_and_report(session, b, doer, at=t0 + timedelta(minutes=1), north_m=199)
    far = await scan_and_report(session, c, doer, at=t0 + timedelta(minutes=2), north_m=-201)
    group = await _group_of(session, anchor)
    assert (await _group_of(session, near)).id == group.id
    assert (await _group_of(session, far)).id != group.id
    assert group.verified_count == 2
    assert near.same_act is not None and near.same_act.report_count == 2


async def test_reports_within_window_join_and_beyond_start_new_group(
    session: AsyncSession,
) -> None:
    doer, a, b, c = await members(session, 4)
    t0 = _t0()
    anchor = await scan_and_report(session, a, doer, at=t0)
    # The window is measured between scan times (L7-A).
    on_edge = await scan_and_report(session, b, doer, at=t0 + timedelta(hours=2))
    too_late = await scan_and_report(session, c, doer, at=t0 + timedelta(hours=2, seconds=1))
    group = await _group_of(session, anchor)
    assert (await _group_of(session, on_edge)).id == group.id
    assert (await _group_of(session, too_late)).id != group.id


async def test_window_applies_before_the_anchor_too(session: AsyncSession) -> None:
    doer, a, b = await members(session, 3)
    t0 = _t0()
    anchor = await scan_and_report(session, a, doer, at=t0)
    # Grouped after the anchor, but scanned 90 minutes before it.
    earlier = await scan_and_report(session, b, doer, at=t0 - timedelta(minutes=90))
    assert (await _group_of(session, earlier)).id == (await _group_of(session, anchor)).id


# --------------------------------------------------- L7-A: scan time, not "When?"


async def test_backdated_reports_scanned_together_join_one_group(session: AsyncSession) -> None:
    doer, a, b, c = await members(session, 4)
    t0 = _t0()
    # a says "Earlier, about 2 hours"; b says "Just now"; c says "about 1 hour". Their
    # "When?" values span 2 h 30 min, but the scans are 30 minutes and 50 m apart.
    first = await scan_and_report(session, a, doer, at=t0, occurred_at=t0 - timedelta(hours=2))
    second = await scan_and_report(session, b, doer, at=t0 + timedelta(minutes=30), north_m=50)
    third = await scan_and_report(
        session,
        c,
        doer,
        at=t0 + timedelta(minutes=20),
        east_m=50,
        occurred_at=t0 - timedelta(minutes=40),
    )
    group = await _group_of(session, first)
    assert (await _group_of(session, second)).id == group.id
    assert (await _group_of(session, third)).id == group.id
    assert (group.verified_count, group.counted_count, group.distinct_reporters) == (3, 3, 3)
    # The anchor is the first scan's time; "When?" is kept as entered, for display only.
    assert group.anchor_at == t0
    assert first.occurred_at == t0 - timedelta(hours=2)


async def test_scans_over_two_hours_apart_are_separate_even_with_the_same_when(
    session: AsyncSession,
) -> None:
    doer, a, b = await members(session, 3)
    t0 = _t0()
    same_when = t0 + timedelta(seconds=30)
    # Both reporters give the same "When?" (b backdates by almost 2 h), but the scans are
    # 2 h 20 s apart, so they are separate acts.
    first = await scan_and_report(session, a, doer, at=t0, occurred_at=same_when)
    second = await scan_and_report(
        session, b, doer, at=t0 + timedelta(hours=2, seconds=20), occurred_at=same_when
    )
    assert first.occurred_at == second.occurred_at == same_when
    assert (await _group_of(session, first)).id != (await _group_of(session, second)).id


async def test_cap_holds_when_reporters_backdate(session: AsyncSession) -> None:
    doer = await make_member(session)
    witnesses = await members(session, 7)
    t0 = _t0()
    # The first reporter backdates by 2 h; the rest say "Just now" or backdate by varying
    # amounts. Grouping by "When?" would have split them into two acts and counted 6; by scan
    # time they are one act and only 5 count.
    offsets = [120, 0, 0, 15, 60, 0, 90]
    deeds = [
        await scan_and_report(
            session,
            w,
            doer,
            at=t0 + timedelta(minutes=i),
            east_m=i * 10,
            occurred_at=t0 + timedelta(minutes=i - offsets[i]),
        )
        for i, w in enumerate(witnesses)
    ]
    group = await _group_of(session, deeds[0])
    assert {(await _group_of(session, d)).id for d in deeds} == {group.id}
    assert [await _counts(session, d) for d in deeds] == [True] * 5 + [False] * 2
    assert (group.verified_count, group.counted_count) == (7, 5)
    total_counted = await session.scalar(
        select(func.count())
        .select_from(Deed)
        .where(Deed.doer_user_id == doer.user_id, Deed.counts_for_points.is_(True))
    )
    assert total_counted == 5


async def test_chained_drift_does_not_creep(session: AsyncSession) -> None:
    doer, a, b, c = await members(session, 4)
    t0 = _t0()
    deed_a = await scan_and_report(session, a, doer, at=t0)
    deed_b = await scan_and_report(session, b, doer, at=t0 + timedelta(minutes=1), north_m=150)
    deed_c = await scan_and_report(session, c, doer, at=t0 + timedelta(minutes=2), north_m=300)
    group = await _group_of(session, deed_a)
    assert (await _group_of(session, deed_b)).id == group.id
    assert (await _group_of(session, deed_c)).id != group.id


async def test_first_five_count_and_later_reports_are_linked(session: AsyncSession) -> None:
    doer = await make_member(session)
    witnesses = await members(session, 7)
    t0 = _t0()
    deeds = [
        await scan_and_report(session, w, doer, at=t0 + timedelta(minutes=i), east_m=i * 10)
        for i, w in enumerate(witnesses)
    ]
    assert [d.same_act.counts_for_points if d.same_act else None for d in deeds] == [
        True,
        True,
        True,
        True,
        True,
        False,
        False,
    ]
    group = await _group_of(session, deeds[0])
    assert {(await _group_of(session, d)).id for d in deeds} == {group.id}
    assert (group.verified_count, group.counted_count, group.distinct_reporters) == (7, 5, 7)
    assert group.cap == 5
    # Every report is verified and saved, counted or not.
    assert all(d.status == "verified" for d in deeds)


async def test_same_reporter_counts_once_per_act(session: AsyncSession) -> None:
    doer, witness = await members(session, 2)
    # 18:15 UTC = 23:45 IST on the 15th; 18:35 UTC = 00:05 IST on the 16th.
    first = await scan_and_report(
        session, witness, doer, at=datetime(2026, 11, 15, 18, 15, tzinfo=UTC)
    )
    second = await scan_and_report(
        session, witness, doer, at=datetime(2026, 11, 15, 18, 35, tzinfo=UTC)
    )
    group = await _group_of(session, first)
    assert (await _group_of(session, second)).id == group.id
    assert await _counts(session, first) is True
    assert await _counts(session, second) is False
    assert (group.verified_count, group.counted_count, group.distinct_reporters) == (2, 1, 1)


async def test_grouping_is_per_doer(session: AsyncSession) -> None:
    doer_1, doer_2, a, b = await members(session, 4)
    t0 = _t0()
    d1 = await scan_and_report(session, a, doer_1, at=t0)
    d2 = await scan_and_report(session, b, doer_2, at=t0)
    assert (await _group_of(session, d1)).id != (await _group_of(session, d2)).id


async def test_null_location_group_is_never_joined(session: AsyncSession) -> None:
    doer, invite_reporter, witness = await members(session, 3)
    version, limits = await load_limits(session)
    t0 = _t0()
    # What an invite deed without a location gets (B6 calls this on accept).
    lonely = await assign_group(
        session,
        doer_id=doer.user_id,
        reporter_id=invite_reporter.user_id,
        location=None,
        grouped_at=t0,
        limits=limits,
        limits_version=version,
    )
    assert lonely.group.centre is None
    assert lonely.counts_for_points is True
    assert lonely.group.verified_count == 1
    later = await scan_and_report(session, witness, doer, at=t0)
    assert (await _group_of(session, later)).id != lonely.group.id
    # And another location-less report gets a group of its own, too.
    other = await assign_group(
        session,
        doer_id=doer.user_id,
        reporter_id=witness.user_id,
        location=None,
        grouped_at=t0,
        limits=limits,
        limits_version=version,
    )
    assert other.group.id != lonely.group.id


async def test_config_change_does_not_alter_existing_groups(session: AsyncSession) -> None:
    doer = await make_member(session)
    witnesses = await members(session, 5)
    t0 = _t0()
    first = await scan_and_report(session, witnesses[0], doer, at=t0)
    old_group = await _group_of(session, first)
    old_version = old_group.limits_version

    await publish_limits(session, same_act_radius_m=1000, same_act_cap=2)
    # 500 m away: outside the old group's own 200 m radius, so a new group with the new values.
    # Scanned 90 minutes later, so the old group's anchor stays nearer in time for the reports
    # below.
    far = await scan_and_report(
        session, witnesses[1], doer, at=t0 + timedelta(minutes=90), north_m=500
    )
    new_group = await _group_of(session, far)
    assert new_group.id != old_group.id
    assert (new_group.radius_m, new_group.cap) == (1000, 2)
    assert new_group.limits_version > old_version

    # The old group keeps radius 200 and cap 5: three more reports of its act (same place,
    # scanned minutes after its anchor, so it is the nearest anchor) all count.
    extra = [
        await scan_and_report(session, w, doer, at=t0 + timedelta(minutes=2 + i), east_m=i)
        for i, w in enumerate(witnesses[2:])
    ]
    old_group = await _group_of(session, first)
    assert (old_group.radius_m, old_group.cap) == (200, 5)
    assert [await _counts(session, d) for d in extra] == [True, True, True]
    assert old_group.counted_count == 4


async def test_cap_and_radius_come_from_config(session: AsyncSession) -> None:
    doer = await make_member(session)
    witnesses = await members(session, 4)
    await publish_limits(session, same_act_cap=2, same_act_radius_m=600)
    t0 = _t0()
    deeds = [
        await scan_and_report(session, w, doer, at=t0 + timedelta(minutes=i), north_m=i * 180)
        for i, w in enumerate(witnesses)
    ]
    groups = {(await _group_of(session, d)).id for d in deeds}
    assert len(groups) == 1  # 540 m from the anchor is inside the 600 m radius
    assert [await _counts(session, d) for d in deeds] == [True, True, False, False]


async def test_nearest_anchor_in_time_wins(session: AsyncSession) -> None:
    doer, a, b, c = await members(session, 4)
    t0 = _t0()
    g1 = await _group_of(session, await scan_and_report(session, a, doer, at=t0))
    g2 = await _group_of(
        session, await scan_and_report(session, b, doer, at=t0 + timedelta(hours=2, minutes=30))
    )
    assert g1.id != g2.id
    # Scanned 1h50 after g1's anchor and 40 min before g2's: both match, g2 is nearer. The
    # backdated "When?" (nearer g1's anchor) is ignored (L7-A).
    deed = await scan_and_report(
        session,
        c,
        doer,
        at=t0 + timedelta(hours=1, minutes=50),
        occurred_at=t0 - timedelta(minutes=10),
    )
    assert (await _group_of(session, deed)).id == g2.id


async def test_group_rows_have_parameters_and_doer(session: AsyncSession) -> None:
    doer, a = await members(session, 2)
    deed = await scan_and_report(session, a, doer, at=_t0())
    group = await _group_of(session, deed)
    assert group.doer_user_id == doer.user_id
    assert (group.radius_m, group.window_minutes, group.cap) == (200, 120, 5)
    ids = (
        await session.scalars(
            select(SameActGroup.id).where(SameActGroup.doer_user_id == doer.user_id)
        )
    ).all()
    assert ids == [group.id]
