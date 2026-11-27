"""The network fan-out (docs/M6-DESIGN.md D110, §4.2; PO Q89): followers of the reporter or the
doer get one grouped row a day; visibility is checked before writing; network rows hold no deed;
caps per actor per day and per deed; batches resume from the cursor."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications import handlers
from app.modules.notifications._fanout import fanout_step
from app.modules.notifications._limits import load_notifications
from app.modules.notifications.models import (
    FanoutStatus,
    MuteTarget,
    Notification,
    NotificationCategory,
    NotificationFanout,
    NotificationKind,
    NotificationMute,
    NotificationPreference,
)
from tests.deeds.helpers import Member, members
from tests.feed.helpers import follow, set_post, verified_deed, withdraw_directly
from tests.notifications.helpers import publish_notifications, rename, rows_for
from tests.notifications.test_handlers import items_for, verified_event

K = NotificationKind


async def fanout_of(session: AsyncSession, deed_id: uuid.UUID) -> NotificationFanout | None:
    return await session.scalar(
        select(NotificationFanout)
        .where(NotificationFanout.deed_id == deed_id)
        .execution_options(populate_existing=True)
    )


async def verify(session: AsyncSession, reporter: Member, doer: Member) -> uuid.UUID:
    """A verified deed and its ``DeedVerified`` handled (recognition + fan-out recorded)."""
    deed_id = await verified_deed(session, reporter, doer)
    await handlers.on_deed_verified(session, await verified_event(session, deed_id))
    await session.flush()
    return deed_id


async def run_all(session: AsyncSession) -> int:
    """Run the job's batches until no fan-out is left running. Returns how many ran."""
    _, config = await load_notifications(session)
    ran = 0
    while await fanout_step(session, config=config, now=datetime.now(UTC)) is not None:
        ran += 1
        assert ran < 100
    return ran


async def network_rows(session: AsyncSession, member: Member) -> list[Notification]:
    return [r for r in await rows_for(session, member) if r.kind == K.NETWORK]


async def test_followers_of_reporter_or_doer_get_one_row_a_day(session: AsyncSession) -> None:
    reporter, doer, fan_r, fan_d, fan_both, stranger = await members(session, 6)
    await follow(session, fan_r, reporter)
    await follow(session, fan_d, doer)
    await follow(session, fan_both, reporter)
    await follow(session, fan_both, doer)
    deed_id = await verify(session, reporter, doer)
    fanout = await fanout_of(session, deed_id)
    assert fanout is not None and fanout.status == FanoutStatus.RUNNING
    assert sorted(fanout.actor_ids) == sorted([reporter.user_id, doer.user_id])
    await run_all(session)

    fanout = await fanout_of(session, deed_id)
    assert fanout is not None and fanout.status == FanoutStatus.DONE and fanout.recipients == 3
    for fan, actors in (
        (fan_r, {reporter.user_id}),
        (fan_d, {doer.user_id}),
        (fan_both, {reporter.user_id, doer.user_id}),
    ):
        [row] = await rows_for(session, fan)
        assert row.kind == K.NETWORK and row.category == NotificationCategory.NETWORK
        assert row.deed_id is None  # decision (b): links to Home, never "no longer available"
        assert set(row.actor_ids) == actors
    assert await rows_for(session, stranger) == []
    [item] = await items_for(session, fan_both)
    assert item.link is not None and item.link.kind == "home"
    assert item.text.endswith("you follow shared good deeds today.")
    [single] = await items_for(session, fan_r)
    assert single.text == f"{reporter.display_name}, who you follow, shared a good deed today."

    # A second deed the same day joins the same row.
    other = await members(session, 1)
    await verify(session, other[0], doer)
    await follow(session, fan_r, other[0])
    await verify(session, other[0], doer)
    await run_all(session)
    [row] = await rows_for(session, fan_r)
    assert row.actor_count == 2


async def test_the_deeds_own_people_get_about_you_rows_not_network(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    await follow(session, reporter, doer)
    await follow(session, doer, reporter)
    await verify(session, reporter, doer)
    await run_all(session)
    assert await network_rows(session, reporter) == []
    assert await network_rows(session, doer) == []
    assert [r.kind for r in await rows_for(session, doer)] == [K.RECOGNISED]


async def test_visibility_mutes_and_preferences_are_checked_before_writing(
    session: AsyncSession,
) -> None:
    reporter, doer, muter, off, fan = await members(session, 5)
    for person in (muter, off, fan):
        await follow(session, person, reporter)
    session.add(
        NotificationMute(
            user_id=muter.user_id, target_type=MuteTarget.PERSON, target_id=reporter.user_id
        )
    )
    session.add(
        NotificationPreference(
            user_id=off.user_id, category=NotificationCategory.NETWORK, enabled=False
        )
    )
    hidden = await verified_deed(session, reporter, doer)
    await set_post(session, hidden, hidden_by_doer_at=datetime.now(UTC))
    await handlers.on_deed_verified(session, await verified_event(session, hidden))
    await session.flush()
    await run_all(session)
    # Decision (a): a hidden deed is not visible to followers, so nobody is told.
    assert await rows_for(session, fan) == []

    await verify(session, reporter, doer)
    await run_all(session)
    assert len(await network_rows(session, fan)) == 1
    assert await rows_for(session, muter) == []
    assert await rows_for(session, off) == []


async def test_a_row_names_only_actors_with_a_deed_still_visible(session: AsyncSession) -> None:
    """SEC6-L1 (D109, Q93): the row holds no deed id, so one hidden deed doesn't turn it into
    "no longer available"; but an actor none of whose deeds the viewer can still see is no
    longer named, and with nobody left the row names no one."""
    ravi, priya, doer, fan = await members(session, 4)
    ravi, priya = await rename(session, ravi, "Ravi"), await rename(session, priya, "Priya")
    await follow(session, fan, ravi)
    await follow(session, fan, priya)
    ravi_first = await verify(session, ravi, doer)
    priya_deed = await verify(session, priya, doer)
    ravi_second = await verify(session, ravi, doer)
    await run_all(session)
    [row] = await network_rows(session, fan)
    assert row.deed_id is None and row.actor_count == 2
    assert {k: sorted(v) for k, v in row.data["deeds"].items()} == {
        str(ravi.user_id): sorted([str(ravi_first), str(ravi_second)]),
        str(priya.user_id): [str(priya_deed)],
    }

    # One of Ravi's deeds hidden: he still has one, so nothing changes.
    await set_post(session, ravi_first, hidden_by_reporter_at=datetime.now(UTC))
    [item] = await items_for(session, fan)
    assert item.actor_count == 2 and item.link is not None and item.link.kind == "home"
    # Both of Ravi's deeds gone (hidden, withdrawn): only Priya is named.
    await withdraw_directly(session, ravi_second)
    [item] = await items_for(session, fan)
    assert item.text == f"{priya.display_name}, who you follow, shared a good deed today."
    assert [a.handle for a in item.actors] == [priya.handle] and item.actor_count == 1
    assert ravi.display_name not in item.text and item.can_mute.person == priya.handle
    # Priya's deed hidden by its doer too: no names left.
    await set_post(session, priya_deed, hidden_by_doer_at=datetime.now(UTC))
    [item] = await items_for(session, fan)
    assert item.text == "This deed is no longer available"
    assert item.actors == [] and item.actor_count == 0 and item.link is None
    assert item.can_mute.person is None


async def test_an_actor_starts_at_most_three_fanouts_a_day(session: AsyncSession) -> None:
    reporter, doer, fan = await members(session, 3)
    await follow(session, fan, reporter)
    _, config = await load_notifications(session)
    cap = config.network.max_fanouts_per_actor_day
    deeds = [await verify(session, reporter, doer) for _ in range(cap + 1)]
    started = [await fanout_of(session, d) for d in deeds]
    assert all(f is not None for f in started[:cap])
    assert started[cap] is None
    # The cap is per actor: a new doer with the same (capped) reporter starts with the doer only.
    [new_doer] = await members(session, 1)
    fresh = await verify(session, reporter, new_doer)
    fanout = await fanout_of(session, fresh)
    assert fanout is not None and fanout.actor_ids == [new_doer.user_id]
    # Re-handling a started deed's event doesn't count twice.
    await handlers.on_deed_verified(session, await verified_event(session, deeds[0]))
    await session.flush()
    again = await fanout_of(session, deeds[0])
    assert again is not None


async def test_recipient_cap_and_resume_in_batches(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    fans = await members(session, 4)
    for fan in fans:
        await follow(session, fan, reporter)
    await publish_notifications(session, network={"fanout_batch": 1, "max_recipients_per_event": 3})
    deed_id = await verify(session, reporter, doer)
    _, config = await load_notifications(session)

    # One batch, then a "restart": the next step continues after the saved cursor.
    step = await fanout_step(session, config=config, now=datetime.now(UTC))
    assert step is not None and step.recipients == 1 and step.cursor is not None
    first_cursor = step.cursor
    assert await run_all(session) == 2
    fanout = await fanout_of(session, deed_id)
    assert fanout is not None
    assert fanout.status == FanoutStatus.CAPPED and fanout.recipients == 3
    assert fanout.finished_at is not None and fanout.cursor != first_cursor
    told = [f for f in fans if await network_rows(session, f)]
    assert len(told) == 3
    assert sorted(f.user_id for f in told) == sorted(f.user_id for f in fans)[:3]


async def test_audience_comes_from_config(session: AsyncSession) -> None:
    reporter, doer, one_way, mutual = await members(session, 4)
    await follow(session, one_way, reporter)
    await follow(session, mutual, reporter)
    await follow(session, reporter, mutual)
    await publish_notifications(session, network={"audience": "mutual", "roles": ["reporter"]})
    await verify(session, reporter, doer)
    await run_all(session)
    assert await network_rows(session, one_way) == []
    assert len(await network_rows(session, mutual)) == 1


async def test_network_daily_cap_is_one_row(session: AsyncSession) -> None:
    reporter, doer, other, fan = await members(session, 4)
    await follow(session, fan, reporter)
    await follow(session, fan, other)
    await verify(session, reporter, doer)
    await run_all(session)
    [row] = await network_rows(session, fan)
    row.read_at = datetime.now(UTC) + timedelta(seconds=1)
    await session.flush()
    await verify(session, other, doer)
    await run_all(session)
    # Read rows don't fold; the day's cap (1) stops a second row.
    assert len(await network_rows(session, fan)) == 1
