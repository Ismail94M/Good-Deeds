"""Follows (docs/M3-DESIGN.md §3.4 #11, Q36).

Open, one-way follows. Following yourself is 400 ``cannot_follow_self``; at ``max_following`` a new
follow is 409 ``following_limit`` (checked under a per-follower lock, so concurrent follows can't
pass it together). Idempotent; only a real insert emits ``UserFollowed``. Removing a follower is
quiet: no event, and they aren't told. Follower and following lists are the caller's own only.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import advisory_xact_lock
from app.core.errors import AppError, Conflict
from app.core.events import emit
from app.modules.feed import _cursor, _reads
from app.modules.feed._limits import FeedLimits
from app.modules.feed.events import UserFollowed
from app.modules.feed.models import Follow
from app.modules.feed.schemas import ConnectionOut, ConnectionPage, SocialOut, UserRef
from app.modules.identity import service as identity


async def follow(
    session: AsyncSession,
    follower_id: uuid.UUID,
    handle: str,
    *,
    limits: FeedLimits,
    now: datetime | None = None,
) -> SocialOut:
    now = now or datetime.now(UTC)
    target = await _reads.active_user(session, handle)
    if target == follower_id:
        raise AppError("You can't follow yourself.", code="cannot_follow_self")
    await advisory_xact_lock(session, "follows", str(follower_id))
    existing = await session.get(Follow, {"follower_id": follower_id, "followed_id": target})
    if existing is None:
        count = await session.scalar(
            select(func.count()).select_from(Follow).where(Follow.follower_id == follower_id)
        )
        if int(count or 0) >= limits.max_following:
            raise Conflict(
                f"You can follow up to {limits.max_following} people.", code="following_limit"
            )
        inserted = await session.scalar(
            insert(Follow)
            .values(follower_id=follower_id, followed_id=target, created_at=now)
            .on_conflict_do_nothing()
            .returning(Follow.followed_id)
        )
        if inserted is not None:
            emit(session, UserFollowed(follower_id=follower_id, followed_id=target))
    return await _reads.social(session, handle, follower_id)


async def unfollow(session: AsyncSession, follower_id: uuid.UUID, handle: str) -> SocialOut:
    target = await _reads.active_user(session, handle)
    await session.execute(
        delete(Follow).where(Follow.follower_id == follower_id, Follow.followed_id == target)
    )
    return await _reads.social(session, handle, follower_id)


async def remove_follower(session: AsyncSession, user_id: uuid.UUID, handle: str) -> None:
    """Quietly end ``handle``'s follow of the caller (no event)."""
    user = await identity.get_user_by_handle(session, handle)
    if user is None:
        raise _reads.user_not_found()
    await session.execute(
        delete(Follow).where(Follow.follower_id == user.id, Follow.followed_id == user_id)
    )


async def connections(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    kind: str,
    cursor: str | None = None,
    limit: int = _cursor.DEFAULT_PAGE_SIZE,
) -> ConnectionPage:
    """The caller's followers (``kind="followers"``) or the people they follow, newest first."""
    if kind == "followers":
        other, mine = Follow.follower_id, Follow.followed_id
    else:
        other, mine = Follow.followed_id, Follow.follower_id
    stmt = select(other, Follow.created_at).where(mine == user_id)
    if cursor is not None:
        at, last_id = _cursor.decode(cursor)
        stmt = stmt.where(tuple_(Follow.created_at, other) < tuple_(at, last_id))
    rows = (
        await session.execute(
            stmt.order_by(Follow.created_at.desc(), other.desc()).limit(limit + 1)
        )
    ).all()
    page, more = rows[:limit], len(rows) > limit
    ids = [row[0] for row in page]
    names = await identity.public_summaries(session, ids)
    if kind == "followers":
        followed_back = set(
            await session.scalars(
                select(Follow.followed_id).where(
                    Follow.follower_id == user_id, Follow.followed_id.in_(ids)
                )
            )
        )
    else:
        followed_back = set(ids)
    items = [
        ConnectionOut(user=UserRef(**names[i].model_dump()), following=i in followed_back)
        for i in ids
        if i in names
    ]
    return ConnectionPage(
        items=items,
        next_cursor=_cursor.encode(page[-1][1], page[-1][0]) if more else None,
    )
