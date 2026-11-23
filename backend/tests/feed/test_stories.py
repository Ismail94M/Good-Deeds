"""Good Deeds Stories (PO 2026-11-23, docs/M3-DESIGN.md D73, migration 0024).

A story is a listed feed post verified in the last ``story_window_hours``, shown in the reporter's
ring (``reported``) and the doer's ring (``verified``), for the caller's network: the caller, the
people they follow and their followers. Rings carry counts only; a person's stories carry photos.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import publish
from app.modules.feed import _stories
from app.modules.feed._limits import FeedLimits, load_limits
from app.modules.feed.models import PostState, StoryRole, StoryView
from app.modules.identity.models import User, UserStatus
from tests.deeds.helpers import Member, members
from tests.feed.conftest import FakePhotos
from tests.feed.helpers import follow, set_post, verified_deed, withdraw_directly

RINGS = "/api/feed/stories"
SEEN = "/api/feed/stories/seen"


def _ago(hours: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


async def _publish_feed(session: AsyncSession, **changes: Any) -> None:
    _, current = await load_limits(session)
    new = FeedLimits.model_validate({**current.model_dump(), **changes})
    await publish(session, new, actor_id=None, reason="test")
    await session.commit()


async def _rings(client: AsyncClient, who: Member) -> dict[str, Any]:
    res = await client.get(RINGS, headers=who.headers)
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


def _by_handle(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {ring["user"]["handle"]: ring for ring in body["items"]}


async def test_rings_cover_my_network_in_the_window_counts_only(
    client: AsyncClient, session: AsyncSession
) -> None:
    me, followed, follower, stranger, other = await members(session, 5)
    await follow(session, me, followed)
    await follow(session, follower, me)
    # followed witnessed `other` (blue); follower's own deed was verified by stranger (green).
    await verified_deed(session, followed, other, verified_at=_ago(2))
    await verified_deed(session, stranger, follower, verified_at=_ago(1))
    # My own report.
    await verified_deed(session, me, other, verified_at=_ago(5))
    # Outside the network: nobody I follow or who follows me took part.
    await verified_deed(session, stranger, other, verified_at=_ago(1))
    # Outside the 24-hour window.
    await verified_deed(session, followed, other, verified_at=_ago(25))
    await session.commit()

    body = await _rings(client, me)
    assert body["seconds_per_story"] == 5 and body["window_hours"] == 24
    handles = [ring["user"]["handle"] for ring in body["items"]]
    # Me first, then the rest newest first.
    assert handles == [me.handle, follower.handle, followed.handle]
    rings = _by_handle(body)
    assert rings[me.handle]["is_me"] is True
    assert (rings[followed.handle]["reported_count"], rings[followed.handle]["verified_count"]) == (
        1,
        0,
    )
    assert (rings[follower.handle]["reported_count"], rings[follower.handle]["verified_count"]) == (
        0,
        1,
    )
    assert all(ring["unseen_count"] == 1 for ring in body["items"])
    # Counts only: no photos or deed content until a ring is opened.
    assert set(body["items"][0]) == {
        "user",
        "is_me",
        "reported_count",
        "verified_count",
        "unseen_count",
        "latest_at",
    }


async def test_one_deed_is_a_story_in_both_rings_and_never_copied(
    client: AsyncClient, session: AsyncSession, photos: FakePhotos
) -> None:
    me, shiva, rahul = await members(session, 3)
    await follow(session, me, shiva)
    await follow(session, me, rahul)
    deed_id = await verified_deed(session, shiva, rahul, verified_at=_ago(1))
    photos.add(deed_id)
    await session.commit()

    rings = _by_handle(await _rings(client, me))
    assert rings[shiva.handle]["reported_count"] == 1
    assert rings[rahul.handle]["verified_count"] == 1

    blue = (await client.get(f"{RINGS}/{shiva.handle}", headers=me.headers)).json()
    green = (await client.get(f"{RINGS}/{rahul.handle}", headers=me.headers)).json()
    assert [(s["role"], s["post"]["deed_id"]) for s in blue["items"]] == [
        ("reported", str(deed_id))
    ]
    assert [(s["role"], s["post"]["deed_id"]) for s in green["items"]] == [
        ("verified", str(deed_id))
    ]
    post = blue["items"][0]["post"]
    assert post["reporter"]["handle"] == shiva.handle and post["doer"]["handle"] == rahul.handle
    assert len(post["photos"]) == 1 and post["photos"][0]["display_url"]
    assert blue["seconds_per_story"] == 5 and blue["is_me"] is False


async def test_a_persons_stories_are_oldest_first_and_capped(
    client: AsyncClient, session: AsyncSession
) -> None:
    me, friend, a, b, c = await members(session, 5)
    await follow(session, me, friend)
    first = await verified_deed(session, friend, a, verified_at=_ago(20))
    second = await verified_deed(session, b, friend, verified_at=_ago(10))
    third = await verified_deed(session, friend, c, verified_at=_ago(1))
    await session.commit()

    items = (await client.get(f"{RINGS}/{friend.handle}", headers=me.headers)).json()["items"]
    assert [(s["post"]["deed_id"], s["role"]) for s in items] == [
        (str(first), "reported"),
        (str(second), "verified"),
        (str(third), "reported"),
    ]
    assert not any(s["seen"] for s in items)

    await _publish_feed(session, story_max_per_person=2)
    items = (await client.get(f"{RINGS}/{friend.handle}", headers=me.headers)).json()["items"]
    # The newest two, still oldest first.
    assert [s["post"]["deed_id"] for s in items] == [str(second), str(third)]


async def test_hidden_withdrawn_and_removed_deeds_leave_stories_at_once(
    client: AsyncClient, session: AsyncSession
) -> None:
    me, friend, a, b, c = await members(session, 5)
    await follow(session, me, friend)
    hidden = await verified_deed(session, friend, a, verified_at=_ago(3))
    withdrawn = await verified_deed(session, friend, b, verified_at=_ago(2))
    removed = await verified_deed(session, friend, c, verified_at=_ago(1))
    kept = await verified_deed(session, a, friend, verified_at=_ago(1))
    await set_post(session, hidden, hidden_by_doer_at=datetime.now(UTC))
    await set_post(session, removed, state=PostState.REMOVED)
    # Withdrawn before the feed handler ran: the post is still listed, the live check drops it.
    await withdraw_directly(session, withdrawn)
    await session.commit()

    items = (await client.get(f"{RINGS}/{friend.handle}", headers=me.headers)).json()["items"]
    assert [s["post"]["deed_id"] for s in items] == [str(kept)]
    ring = _by_handle(await _rings(client, me))[friend.handle]
    # Not yet synced, so the withdrawn one is still counted in the ring until its handler runs.
    assert (ring["reported_count"], ring["verified_count"]) == (1, 1)

    for deed_id in (hidden, removed):
        res = await client.put(
            SEEN, json={"deed_id": str(deed_id), "role": "reported"}, headers=me.headers
        )
        assert (res.status_code, res.json()["code"]) == (404, "deed_not_found")


async def test_people_outside_my_network_or_inactive_have_no_stories_for_me(
    client: AsyncClient, session: AsyncSession
) -> None:
    me, stranger, friend, other = await members(session, 4)
    await follow(session, me, friend)
    await verified_deed(session, stranger, other, verified_at=_ago(1))
    await verified_deed(session, friend, other, verified_at=_ago(1))
    await session.commit()

    res = await client.get(f"{RINGS}/{stranger.handle}", headers=me.headers)
    assert (res.status_code, res.json()["code"]) == (404, "user_not_found")
    res = await client.get(f"{RINGS}/nobody-by-that-name", headers=me.headers)
    assert (res.status_code, res.json()["code"]) == (404, "user_not_found")

    await session.execute(
        update(User).where(User.id == friend.user_id).values(status=UserStatus.SUSPENDED)
    )
    await session.commit()
    assert friend.handle not in _by_handle(await _rings(client, me))
    res = await client.get(f"{RINGS}/{friend.handle}", headers=me.headers)
    assert res.status_code == 404


async def test_watched_stories_turn_the_ring_grey_and_move_it_back(
    client: AsyncClient, session: AsyncSession
) -> None:
    me, newer, older, other = await members(session, 4)
    await follow(session, me, newer)
    await follow(session, me, older)
    watched = await verified_deed(session, newer, other, verified_at=_ago(1))
    await verified_deed(session, older, other, verified_at=_ago(3))
    await session.commit()
    assert [r["user"]["handle"] for r in (await _rings(client, me))["items"]] == [
        newer.handle,
        older.handle,
    ]

    for _ in range(2):  # idempotent
        res = await client.put(
            SEEN, json={"deed_id": str(watched), "role": "reported"}, headers=me.headers
        )
        assert res.status_code == 204
    body = await _rings(client, me)
    # Unwatched rings come first.
    assert [(r["user"]["handle"], r["unseen_count"]) for r in body["items"]] == [
        (older.handle, 1),
        (newer.handle, 0),
    ]
    items = (await client.get(f"{RINGS}/{newer.handle}", headers=me.headers)).json()["items"]
    assert items[0]["seen"] is True
    # Watching it in one ring doesn't mark it watched in the doer's ring.
    count = await session.scalar(
        select(func.count()).select_from(StoryView).where(StoryView.viewer_id == me.user_id)
    )
    assert count == 1


async def test_seen_needs_login_and_a_valid_role(
    client: AsyncClient, session: AsyncSession
) -> None:
    me, a, b = await members(session, 3)
    deed_id = await verified_deed(session, a, b, verified_at=_ago(1))
    await session.commit()
    body = {"deed_id": str(deed_id), "role": "reported"}
    assert (await client.put(SEEN, json=body)).status_code == 401
    assert (await client.get(RINGS)).status_code == 401
    bad = await client.put(SEEN, json={**body, "role": "liked"}, headers=me.headers)
    assert bad.status_code == 422


async def test_window_follows_config(client: AsyncClient, session: AsyncSession) -> None:
    me, friend, other = await members(session, 3)
    await follow(session, me, friend)
    await verified_deed(session, friend, other, verified_at=_ago(30))
    await session.commit()
    assert (await _rings(client, me))["items"] == []
    await _publish_feed(session, story_window_hours=48)
    body = await _rings(client, me)
    assert body["window_hours"] == 48 and len(body["items"]) == 1


async def test_prune_deletes_only_old_views(session: AsyncSession) -> None:
    me, a, b = await members(session, 3)
    old = await verified_deed(session, a, b, verified_at=_ago(100))
    new = await verified_deed(session, b, a, verified_at=_ago(1))
    await _stories.mark_seen(session, me.user_id, old, StoryRole.REPORTED, now=_ago(60))
    await _stories.mark_seen(session, me.user_id, new, StoryRole.VERIFIED)
    _, limits = await load_limits(session)
    assert await _stories.prune_views_batch(session, limits) == 1
    left = list(await session.scalars(select(StoryView.deed_id)))
    assert left == [new]
