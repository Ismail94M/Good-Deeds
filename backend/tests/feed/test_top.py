"""Top snapshots per period (docs/M3-DESIGN.md §3.4 #1 "top", Q26, Q43; test plan §8 "Feed" Top,
"Config").

Snapshots are computed with an explicit ``now`` far in the future (2091), so the windows hold only
this test's posts, whatever other test files committed.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import publish
from app.modules.deeds.models import Deed, SameActGroup
from app.modules.feed import _top
from app.modules.feed._limits import FeedLimits, load_limits
from app.modules.feed.models import FeedTopEntry, FeedTopSnapshot, TopPeriod
from tests.deeds.helpers import Member, members
from tests.feed.helpers import set_post, verified_deed, withdraw_directly

NOW = datetime(2091, 6, 1, 12, 0, tzinfo=UTC)


async def publish_feed(session: AsyncSession, **changes: Any) -> FeedLimits:
    _, current = await load_limits(session)
    new = FeedLimits.model_validate({**current.model_dump(), **changes})
    await publish(session, new, actor_id=None, reason="test")
    await session.commit()
    return new


async def _limits(session: AsyncSession) -> FeedLimits:
    return (await load_limits(session))[1]


async def _ranking(session: AsyncSession, snapshot: FeedTopSnapshot) -> list[tuple[Any, int]]:
    rows = await session.execute(
        select(FeedTopEntry.deed_id, FeedTopEntry.score)
        .where(FeedTopEntry.snapshot_id == snapshot.id)
        .order_by(FeedTopEntry.rank)
    )
    return [(r[0], r[1]) for r in rows]


async def _post(
    session: AsyncSession,
    people: tuple[Member, Member],
    *,
    days_ago: float,
    likes: int = 0,
    top_likes: int | None = None,
    comments: int = 0,
) -> Any:
    deed_id = await verified_deed(
        session, people[0], people[1], verified_at=NOW - timedelta(days=days_ago)
    )
    await set_post(
        session,
        deed_id,
        like_count=likes,
        top_like_count=likes if top_likes is None else top_likes,
        comment_count=comments,
    )
    return deed_id


async def test_score_uses_top_likes_and_comments_with_config_weights(
    session: AsyncSession,
) -> None:
    reporter, doer = await members(session, 2)
    people = (reporter, doer)
    many_likes = await _post(session, people, days_ago=1, likes=10)  # 10
    many_comments = await _post(session, people, days_ago=1, comments=4)  # 12
    # Self-likes (Q43): like_count 50, but only 1 like from others counts.
    self_liked = await _post(session, people, days_ago=1, likes=50, top_likes=1)  # 1
    limits = await _limits(session)
    for period in TopPeriod:
        snap = await _top.compute_snapshot(session, period, limits, now=NOW)
        assert await _ranking(session, snap) == [
            (many_comments, 12),
            (many_likes, 10),
            (self_liked, 1),
        ]
        assert snap.weights == {"like": 1, "comment": 3}

    # Weights 2 / 5 change the order in every period, without a code change.
    limits = await publish_feed(session, top_like_weight=5, top_comment_weight=2)
    for period in TopPeriod:
        snap = await _top.compute_snapshot(session, period, limits, now=NOW + timedelta(minutes=1))
        assert await _ranking(session, snap) == [
            (many_likes, 50),
            (many_comments, 8),
            (self_liked, 5),
        ]


@pytest.mark.parametrize(
    ("days_ago", "in_periods"),
    [
        (6, {"week", "month", "year"}),
        (8, {"month", "year"}),
        (29, {"month", "year"}),
        (31, {"year"}),
        (364, {"year"}),
        (366, set()),
    ],
)
async def test_rolling_windows(session: AsyncSession, days_ago: int, in_periods: set[str]) -> None:
    people = tuple(await members(session, 2))
    deed_id = await _post(session, (people[0], people[1]), days_ago=days_ago, likes=1)
    limits = await _limits(session)
    found = set()
    for period in TopPeriod:
        snap = await _top.compute_snapshot(session, period, limits, now=NOW)
        if deed_id in {d for d, _ in await _ranking(session, snap)}:
            found.add(period.value)
        assert snap.window_start == NOW - timedelta(days=limits.top_days(period.value))
    assert found == in_periods


async def test_old_deed_with_new_likes_is_not_in_week(session: AsyncSession) -> None:
    """Total counts are used, but a deed published before the window never appears."""
    reporter, doer = await members(session, 2)
    old_popular = await _post(session, (reporter, doer), days_ago=10, likes=30)
    limits = await _limits(session)
    week = await _top.compute_snapshot(session, TopPeriod.WEEK, limits, now=NOW)
    month = await _top.compute_snapshot(session, TopPeriod.MONTH, limits, now=NOW)
    year = await _top.compute_snapshot(session, TopPeriod.YEAR, limits, now=NOW)
    assert old_popular not in {d for d, _ in await _ranking(session, week)}
    assert (old_popular, 30) in await _ranking(session, month)
    assert (old_popular, 30) in await _ranking(session, year)


async def test_window_follows_config(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    deed_id = await _post(session, (reporter, doer), days_ago=20, likes=1)
    limits = await publish_feed(session, top_month_days=14)
    snap = await _top.compute_snapshot(session, TopPeriod.MONTH, limits, now=NOW)
    assert deed_id not in {d for d, _ in await _ranking(session, snap)}


async def test_ties_newer_deed_first_then_deed_id(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    older = await _post(session, (reporter, doer), days_ago=2, likes=3)
    newer = await _post(session, (reporter, doer), days_ago=1, likes=3)
    same_a = await _post(session, (reporter, doer), days_ago=3, likes=3)
    same_b = await _post(session, (reporter, doer), days_ago=3, likes=3)
    limits = await _limits(session)
    expected = [newer, older, *sorted([same_a, same_b], reverse=True)]
    for period in TopPeriod:
        snap = await _top.compute_snapshot(session, period, limits, now=NOW)
        assert [d for d, _ in await _ranking(session, snap)] == expected


async def test_size_limit_and_hidden_posts_are_not_ranked(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    ids = [await _post(session, (reporter, doer), days_ago=1, likes=i) for i in range(5)]
    await set_post(session, ids[4], hidden_by_doer_at=NOW)
    limits = await publish_feed(session, top_size=3, top_badge_ranks=3)
    snap = await _top.compute_snapshot(session, TopPeriod.WEEK, limits, now=NOW)
    assert [d for d, _ in await _ranking(session, snap)] == [ids[3], ids[2], ids[1]]


async def test_keeps_only_the_newest_two_snapshots_per_period(session: AsyncSession) -> None:
    limits = await _limits(session)
    week = [
        await _top.compute_snapshot(session, TopPeriod.WEEK, limits, now=NOW + timedelta(minutes=i))
        for i in range(4)
    ]
    month = await _top.compute_snapshot(session, TopPeriod.MONTH, limits, now=NOW)
    kept = set(
        await session.scalars(select(FeedTopSnapshot.id).where(FeedTopSnapshot.computed_at >= NOW))
    )
    assert kept == {week[2].id, week[3].id, month.id}


async def test_refresh_due_follows_each_periods_interval(session: AsyncSession) -> None:
    limits = await publish_feed(session, top_week_refresh_minutes=10, top_month_refresh_minutes=30)
    assert await _top.refresh_due(session, limits, now=NOW) == list(TopPeriod)
    assert await _top.refresh_due(session, limits, now=NOW + timedelta(minutes=9)) == []
    assert await _top.refresh_due(session, limits, now=NOW + timedelta(minutes=10)) == [
        TopPeriod.WEEK
    ]
    later = NOW + timedelta(minutes=30)
    assert await _top.refresh_due(session, limits, now=later) == [TopPeriod.WEEK, TopPeriod.MONTH]
    assert await _top.refresh_due(session, limits, now=NOW + timedelta(minutes=60)) == list(
        TopPeriod
    )


async def test_snapshots_never_touch_deeds(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    deed_id = await _post(session, (reporter, doer), days_ago=1, likes=5, comments=2)

    async def snapshot() -> tuple[Any, ...]:
        deed = (
            await session.scalars(
                select(Deed).where(Deed.id == deed_id).execution_options(populate_existing=True)
            )
        ).one()
        group = await session.get(SameActGroup, deed.same_act_group_id, populate_existing=True)
        assert group is not None
        return (
            deed.status,
            deed.counts_for_points,
            deed.prize_rank_eligible,
            deed.updated_at,
            group.verified_count,
            group.counted_count,
        )

    before = await snapshot()
    limits = await publish_feed(session, top_like_weight=4, top_week_days=3)
    for period in TopPeriod:
        await _top.compute_snapshot(session, period, limits, now=NOW)
    assert await snapshot() == before


# --------------------------------------------------- reading Top through the API


async def _top_page(client: AsyncClient, who: Member, query: str = "", expect: int = 200) -> Any:
    response = await client.get(f"/api/feed?tab=top{query}", headers=who.headers)
    assert response.status_code == expect, response.text
    return response.json()


async def test_top_tab_reads_the_newest_snapshot_and_pages_by_rank(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, viewer = await members(session, 3)
    now = datetime.now(UTC)
    ids = []
    for i in range(5):
        deed_id = await verified_deed(
            session, reporter, doer, verified_at=now - timedelta(hours=1, seconds=i)
        )
        await set_post(session, deed_id, like_count=1000 + i, top_like_count=1000 + i)
        ids.append(deed_id)
    limits = await _limits(session)
    for period in TopPeriod:
        await _top.compute_snapshot(session, period, limits, now=now)
    await session.commit()

    first = await _top_page(client, viewer, "&limit=2")  # period missing -> week
    assert [i["deed_id"] for i in first["items"]] == [str(ids[4]), str(ids[3])]
    assert [i["rank"] for i in first["items"]] == [1, 2]
    month_page = await _top_page(client, viewer, "&period=month&limit=2")
    assert [i["rank"] for i in month_page["items"]] == [1, 2]

    # A week cursor sent with period=month is refused.
    wrong = await _top_page(client, viewer, f"&period=month&cursor={first['next_cursor']}", 400)
    assert wrong["code"] == "invalid_cursor"

    # Hidden and withdrawn posts disappear at once, even inside the snapshot.
    await set_post(session, ids[2], hidden_by_doer_at=now)
    await withdraw_directly(session, ids[1])
    # A new snapshot computed meanwhile: the reader finishes on the old one.
    await _top.compute_snapshot(session, TopPeriod.WEEK, limits, now=now + timedelta(minutes=1))
    await session.commit()
    second = await _top_page(client, viewer, f"&period=week&limit=2&cursor={first['next_cursor']}")
    # Rank 5 comes next (3 hidden, 4 withdrawn). Posts committed by other test files may follow
    # it at lower ranks, so only the first item is checked.
    assert (second["items"][0]["deed_id"], second["items"][0]["rank"]) == (str(ids[0]), 5)
    assert {str(ids[1]), str(ids[2])}.isdisjoint(i["deed_id"] for i in second["items"])

    # Two more refreshes prune that snapshot: its cursor no longer works.
    for minutes in (2, 3):
        await _top.compute_snapshot(
            session, TopPeriod.WEEK, limits, now=now + timedelta(minutes=minutes)
        )
    await session.commit()
    stale = await _top_page(client, viewer, f"&limit=2&cursor={first['next_cursor']}", 400)
    assert stale["code"] == "invalid_cursor"


async def test_top_tab_without_a_snapshot_is_empty(
    client: AsyncClient, session: AsyncSession
) -> None:
    (viewer,) = await members(session, 1)
    count = await session.scalar(
        select(func.count()).select_from(FeedTopSnapshot).where(FeedTopSnapshot.period == "year")
    )
    if count:
        pytest.skip("another test committed a year snapshot")
    body = await _top_page(client, viewer, "&period=year")
    assert body == {"items": [], "next_cursor": None}
