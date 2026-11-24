"""The feed projection and ``feed.service`` (docs/M3-DESIGN.md §5, D37; test plan §8 "Feed",
"Events")."""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import handlers_for
from app.modules.deeds.models import Deed, DeedStatus
from app.modules.feed import service as feed
from app.modules.feed._projection import sync_post
from app.modules.feed.models import FeedPost, PostState
from tests.deeds.helpers import members, scan_and_report
from tests.deeds.invite_helpers import accept, create_invite, withdraw
from tests.feed.helpers import (
    feed_events,
    post_of,
    run_feed_handlers,
    set_post,
    verified_deed,
    withdraw_directly,
)


async def _nearby_ids(client: AsyncClient, headers: dict[str, str]) -> list[str]:
    response = await client.get("/api/feed?tab=nearby", headers=headers)
    assert response.status_code == 200, response.text
    return [item["deed_id"] for item in response.json()["items"]]


def test_feed_subscribes_to_exactly_the_three_deed_events() -> None:
    def feed_handlers(event_type: str) -> list[str]:
        return [n for n in handlers_for(event_type) if n.startswith("app.modules.feed.")]

    for event_type in ("deeds.deed_verified", "deeds.deed_withdrawn", "deeds.deed_rejected"):
        assert len(feed_handlers(event_type)) == 1, event_type
    for event_type in (
        "deeds.deed_submitted",
        "deeds.deed_edited",
        "deeds.prize_rank_changed",
        "deeds.counts_for_points_changed",
    ):
        assert feed_handlers(event_type) == [], event_type


async def test_qr_deed_is_published_when_its_handler_runs(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer, viewer = await members(session, 3)
    deed = await scan_and_report(session, witness, doer)
    await session.commit()
    assert await post_of(session, deed.id) is None
    assert str(deed.id) not in await _nearby_ids(client, viewer.headers)

    events = await feed_events(session, deed.id)
    assert [e.event_type for e in events] == ["deeds.deed_verified"]
    assert await run_feed_handlers(session, events) == 1
    await session.commit()
    post = await post_of(session, deed.id)
    assert post is not None and post.state == PostState.PUBLISHED and post.listed
    assert post.published_at == deed.verified_at
    assert (post.doer_id, post.reporter_id) == (doer.user_id, witness.user_id)
    assert (post.like_count, post.top_like_count, post.comment_count) == (0, 0, 0)
    assert str(deed.id) in await _nearby_ids(client, viewer.headers)

    # Re-running is a no-op (processed_events), and so is calling sync_post again.
    assert await run_feed_handlers(session, events) == 0
    assert await sync_post(session, deed.id) == PostState.PUBLISHED
    count = await session.scalar(select(FeedPost.deed_id).where(FeedPost.deed_id == deed.id))
    assert count == deed.id


async def test_withdrawn_before_verified_handler_leaves_no_post_or_withdrawn(
    session: AsyncSession,
) -> None:
    """Handlers in any order: the result follows the deed's current status."""
    # Case 1: withdrawn before either handler ran -> no post ever.
    witness, doer = await members(session, 2)
    deed = await scan_and_report(session, witness, doer)
    await withdraw(session, witness, deed.id)
    events = await feed_events(session, deed.id)
    assert [e.event_type for e in events] == ["deeds.deed_verified", "deeds.deed_withdrawn"]
    await run_feed_handlers(session, list(reversed(events)))
    assert await post_of(session, deed.id) is None

    # Case 2: published, then withdrawn; the withdrawn handler runs, then verified again.
    w2, d2 = await members(session, 2)
    deed2 = await scan_and_report(session, w2, d2)
    verified = await feed_events(session, deed2.id)
    await run_feed_handlers(session, verified)
    await withdraw(session, w2, deed2.id)
    withdrawn = [e for e in await feed_events(session, deed2.id) if e not in verified]
    await run_feed_handlers(session, withdrawn)
    post = await post_of(session, deed2.id)
    assert post is not None and post.state == PostState.WITHDRAWN and not post.listed
    assert await sync_post(session, deed2.id) == PostState.WITHDRAWN


async def test_terminal_state_is_never_republished(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    deed_id = await verified_deed(session, reporter, doer)
    await set_post(session, deed_id, state=PostState.REMOVED)
    assert await sync_post(session, deed_id) == PostState.REMOVED
    await set_post(session, deed_id, state=PostState.HELD)
    assert await sync_post(session, deed_id) == PostState.HELD  # M5 flags stay


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (DeedStatus.REJECTED, PostState.REJECTED),
        (DeedStatus.UNVERIFIED, PostState.REJECTED),
        (DeedStatus.REMOVED, PostState.REMOVED),
    ],
)
async def test_rejected_and_removed_deeds_leave_the_feed(
    session: AsyncSession, status: DeedStatus, expected: PostState
) -> None:
    reporter, doer = await members(session, 2)
    deed_id = await verified_deed(session, reporter, doer)
    deed = await session.get(Deed, deed_id)
    assert deed is not None
    deed.status = status
    await session.flush()
    assert await sync_post(session, deed_id) == expected


@pytest.mark.usefixtures("invites_on")
async def test_pending_and_expired_invite_deeds_never_get_a_post(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    pending, _ = await create_invite(session, reporter)
    assert await sync_post(session, pending.id) is None
    deed = await session.get(Deed, pending.id)
    assert deed is not None
    deed.status = DeedStatus.UNVERIFIED  # expired
    await session.flush()
    assert await sync_post(session, pending.id) is None
    assert await post_of(session, pending.id) is None
    # An accepted invite is published like a QR deed.
    other, token = await create_invite(session, reporter)
    await accept(session, token, doer)
    await run_feed_handlers(session, await feed_events(session, other.id))
    post = await post_of(session, other.id)
    assert post is not None and post.state == PostState.PUBLISHED


async def test_sync_of_unknown_deed_does_nothing(session: AsyncSession) -> None:
    assert await sync_post(session, uuid.uuid4()) is None


# --------------------------------------------------- feed.service


async def test_service_visibility_checks(session: AsyncSession) -> None:
    reporter, doer, stranger = await members(session, 3)
    deed_id = await verified_deed(session, reporter, doer)
    assert await feed.is_public(session, deed_id)
    assert await feed.can_view(session, deed_id, stranger.user_id)
    view = await feed.visibility(session, deed_id)
    assert view is not None and view.listed and view.state == PostState.PUBLISHED

    await set_post(session, deed_id, hidden_by_doer_at=datetime.now(UTC))
    assert not await feed.is_public(session, deed_id)
    assert not await feed.can_view(session, deed_id, stranger.user_id)
    assert await feed.can_view(session, deed_id, doer.user_id)
    assert await feed.can_view(session, deed_id, reporter.user_id)
    view = await feed.visibility(session, deed_id)
    assert view is not None and not view.listed and view.hidden_by_doer_at is not None

    # Withdrawn but the handler hasn't run yet: nobody can see it any more (live check).
    await set_post(session, deed_id, hidden_by_doer_at=None)
    await withdraw_directly(session, deed_id)
    assert not await feed.is_public(session, deed_id)
    assert not await feed.can_view(session, deed_id, doer.user_id)
    assert not await feed.can_view(session, deed_id, reporter.user_id)

    assert await feed.visibility(session, uuid.uuid4()) is None
    assert not await feed.is_public(session, uuid.uuid4())
