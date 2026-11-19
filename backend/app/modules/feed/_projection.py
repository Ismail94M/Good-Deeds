"""The feed projection (docs/M3-DESIGN.md §2.3, §5, D37).

``sync_post`` reads the deed's *current* status through ``deeds.service`` and upserts its post, so
handler order and retries never matter:

- ``verified``: a ``published`` post, inserted the first time (``published_at`` is the deed's
  ``verified_at``). An existing post keeps its state: ``held`` stays held, and a terminal state is
  never turned back into ``published``.
- ``withdrawn`` -> ``withdrawn``; ``rejected`` and ``unverified`` -> ``rejected``; ``removed`` ->
  ``removed``. Only an existing post changes: a deed that never had a post (a pending deed that was
  withdrawn or expired) gets none.
- ``pending``: nothing (pending deeds are never published).

Only ``feed_posts`` is written; the deed, its counts and prize fields are never touched.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import service as deeds
from app.modules.feed.models import FeedPost, PostState

TERMINAL = frozenset({PostState.WITHDRAWN, PostState.REJECTED, PostState.REMOVED})

_STATE_FOR_STATUS: dict[deeds.DeedStatus, PostState] = {
    deeds.DeedStatus.WITHDRAWN: PostState.WITHDRAWN,
    deeds.DeedStatus.REJECTED: PostState.REJECTED,
    deeds.DeedStatus.UNVERIFIED: PostState.REJECTED,
    deeds.DeedStatus.REMOVED: PostState.REMOVED,
}


async def sync_post(
    session: AsyncSession, deed_id: uuid.UUID, *, now: datetime | None = None
) -> PostState | None:
    """Bring ``deed_id``'s post in line with the deed. Returns the post's state afterwards (None
    when it has no post). Idempotent."""
    now = now or datetime.now(UTC)
    deed = await deeds.get_deed(session, deed_id)
    if deed is None:
        return None
    if (
        deed.status == deeds.DeedStatus.VERIFIED
        and deed.doer_id is not None
        and deed.verified_at is not None
    ):
        # Two syncs of one deed at once: the first insert wins, the other updates below.
        await session.execute(
            insert(FeedPost)
            .values(
                deed_id=deed.id,
                doer_id=deed.doer_id,
                reporter_id=deed.reporter_id,
                published_at=deed.verified_at,
                state=PostState.PUBLISHED,
                synced_at=now,
            )
            .on_conflict_do_nothing(index_elements=[FeedPost.deed_id])
        )
    post = (
        await session.scalars(
            select(FeedPost)
            .where(FeedPost.deed_id == deed_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if post is None:
        return None
    target = _STATE_FOR_STATUS.get(deed.status)
    if target is not None and post.state not in TERMINAL:
        post.state = target
    post.synced_at = now
    await session.flush()
    return post.state
