"""Likes, saves, shares and hide/show (docs/M3-DESIGN.md §3.4 #3, #4, #8, #9; Q24, Q43).

Rate limits are the router's job and come before any lookup. A deed the caller may not see is 404
``deed_not_found`` (the same as a missing one).

Likes (Q43): every like counts in ``like_count``; only likes from someone other than the deed's
doer and reporter count in ``top_like_count`` (the Top score), and both change in the same UPDATE,
so an unlike reverses exactly what its like did. Only a real insert or delete changes a counter.

Hide/show (Q24): two separate hides, each set and cleared only by the person who set it. They
never touch the deed, its counts, points or prize fields; each change is audit-logged.
"""

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import case, delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import Conflict
from app.core.events import emit
from app.modules.deeds import service as deeds
from app.modules.feed import _access
from app.modules.feed._compose import visibility_out
from app.modules.feed.events import DeedLiked
from app.modules.feed.models import FeedLike, FeedPost, FeedSave, FeedShare, PostState, ShareTarget
from app.modules.feed.schemas import LikeOut, SaveOut, VisibilityOut

# --------------------------------------------------- likes (#3)


def _counter_change(user_id: uuid.UUID, step: int) -> dict[object, object]:
    """``like_count`` always; ``top_like_count`` only for someone who is neither the doer nor
    the reporter (read from the row being updated, so it is one statement)."""
    others = case(
        (FeedPost.doer_id == user_id, 0),
        (FeedPost.reporter_id == user_id, 0),
        else_=step,
    )
    return {
        FeedPost.like_count: FeedPost.like_count + step,
        FeedPost.top_like_count: FeedPost.top_like_count + others,
    }


async def _bump(session: AsyncSession, deed_id: uuid.UUID, user_id: uuid.UUID, step: int) -> int:
    count = await session.scalar(
        update(FeedPost)
        .where(FeedPost.deed_id == deed_id)
        .values(_counter_change(user_id, step))
        .returning(FeedPost.like_count)
        .execution_options(synchronize_session=False)
    )
    assert count is not None
    return int(count)


async def _like_count(session: AsyncSession, deed_id: uuid.UUID) -> int:
    count = await session.scalar(select(FeedPost.like_count).where(FeedPost.deed_id == deed_id))
    return int(count or 0)


async def like(session: AsyncSession, deed_id: uuid.UUID, user_id: uuid.UUID) -> LikeOut:
    await _access.viewable(session, deed_id, user_id)
    inserted = await session.scalar(
        insert(FeedLike)
        .values(deed_id=deed_id, user_id=user_id)
        .on_conflict_do_nothing()
        .returning(FeedLike.deed_id)
    )
    if inserted is None:
        return LikeOut(liked=True, like_count=await _like_count(session, deed_id))
    count = await _bump(session, deed_id, user_id, +1)
    emit(session, DeedLiked(deed_id=deed_id, user_id=user_id))
    return LikeOut(liked=True, like_count=count)


async def unlike(session: AsyncSession, deed_id: uuid.UUID, user_id: uuid.UUID) -> LikeOut:
    await _access.viewable(session, deed_id, user_id)
    deleted = await session.scalar(
        delete(FeedLike)
        .where(FeedLike.deed_id == deed_id, FeedLike.user_id == user_id)
        .returning(FeedLike.deed_id)
    )
    if deleted is None:
        return LikeOut(liked=False, like_count=await _like_count(session, deed_id))
    return LikeOut(liked=False, like_count=await _bump(session, deed_id, user_id, -1))


# --------------------------------------------------- saves (#4)


async def save(session: AsyncSession, deed_id: uuid.UUID, user_id: uuid.UUID) -> SaveOut:
    await _access.viewable(session, deed_id, user_id)
    await session.execute(
        insert(FeedSave).values(user_id=user_id, deed_id=deed_id).on_conflict_do_nothing()
    )
    return SaveOut(saved=True)


async def unsave(session: AsyncSession, deed_id: uuid.UUID, user_id: uuid.UUID) -> SaveOut:
    """Always allowed (it only removes the caller's own private row), so a save of a deed that is
    hidden now can still be cleared."""
    await session.execute(
        delete(FeedSave).where(FeedSave.user_id == user_id, FeedSave.deed_id == deed_id)
    )
    return SaveOut(saved=False)


# --------------------------------------------------- shares (#9)


async def record_share(
    session: AsyncSession, deed_id: uuid.UUID, user_id: uuid.UUID, target: str
) -> None:
    found = await _access.viewable(session, deed_id, user_id)
    if not found.public:
        raise Conflict("A hidden deed has no share link.", code="not_shareable")
    session.add(FeedShare(deed_id=deed_id, user_id=user_id, target=ShareTarget(target)))
    await session.flush()


# --------------------------------------------------- hide / show (#8)


async def set_hidden(
    session: AsyncSession,
    deed_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    hidden: bool,
    now: datetime | None = None,
) -> VisibilityOut:
    """Set or clear the caller's own hide. Anyone but the doer or reporter gets 404; a post that
    isn't ``published`` (or a deed no longer verified) gets 409 ``deed_not_published``."""
    now = now or datetime.now(UTC)
    post = (
        await session.scalars(
            select(FeedPost)
            .where(FeedPost.deed_id == deed_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    role: Literal["doer", "reporter"] | None = (
        None if post is None else _access.role_of(post, user_id)
    )
    if post is None or role is None:
        raise _access.deed_not_found()
    deed = await deeds.get_deed(session, deed_id)
    if (
        post.state != PostState.PUBLISHED
        or deed is None
        or deed.status != deeds.DeedStatus.VERIFIED
    ):
        raise Conflict("This deed isn't in the feed.", code="deed_not_published")
    column = "hidden_by_doer_at" if role == "doer" else "hidden_by_reporter_at"
    current = getattr(post, column)
    if hidden != (current is not None):
        setattr(post, column, now if hidden else None)
        record_audit(
            session,
            entity_type="deed_visibility",
            entity_id=str(deed_id),
            action="hidden" if hidden else "shown",
            actor_id=user_id,
            new_value={"role": role},
        )
        await session.flush()
        # ``listed`` is generated by the database.
        await session.refresh(post, ["listed"])
    return visibility_out(post, role)
