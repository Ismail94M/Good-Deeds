"""The feed reads notifications uses (docs/M6-DESIGN.md D104, D110, D111): ``follower_ids_page``
and ``earlier_commenter_ids``."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.feed.models import CommentDeletedRole, FeedComment
from app.modules.feed.service import Follower, earlier_commenter_ids, follower_ids_page
from tests.deeds.helpers import members
from tests.feed.helpers import follow, verified_deed


async def test_follower_pages_are_keyset_ordered_and_deduplicated(session: AsyncSession) -> None:
    a, b, x, y, z = await members(session, 5)
    for fan in (x, y, z):
        await follow(session, fan, a)
    await follow(session, y, b)
    await follow(session, a, b)  # a follows b, but the given people are left out
    ordered = sorted([x, y, z], key=lambda m: m.user_id)

    first = await follower_ids_page(session, [a.user_id, b.user_id], after=None, limit=2)
    assert [f.user_id for f in first] == [m.user_id for m in ordered[:2]]
    rest = await follower_ids_page(
        session, [a.user_id, b.user_id], after=first[-1].user_id, limit=2
    )
    assert [f.user_id for f in rest] == [ordered[2].user_id]
    every = {f.user_id: f for f in first + rest}
    assert every[y.user_id] == Follower(y.user_id, tuple(sorted([a.user_id, b.user_id])))
    assert every[x.user_id].of == (a.user_id,)
    assert await follower_ids_page(session, [], after=None, limit=5) == []


async def test_follower_audiences(session: AsyncSession) -> None:
    actor, fan, friend, followed = await members(session, 4)
    await follow(session, fan, actor)
    await follow(session, friend, actor)
    await follow(session, actor, friend)
    await follow(session, actor, followed)

    async def ids(
        audience: Literal["followers", "mutual", "followers_or_following"],
    ) -> set[uuid.UUID]:
        page = await follower_ids_page(
            session,
            [actor.user_id],
            after=None,
            limit=10,
            audience=audience,
        )
        return {f.user_id for f in page}

    assert await ids("followers") == {fan.user_id, friend.user_id}
    assert await ids("mutual") == {friend.user_id}
    assert await ids("followers_or_following") == {
        fan.user_id,
        friend.user_id,
        followed.user_id,
    }


async def test_earlier_commenters(session: AsyncSession) -> None:
    doer, reporter, a, b, c, old = await members(session, 6)
    deed_id = await verified_deed(session, reporter, doer)
    now = datetime.now(UTC)

    def add(author_id: uuid.UUID, at: datetime, deleted: bool = False) -> FeedComment:
        row = FeedComment(
            deed_id=deed_id,
            author_id=author_id,
            body=None if deleted else "Lovely",
            created_at=at,
            deleted_at=at if deleted else None,
            deleted_by=author_id if deleted else None,
            deleted_role=CommentDeletedRole.AUTHOR if deleted else None,
        )
        session.add(row)
        return row

    add(old.user_id, now - timedelta(days=40))
    add(a.user_id, now - timedelta(minutes=3))
    add(b.user_id, now - timedelta(minutes=2), deleted=True)
    add(c.user_id, now + timedelta(minutes=5))  # after the new comment
    mine = add(a.user_id, now)
    await session.flush()
    since = now - timedelta(days=30)
    assert await earlier_commenter_ids(session, deed_id, before=mine.id, since=since) == set()
    new = add(doer.user_id, now + timedelta(seconds=1))
    await session.flush()
    got = await earlier_commenter_ids(session, deed_id, before=new.id, since=since)
    assert got == {a.user_id}
    other = await verified_deed(session, reporter, doer)
    assert await earlier_commenter_ids(session, other, before=new.id, since=since) == set()
