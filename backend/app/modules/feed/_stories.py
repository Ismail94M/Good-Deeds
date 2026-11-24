"""Good Deeds Stories (PO 2026-11-23, docs/M3-DESIGN.md D73, migration 0024).

A story is a *listed* feed post whose deed was verified in the last ``story_window_hours``. It is
not a new record: the same post is a story in two rings, the reporter's (``reported``, blue: "Shiva
reported a kindness act by Rahul") and the doer's (``verified``, green: "Rahul's kindness act was
verified by Shiva"). Expiry is the query's window, so nothing is deleted when a story ends, and the
deed stays in the feed as before.

Audience: the caller's network, i.e. the caller, the people they follow and the people who follow
them (open follows, Q36). Only listed posts count, so a hide, withdrawal, rejection, removal or hold
takes a story down at once, exactly as in the feed; the person's stories are composed through
``_compose`` like any feed page (live deed check, removed photos left out, signed photo URLs).

Loading is in two steps so no media is loaded up front: ``rings`` (who has stories, counts, seen
state; no photos) and ``person`` (one person's stories with signed photo URLs, read when their ring
is opened). ``story_views`` remembers what the caller watched; the nightly job prunes it.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, literal, or_, select, tuple_, union, union_all
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import ObjectStorage
from app.modules.feed import _access, _compose, _reads
from app.modules.feed._limits import FeedLimits
from app.modules.feed.models import FeedPost, Follow, StoryRole, StoryView
from app.modules.feed.schemas import StoryOut, StoryPerson, StoryRing, StoryRings, UserRef
from app.modules.identity import service as identity

PRUNE_BATCH_SIZE = 5000


def _network(viewer_id: uuid.UUID) -> Any:
    """The caller, the people they follow and their followers, as one id subquery."""
    return union(
        select(literal(viewer_id).label("user_id")),
        select(Follow.followed_id).where(Follow.follower_id == viewer_id),
        select(Follow.follower_id).where(Follow.followed_id == viewer_id),
    ).subquery()


def _since(limits: FeedLimits, now: datetime) -> datetime:
    return now - timedelta(hours=limits.story_window_hours)


async def in_network(session: AsyncSession, viewer_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    if viewer_id == user_id:
        return True
    found = await session.scalar(
        select(Follow.follower_id)
        .where(
            or_(
                and_(Follow.follower_id == viewer_id, Follow.followed_id == user_id),
                and_(Follow.follower_id == user_id, Follow.followed_id == viewer_id),
            )
        )
        .limit(1)
    )
    return found is not None


async def rings(
    session: AsyncSession,
    viewer_id: uuid.UUID,
    *,
    limits: FeedLimits,
    now: datetime | None = None,
) -> StoryRings:
    """Everyone in the caller's network with a story: the caller first, then people with stories
    the caller hasn't watched, then the rest; newest story first within each group."""
    since = _since(limits, now or datetime.now(UTC))
    net = _network(viewer_id)
    window = and_(FeedPost.listed, FeedPost.published_at >= since)
    stories = union_all(
        select(
            FeedPost.reporter_id.label("owner"),
            literal(StoryRole.REPORTED.value).label("role"),
            FeedPost.deed_id,
            FeedPost.published_at,
        ).where(window, FeedPost.reporter_id.in_(select(net.c.user_id))),
        select(
            FeedPost.doer_id.label("owner"),
            literal(StoryRole.VERIFIED.value).label("role"),
            FeedPost.deed_id,
            FeedPost.published_at,
        ).where(window, FeedPost.doer_id.in_(select(net.c.user_id))),
    ).subquery()
    seen = and_(
        StoryView.viewer_id == viewer_id,
        StoryView.deed_id == stories.c.deed_id,
        StoryView.role == stories.c.role,
    )
    rows = (
        await session.execute(
            select(
                stories.c.owner,
                func.count().filter(stories.c.role == StoryRole.REPORTED.value),
                func.count().filter(stories.c.role == StoryRole.VERIFIED.value),
                func.count().filter(StoryView.deed_id.is_(None)),
                func.max(stories.c.published_at),
            )
            .select_from(stories)
            .outerjoin(StoryView, seen)
            .group_by(stories.c.owner)
        )
    ).all()
    active = await identity.active_user_ids(session, [r[0] for r in rows])
    rows = [r for r in rows if r[0] in active]
    # Me first, then unwatched, then watched; newest first within each.
    rows.sort(key=lambda r: (r[0] != viewer_id, r[3] == 0, -r[4].timestamp()))
    rows = rows[: limits.story_max_people]
    names = await identity.public_summaries(session, [r[0] for r in rows])
    items = [
        StoryRing(
            user=UserRef(**names[owner].model_dump()),
            is_me=owner == viewer_id,
            reported_count=int(reported),
            verified_count=int(verified),
            unseen_count=int(unseen),
            latest_at=latest,
        )
        for owner, reported, verified, unseen, latest in rows
        if owner in names
    ]
    return StoryRings(
        items=items,
        seconds_per_story=limits.story_seconds,
        window_hours=limits.story_window_hours,
    )


async def person(
    session: AsyncSession,
    viewer_id: uuid.UUID,
    handle: str,
    storage: ObjectStorage,
    *,
    limits: FeedLimits,
    now: datetime | None = None,
) -> StoryPerson:
    """One person's stories, oldest first (the newest ``story_max_per_person``). 404
    ``user_not_found`` for someone outside the caller's network, like an unknown handle."""
    owner = await _reads.active_user(session, handle)
    if not await in_network(session, viewer_id, owner):
        raise _reads.user_not_found()
    since = _since(limits, now or datetime.now(UTC))
    posts = list(
        await session.scalars(
            select(FeedPost)
            .where(
                FeedPost.listed,
                FeedPost.published_at >= since,
                or_(FeedPost.doer_id == owner, FeedPost.reporter_id == owner),
            )
            .order_by(FeedPost.published_at.desc(), FeedPost.deed_id.desc())
            .limit(limits.story_max_per_person)
            .execution_options(populate_existing=True)
        )
    )
    posts.reverse()
    roles = {
        p.deed_id: StoryRole.VERIFIED if p.doer_id == owner else StoryRole.REPORTED for p in posts
    }
    watched: set[tuple[uuid.UUID, str]] = set()
    if posts:
        watched = {
            (deed_id, StoryRole(role).value)
            for deed_id, role in await session.execute(
                select(StoryView.deed_id, StoryView.role).where(
                    StoryView.viewer_id == viewer_id, StoryView.deed_id.in_(list(roles))
                )
            )
        }
    composed = await _compose.compose(session, posts, viewer_id, storage)
    names = await identity.public_summaries(session, [owner])
    return StoryPerson(
        user=UserRef(**names[owner].model_dump()),
        is_me=owner == viewer_id,
        seconds_per_story=limits.story_seconds,
        items=[
            StoryOut(
                role=roles[post.deed_id].value,
                seen=(post.deed_id, roles[post.deed_id].value) in watched,
                post=post,
            )
            for post in composed
        ],
    )


async def mark_seen(
    session: AsyncSession,
    viewer_id: uuid.UUID,
    deed_id: uuid.UUID,
    role: StoryRole,
    *,
    now: datetime | None = None,
) -> None:
    """Remember that the caller watched this story. Idempotent; 404 ``deed_not_found`` for a
    deed that isn't in the feed (the same answer as for one that doesn't exist)."""
    found = await _access.access(session, deed_id, viewer_id)
    if found is None or not found.public:
        raise _access.deed_not_found()
    await session.execute(
        insert(StoryView)
        .values(viewer_id=viewer_id, deed_id=deed_id, role=role, seen_at=now or datetime.now(UTC))
        .on_conflict_do_nothing()
    )


async def prune_views_batch(
    session: AsyncSession, limits: FeedLimits, *, now: datetime | None = None
) -> int:
    """Delete up to ``PRUNE_BATCH_SIZE`` views older than ``story_seen_retention_hours``."""
    cutoff = (now or datetime.now(UTC)) - timedelta(hours=limits.story_seen_retention_hours)
    batch = (
        select(StoryView.viewer_id, StoryView.deed_id, StoryView.role)
        .where(StoryView.seen_at < cutoff)
        .limit(PRUNE_BATCH_SIZE)
    )
    result = await session.execute(
        delete(StoryView).where(
            tuple_(StoryView.viewer_id, StoryView.deed_id, StoryView.role).in_(batch)
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)
