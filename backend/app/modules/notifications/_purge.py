"""Retention (docs/M6-DESIGN.md D115, §15 data minimisation; SEC6-L5).

Nightly:

- notifications ``retention_days`` after their last update;
- dedupe keys after ``dedupe_days``, except a grouped row's ``{group_key}|{actor}`` claims,
  which stay while a row of that group still exists for the person (a grouped row can live
  ``retention_days``, so an unlike and like again after ``dedupe_days`` can't count twice);
- finished fan-out records (``done`` or ``capped``, with the deed and its actors) once they
  finished ``retention_days`` ago, the same window as the rows they wrote;
- daily counters after ``DAILY_COUNT_DAYS``.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications._limits import NotificationsConfig
from app.modules.notifications._write import ist_day
from app.modules.notifications.models import (
    FanoutStatus,
    Notification,
    NotificationDailyCount,
    NotificationDedupe,
    NotificationFanout,
)

DAILY_COUNT_DAYS = 7


@dataclass(frozen=True)
class Purged:
    notifications: int
    dedupe: int
    daily_counts: int
    fanouts: int = 0


def _count(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


async def purge(session: AsyncSession, config: NotificationsConfig, now: datetime) -> Purged:
    """Delete expired rows. The caller commits."""
    history = now - timedelta(days=config.retention_days)
    notifications = await session.execute(
        delete(Notification).where(Notification.updated_at < history)
    )
    # A group claim is ``{group_key}|{actor_id}``; other keys have no ``|``.
    group_alive = exists(
        select(Notification.id).where(
            and_(
                Notification.user_id == NotificationDedupe.user_id,
                Notification.group_key.is_not(None),
                Notification.group_key == func.split_part(NotificationDedupe.key, "|", 1),
            )
        )
    )
    dedupe = await session.execute(
        delete(NotificationDedupe)
        .where(
            NotificationDedupe.created_at < now - timedelta(days=config.dedupe_days),
            ~(NotificationDedupe.key.contains("|") & group_alive),
        )
        .execution_options(synchronize_session=False)
    )
    fanouts = await session.execute(
        delete(NotificationFanout).where(
            NotificationFanout.status.in_([FanoutStatus.DONE, FanoutStatus.CAPPED]),
            NotificationFanout.finished_at < history,
        )
    )
    counts = await session.execute(
        delete(NotificationDailyCount).where(
            NotificationDailyCount.day < ist_day(now, config) - timedelta(days=DAILY_COUNT_DAYS)
        )
    )
    return Purged(_count(notifications), _count(dedupe), _count(counts), _count(fanouts))
