"""Retention (docs/M6-DESIGN.md D115, §15 data minimisation).

Nightly: notifications ``retention_days`` after their last update, dedupe keys after
``dedupe_days``, and daily counters after ``DAILY_COUNT_DAYS``. Finished fan-out records stay
(they record when a cap was hit) until account deletion is built.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications._limits import NotificationsConfig
from app.modules.notifications._write import ist_day
from app.modules.notifications.models import (
    Notification,
    NotificationDailyCount,
    NotificationDedupe,
)

DAILY_COUNT_DAYS = 7


@dataclass(frozen=True)
class Purged:
    notifications: int
    dedupe: int
    daily_counts: int


def _count(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


async def purge(session: AsyncSession, config: NotificationsConfig, now: datetime) -> Purged:
    """Delete expired rows. The caller commits."""
    notifications = await session.execute(
        delete(Notification).where(
            Notification.updated_at < now - timedelta(days=config.retention_days)
        )
    )
    dedupe = await session.execute(
        delete(NotificationDedupe).where(
            NotificationDedupe.created_at < now - timedelta(days=config.dedupe_days)
        )
    )
    counts = await session.execute(
        delete(NotificationDailyCount).where(
            NotificationDailyCount.day < ist_day(now, config) - timedelta(days=DAILY_COUNT_DAYS)
        )
    )
    return Purged(_count(notifications), _count(dedupe), _count(counts))
