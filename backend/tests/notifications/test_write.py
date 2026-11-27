"""``_write.notify``: who is never notified, grouping, dedupe, caps, preferences and mutes
(docs/M6-DESIGN.md §4.2, D108, D109, D113, D115)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import UserStatus
from app.modules.notifications._write import Notice, Outcome, notify
from app.modules.notifications.models import (
    MuteTarget,
    NotificationCategory,
    NotificationDailyCount,
    NotificationKind,
    NotificationMute,
    NotificationPreference,
)
from tests.deeds.helpers import make_member, members
from tests.feed.helpers import verified_deed
from tests.notifications.helpers import publish_notifications, rows_for, send, set_status

K = NotificationKind


async def test_nobody_is_notified_about_their_own_action(session: AsyncSession) -> None:
    doer, reporter = await members(session, 2)
    deed = await verified_deed(session, reporter, doer)
    assert await send(session, K.LIKE, doer, actor=doer, deed_id=deed) == Outcome.SELF
    assert await rows_for(session, doer) == []


@pytest.mark.parametrize("status", [UserStatus.SUSPENDED, UserStatus.BLOCKED])
async def test_inactive_recipients_get_nothing(session: AsyncSession, status: UserStatus) -> None:
    doer, reporter = await members(session, 2)
    deed = await verified_deed(session, reporter, doer)
    await set_status(session, doer, status)
    outcome = await send(session, K.RECOGNISED, doer, actor=reporter, deed_id=deed)
    assert outcome == Outcome.INACTIVE
    assert await rows_for(session, doer) == []


async def test_likes_group_per_deed_with_newest_three_and_distinct_count(
    session: AsyncSession,
) -> None:
    doer, reporter, a, b, c, d = await members(session, 6)
    deed = await verified_deed(session, reporter, doer)
    t0 = datetime.now(UTC)
    # 5 likes from 4 people: ``a`` unlikes and likes again (one person counts once).
    for i, liker in enumerate([a, b, a, c, d]):
        await send(session, K.LIKE, doer, actor=liker, deed_id=deed, now=t0 + timedelta(seconds=i))
    rows = await rows_for(session, doer)
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_count == 4
    assert row.actor_ids == [d.user_id, c.user_id, b.user_id]
    assert row.group_key == f"like:{deed}"
    assert row.updated_at == t0 + timedelta(seconds=4)
    assert row.category == NotificationCategory.ACTIVITY


async def test_a_read_group_starts_a_new_row(session: AsyncSession) -> None:
    doer, reporter, a, b = await members(session, 4)
    deed = await verified_deed(session, reporter, doer)
    now = datetime.now(UTC)
    await send(session, K.LIKE, doer, actor=a, deed_id=deed, now=now)
    [first] = await rows_for(session, doer)
    first.read_at = now + timedelta(seconds=1)
    await session.flush()
    later = now + timedelta(seconds=2)
    # The same person again doesn't count twice, even after the row was read.
    assert await send(session, K.LIKE, doer, actor=a, deed_id=deed, now=later) == Outcome.DUPLICATE
    assert await send(session, K.LIKE, doer, actor=b, deed_id=deed, now=later) == Outcome.CREATED
    rows = await rows_for(session, doer)
    assert len(rows) == 2
    assert rows[0].actor_ids == [b.user_id] and rows[0].read_at is None
    assert rows[1].id == first.id


async def test_groups_are_per_deed_and_follows_per_day(session: AsyncSession) -> None:
    doer, reporter, a = await members(session, 3)
    deed1 = await verified_deed(session, reporter, doer)
    deed2 = await verified_deed(session, reporter, doer)
    await send(session, K.LIKE, doer, actor=a, deed_id=deed1)
    await send(session, K.LIKE, doer, actor=a, deed_id=deed2)
    await send(session, K.COMMENT, doer, actor=a, deed_id=deed1)
    assert len(await rows_for(session, doer)) == 3

    day1 = datetime(2026, 11, 20, 6, 0, tzinfo=UTC)
    target = await make_member(session)
    followers = await members(session, 3)
    await send(session, K.FOLLOW, target, actor=followers[0], now=day1)
    await send(session, K.FOLLOW, target, actor=followers[1], now=day1 + timedelta(hours=1))
    # 19:00 UTC is past midnight IST: the next day's group.
    await send(session, K.FOLLOW, target, actor=followers[2], now=day1 + timedelta(hours=13))
    rows = await rows_for(session, target)
    assert sorted(r.group_key or "" for r in rows) == ["follow:2026-11-20", "follow:2026-11-21"]
    assert sorted(r.actor_count for r in rows) == [1, 2]


async def test_single_rows_need_a_key_and_rerun_is_harmless(session: AsyncSession) -> None:
    doer, reporter = await members(session, 2)
    deed = await verified_deed(session, reporter, doer)
    key = f"recognised:{deed}"
    first = await send(session, K.RECOGNISED, doer, actor=reporter, deed_id=deed, dedupe_key=key)
    again = await send(session, K.RECOGNISED, doer, actor=reporter, deed_id=deed, dedupe_key=key)
    assert (first, again) == (Outcome.CREATED, Outcome.DUPLICATE)
    [row] = await rows_for(session, doer)
    assert row.actor_ids == [reporter.user_id] and row.actor_count == 1
    assert row.group_key is None and row.dedupe_key == key
    with pytest.raises(ValueError, match="dedupe_key"):
        await notify(session, Notice(kind=K.RECOGNISED, recipient_id=doer.user_id, deed_id=deed))
    with pytest.raises(ValueError, match="actor"):
        await notify(session, Notice(kind=K.LIKE, recipient_id=doer.user_id, deed_id=deed))


async def test_daily_activity_cap_stops_new_rows_but_groups_grow(session: AsyncSession) -> None:
    await publish_notifications(session, daily_caps={"activity": 2})
    doer, reporter, a, b, c = await members(session, 5)
    deeds = [await verified_deed(session, reporter, doer) for _ in range(3)]
    now = datetime.now(UTC)
    assert await send(session, K.LIKE, doer, actor=a, deed_id=deeds[0], now=now) == Outcome.CREATED
    assert await send(session, K.LIKE, doer, actor=a, deed_id=deeds[1], now=now) == Outcome.CREATED
    # The 3rd new row of the day is not created...
    assert await send(session, K.LIKE, doer, actor=a, deed_id=deeds[2], now=now) == Outcome.CAPPED
    # ...but existing groups still grow.
    assert await send(session, K.LIKE, doer, actor=b, deed_id=deeds[0], now=now) == Outcome.GROUPED
    rows = await rows_for(session, doer)
    assert len(rows) == 2
    # A capped actor wasn't counted, so they still count once the cap allows (next day).
    tomorrow = now + timedelta(days=1)
    outcome = await send(session, K.LIKE, doer, actor=a, deed_id=deeds[2], now=tomorrow)
    assert outcome == Outcome.CREATED
    # About you and Account & safety have no cap.
    for _ in range(3):
        assert await send(session, K.RECOGNISED, doer, actor=c, deed_id=deeds[0]) == (
            Outcome.CREATED
        )
        assert await send(session, K.BADGE_AWARDED, doer) == Outcome.CREATED
    counts = list(
        await session.scalars(
            select(NotificationDailyCount.count).where(
                NotificationDailyCount.user_id == doer.user_id,
                NotificationDailyCount.category == NotificationCategory.ACTIVITY,
            )
        )
    )
    assert sorted(counts) == [1, 2]


async def test_network_rows_are_one_a_day(session: AsyncSession) -> None:
    viewer, a, b, other = await members(session, 4)
    deed_a = await verified_deed(session, a, other)
    deed_b = await verified_deed(session, b, other)
    now = datetime.now(UTC)
    assert await send(session, K.NETWORK, viewer, actor=a, deed_id=deed_a, now=now) == (
        Outcome.CREATED
    )
    [row] = await rows_for(session, viewer)
    row.read_at = now + timedelta(seconds=1)
    await session.flush()
    later = now + timedelta(seconds=2)
    # Read, so a new row would be needed: the network cap (1 a day) stops it.
    assert await send(session, K.NETWORK, viewer, actor=b, deed_id=deed_b, now=later) == (
        Outcome.CAPPED
    )
    assert len(await rows_for(session, viewer)) == 1


async def test_preferences_switch_categories_off_except_locked(session: AsyncSession) -> None:
    doer, reporter, a = await members(session, 3)
    deed = await verified_deed(session, reporter, doer)
    session.add_all(
        [
            NotificationPreference(
                user_id=doer.user_id, category=NotificationCategory.ACTIVITY, enabled=False
            ),
            # A row for a locked category (written directly) is ignored: always on.
            NotificationPreference(
                user_id=doer.user_id, category=NotificationCategory.ACCOUNT, enabled=False
            ),
        ]
    )
    await session.flush()
    assert await send(session, K.LIKE, doer, actor=a, deed_id=deed) == Outcome.CATEGORY_OFF
    assert await send(session, K.BADGE_AWARDED, doer) == Outcome.CREATED
    assert await send(session, K.RECOGNISED, doer, actor=reporter, deed_id=deed) == (
        Outcome.CREATED
    )


async def test_config_default_off_applies_without_a_choice(session: AsyncSession) -> None:
    await publish_notifications(
        session, categories={"network": {"default": False, "locked": False}}
    )
    viewer, a, other = await members(session, 3)
    deed = await verified_deed(session, a, other)
    assert await send(session, K.NETWORK, viewer, actor=a, deed_id=deed) == Outcome.CATEGORY_OFF


async def test_mutes(session: AsyncSession) -> None:
    doer, reporter, a, b = await members(session, 4)
    deed = await verified_deed(session, reporter, doer)
    other_deed = await verified_deed(session, reporter, doer)
    session.add_all(
        [
            NotificationMute(
                user_id=doer.user_id, target_type=MuteTarget.PERSON, target_id=reporter.user_id
            ),
            NotificationMute(user_id=doer.user_id, target_type=MuteTarget.DEED, target_id=deed),
        ]
    )
    await session.flush()
    # A muted person: their activity stops, recognitions of you still arrive (D113).
    assert await send(session, K.LIKE, doer, actor=reporter, deed_id=other_deed) == Outcome.MUTED
    assert await send(session, K.FOLLOW, doer, actor=reporter) == Outcome.MUTED
    assert await send(session, K.RECOGNISED, doer, actor=reporter, deed_id=deed) == (
        Outcome.CREATED
    )
    # A muted deed: its likes, comments and replies stop, from anyone.
    for kind in (K.LIKE, K.COMMENT, K.REPLY):
        assert await send(session, kind, doer, actor=a, deed_id=deed) == Outcome.MUTED
    assert await send(session, K.LIKE, doer, actor=b, deed_id=other_deed) == Outcome.CREATED


async def test_unknown_group_deed_is_refused() -> None:
    from app.modules.notifications._catalogue import group_key

    with pytest.raises(ValueError, match="deed"):
        group_key(K.COMMENT, None, datetime.now(UTC).date())
    assert group_key(K.BADGE_AWARDED, uuid.uuid4(), datetime.now(UTC).date()) is None
