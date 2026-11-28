"""The list, the bell count and read state (docs/M6-DESIGN.md §3.2 N1 to N4, D114).

Own rows only: another person's notification id is 404, the same answer as for an id that
doesn't exist.
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.modules.notifications import _cursor, _render
from app.modules.notifications._limits import NotificationsConfig
from app.modules.notifications.models import Notification, NotificationView
from app.modules.notifications.schemas import NotificationPage, UnreadCount

UNREAD_GROUP_INDEX = "uq_notifications_unread_group"


def not_found() -> NotFound:
    return NotFound("We couldn't find that notification.", code="notification_not_found")


async def list_page(
    session: AsyncSession,
    *,
    viewer_id: uuid.UUID,
    viewer_handle: str,
    unread_only: bool,
    cursor: str | None,
    config: NotificationsConfig,
) -> NotificationPage:
    """N1: newest activity first, ``page_size`` per page, keyset on ``(updated_at, id)``."""
    tab = "unread" if unread_only else "all"
    stmt = select(Notification).where(Notification.user_id == viewer_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    if cursor:
        at, item_id = _cursor.decode(cursor, tab)
        stmt = stmt.where(tuple_(Notification.updated_at, Notification.id) < tuple_(at, item_id))
    rows = list(
        await session.scalars(
            stmt.order_by(Notification.updated_at.desc(), Notification.id.desc())
            .limit(config.page_size + 1)
            .execution_options(populate_existing=True)
        )
    )
    more = len(rows) > config.page_size
    rows = rows[: config.page_size]
    items = await _render.render(
        session, rows, viewer_id=viewer_id, viewer_handle=viewer_handle, config=config
    )
    last = rows[-1] if more and rows else None
    return NotificationPage(
        items=items,
        next_cursor=None if last is None else _cursor.encode(tab, last.updated_at, last.id),
    )


async def unread_count(
    session: AsyncSession, user_id: uuid.UUID, config: NotificationsConfig
) -> UnreadCount:
    """N2: all unread rows, and those new or updated since the bell was last opened."""
    seen_at = await session.scalar(
        select(NotificationView.seen_at).where(NotificationView.user_id == user_id)
    )
    unread = Notification.read_at.is_(None)
    new = func.count().filter(Notification.updated_at > seen_at) if seen_at else func.count()
    row = (
        await session.execute(
            select(func.count(), new).where(Notification.user_id == user_id, unread)
        )
    ).one()
    return UnreadCount(
        unread=int(row[0]),
        new_since_seen=int(row[1]),
        unread_poll_seconds=config.unread_poll_seconds,
    )


async def mark_seen(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> None:
    """N3: the bell was opened (never moves back). The caller commits."""
    stmt = insert(NotificationView).values(user_id=user_id, seen_at=now)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[NotificationView.user_id],
            set_={"seen_at": func.greatest(NotificationView.seen_at, stmt.excluded.seen_at)},
        )
    )


async def _own(session: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID) -> Notification:
    row = await session.scalar(
        select(Notification)
        .where(Notification.id == item_id, Notification.user_id == user_id)
        .with_for_update()
    )
    if row is None:
        raise not_found()
    return row


async def mark_read(
    session: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID, now: datetime
) -> None:
    """N4: mark one row read (idempotent). The caller commits."""
    row = await _own(session, user_id, item_id)
    if row.read_at is None:
        row.read_at = max(now, row.created_at)
        await session.flush()


async def mark_unread(session: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID) -> None:
    """N4: mark one row unread (idempotent). A grouped row can't become unread while a newer
    unread row of the same group exists (one unread row per group, D108): 409
    ``newer_unread_exists``."""
    row = await _own(session, user_id, item_id)
    if row.read_at is None:
        return
    try:
        async with session.begin_nested():
            row.read_at = None
            await session.flush()
    except IntegrityError as exc:
        if UNREAD_GROUP_INDEX not in str(exc.orig):
            raise
        raise Conflict(
            "There's already a newer unread notification like this one.",
            code="newer_unread_exists",
        ) from exc


async def mark_all_read(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> int:
    """N4: mark every unread row read. Returns how many changed. The caller commits."""
    result = await session.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=func.greatest(Notification.created_at, now))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
