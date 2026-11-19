"""One-time notices (docs/M3-DESIGN.md Q41, §3.4b #1).

``GET /api/feed/me/notices`` returns the known keys the caller hasn't dismissed yet, as a bare
array; ``POST /api/feed/me/notices/{key}/seen`` dismisses one (idempotent; an unknown key is 404).
Known keys are a code constant. The "comment removed" notices (``/me/comment-notices``) come with
comment reports (B27).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.modules.feed.models import FeedNoticeSeen

KNOWN_NOTICES: tuple[str, ...] = ("public_feed_intro",)


async def unseen(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    seen = set(
        await session.scalars(
            select(FeedNoticeSeen.notice_key).where(FeedNoticeSeen.user_id == user_id)
        )
    )
    return [key for key in KNOWN_NOTICES if key not in seen]


async def mark_seen(
    session: AsyncSession, user_id: uuid.UUID, key: str, *, now: datetime | None = None
) -> None:
    if key not in KNOWN_NOTICES:
        raise NotFound("We couldn't find that notice.", code="notice_not_found")
    await session.execute(
        insert(FeedNoticeSeen)
        .values(user_id=user_id, notice_key=key, seen_at=now or datetime.now(UTC))
        .on_conflict_do_nothing()
    )
