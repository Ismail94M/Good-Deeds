"""Turning feed posts into responses (docs/M3-DESIGN.md §3.4 #1 "Composition", §3.2).

Same for every list: the page's posts -> ``deeds.service.get_deeds`` (anything no longer
``verified`` is dropped, D37) -> ``identity.service.public_summaries`` (doer and reporter) ->
``media.service.public_photos`` -> the caller's likes, saves and follows for those IDs (three
indexed queries). Content (category, description, area) is read live, never copied (D38).

Privacy: ``UserRef`` only (no IDs), the area name only (never coordinates or the ward code).
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.storage import ObjectStorage
from app.modules.deeds import service as deeds
from app.modules.feed import _photos
from app.modules.feed._access import Access, Role, role_of
from app.modules.feed.models import FeedLike, FeedPost, FeedSave, Follow, PostState
from app.modules.feed.schemas import (
    PhotoOut,
    PostDetail,
    PostOut,
    PublicDeed,
    UserRef,
    VisibilityOut,
)
from app.modules.identity import service as identity


def share_url(deed_id: uuid.UUID) -> str:
    return f"{get_settings().web_base_url.rstrip('/')}/d/{deed_id}"


def visibility_out(post: FeedPost, role: Role) -> VisibilityOut:
    """The doer's or reporter's view of the two separate hides (Q24): each person can undo only
    their own hide, and only while the post is ``published``."""
    mine, other = (
        (post.hidden_by_doer_at, post.hidden_by_reporter_at)
        if role == "doer"
        else (post.hidden_by_reporter_at, post.hidden_by_doer_at)
    )
    published = post.state == PostState.PUBLISHED
    return VisibilityOut(
        listed=post.listed,
        hidden_by_me=mine is not None,
        hidden_by_other=other is not None,
        can_hide=published and mine is None,
        can_show=published and mine is not None,
    )


@dataclass(frozen=True)
class _Parts:
    views: Mapping[uuid.UUID, deeds.DeedView]
    users: Mapping[uuid.UUID, UserRef]
    photos: Mapping[uuid.UUID, list[PhotoOut]]
    liked: frozenset[uuid.UUID]
    saved: frozenset[uuid.UUID]
    following: frozenset[uuid.UUID]
    labels: Mapping[str, str]


async def _gather(
    session: AsyncSession,
    posts: Sequence[FeedPost],
    viewer_id: uuid.UUID | None,
    storage: ObjectStorage,
) -> _Parts:
    views = {
        deed_id: view
        for deed_id, view in (await deeds.get_deeds(session, [p.deed_id for p in posts])).items()
        if view.status == deeds.DeedStatus.VERIFIED
    }
    live = [p for p in posts if p.deed_id in views]
    summaries = await identity.public_summaries(
        session, {p.doer_id for p in live} | {p.reporter_id for p in live}
    )
    users = {uid: UserRef.of(s) for uid, s in summaries.items()}
    ids = [p.deed_id for p in live]
    photos = await _photos.public_photos(session, ids, storage)
    liked: set[uuid.UUID] = set()
    saved: set[uuid.UUID] = set()
    following: set[uuid.UUID] = set()
    if viewer_id is not None and ids:
        liked = set(
            await session.scalars(
                select(FeedLike.deed_id).where(
                    FeedLike.user_id == viewer_id, FeedLike.deed_id.in_(ids)
                )
            )
        )
        saved = set(
            await session.scalars(
                select(FeedSave.deed_id).where(
                    FeedSave.user_id == viewer_id, FeedSave.deed_id.in_(ids)
                )
            )
        )
        following = set(
            await session.scalars(
                select(Follow.followed_id).where(
                    Follow.follower_id == viewer_id,
                    Follow.followed_id.in_({p.doer_id for p in live}),
                )
            )
        )
    return _Parts(
        views=views,
        users=users,
        photos=photos,
        liked=frozenset(liked),
        saved=frozenset(saved),
        following=frozenset(following),
        labels=await deeds.category_labels(session),
    )


def _post_out(
    post: FeedPost, parts: _Parts, viewer_id: uuid.UUID | None, rank: int | None
) -> PostOut | None:
    view = parts.views.get(post.deed_id)
    doer, reporter = parts.users.get(post.doer_id), parts.users.get(post.reporter_id)
    if view is None or doer is None or reporter is None:
        return None
    return PostOut(
        deed_id=post.deed_id,
        doer=doer,
        reporter=reporter,
        category=view.category,
        # A category later removed from config falls back to its slug.
        category_label=parts.labels.get(view.category, view.category),
        area_name=deeds.area_name(view),
        occurred_at=view.occurred_at,
        published_at=post.published_at,
        description=view.description,
        photos=parts.photos.get(post.deed_id, []),
        like_count=post.like_count,
        comment_count=post.comment_count,
        liked=post.deed_id in parts.liked,
        saved=post.deed_id in parts.saved,
        following_doer=post.doer_id in parts.following,
        my_role=role_of(post, viewer_id),
        rank=rank,
        share_url=share_url(post.deed_id),
    )


async def compose(
    session: AsyncSession,
    posts: Sequence[FeedPost],
    viewer_id: uuid.UUID,
    storage: ObjectStorage,
    *,
    ranks: Mapping[uuid.UUID, int] | None = None,
) -> list[PostOut]:
    """Responses for a page of posts, in the given order. Posts whose deed is no longer verified
    (or whose people can't be found) are left out; the caller's cursor still comes from the rows
    it read, so paging never skips or repeats."""
    if not posts:
        return []
    parts = await _gather(session, posts, viewer_id, storage)
    out = (_post_out(p, parts, viewer_id, (ranks or {}).get(p.deed_id)) for p in posts)
    return [o for o in out if o is not None]


async def detail(
    session: AsyncSession, found: Access, viewer_id: uuid.UUID, storage: ObjectStorage
) -> PostDetail | None:
    parts = await _gather(session, [found.post], viewer_id, storage)
    base = _post_out(found.post, parts, viewer_id, None)
    if base is None:
        return None
    return PostDetail(
        **base.model_dump(),
        beneficiaries_count=found.deed.beneficiaries_count,
        verification=found.deed.verification_method.value,
        visibility=None if found.role is None else visibility_out(found.post, found.role),
    )


async def public(session: AsyncSession, found: Access, storage: ObjectStorage) -> PublicDeed | None:
    parts = await _gather(session, [found.post], None, storage)
    base = _post_out(found.post, parts, None, None)
    if base is None:
        return None
    # Public pages never show profile pictures (M6A D119): the default avatar only.
    return PublicDeed(
        deed_id=base.deed_id,
        doer=base.doer.model_copy(update={"avatar": None}),
        reporter=base.reporter.model_copy(update={"avatar": None}),
        category=base.category,
        category_label=base.category_label,
        area_name=base.area_name,
        occurred_at=base.occurred_at,
        description=base.description,
        photos=base.photos,
        like_count=base.like_count,
        comment_count=base.comment_count,
    )
