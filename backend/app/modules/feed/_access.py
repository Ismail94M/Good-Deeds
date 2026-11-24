"""Who may see a feed post (docs/M3-DESIGN.md §3.4 #2, #10, §3.5; D37).

Every check reads the post *and* the deed's live status through ``deeds.service``, so a withdrawn
deed disappears at once, before its ``DeedWithdrawn`` handler has run.

- **Public** (visitors, the public page, photo reports): the post is ``listed`` and the deed is
  ``verified``.
- **Viewable** by a signed-in user: public, or the caller is the deed's doer or reporter and the
  post is ``published`` (hidden by one of them, but not withdrawn, rejected, removed or held).
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.modules.deeds import service as deeds
from app.modules.feed.models import FeedPost, PostState

Role = Literal["doer", "reporter"]


def deed_not_found() -> NotFound:
    return NotFound("We couldn't find that deed.", code="deed_not_found")


@dataclass(frozen=True)
class Access:
    post: FeedPost
    deed: deeds.DeedView
    role: Role | None

    @property
    def public(self) -> bool:
        return self.post.listed

    @property
    def viewable(self) -> bool:
        return self.public or (self.role is not None and self.post.state == PostState.PUBLISHED)


def role_of(post: FeedPost, user_id: uuid.UUID | None) -> Role | None:
    if user_id is None:
        return None
    if user_id == post.doer_id:
        return "doer"
    if user_id == post.reporter_id:
        return "reporter"
    return None


async def access(
    session: AsyncSession, deed_id: uuid.UUID, user_id: uuid.UUID | None, *, lock: bool = False
) -> Access | None:
    """The post, the live deed and the caller's role (``user_id`` None for a visitor). None when
    there is no post or the deed isn't ``verified`` any more (then nobody may see it)."""
    stmt = select(FeedPost).where(FeedPost.deed_id == deed_id)
    if lock:
        stmt = stmt.with_for_update()
    post = (await session.scalars(stmt.execution_options(populate_existing=True))).first()
    if post is None:
        return None
    deed = await deeds.get_deed(session, deed_id)
    if deed is None or deed.status != deeds.DeedStatus.VERIFIED:
        return None
    return Access(post=post, deed=deed, role=role_of(post, user_id))


async def viewable(
    session: AsyncSession, deed_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool = False
) -> Access:
    """The post if ``user_id`` may see it, else 404 ``deed_not_found`` (the same answer as for a
    deed that doesn't exist)."""
    found = await access(session, deed_id, user_id, lock=lock)
    if found is None or not found.viewable:
        raise deed_not_found()
    return found
