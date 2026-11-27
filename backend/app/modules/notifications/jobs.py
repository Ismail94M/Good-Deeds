"""Background jobs of the notifications module (docs/M6-DESIGN.md §5, D115).

Jobs run on the ``notifications`` queue. ``fanout_network`` (deferred by the ``DeedVerified``
handler, D110) comes with the event handlers (task B50).
"""

from datetime import UTC, datetime

from app.core.db import SessionLocal
from app.core.jobs import job_app
from app.modules.notifications._limits import load_notifications
from app.modules.notifications._purge import purge as _purge


# Procrastinate's cron is in UTC: 22:10 UTC is 03:40 IST.
@job_app.periodic(cron="10 22 * * *", periodic_id="notifications_purge")
@job_app.task(
    name="notifications.purge", queue="notifications", queueing_lock="notifications_purge"
)
async def purge(timestamp: int) -> int:
    """Nightly at 03:40 IST: delete notifications ``retention_days`` after their last update,
    dedupe keys after ``dedupe_days`` and old daily counters. Returns how many notifications
    went."""
    async with SessionLocal() as session, session.begin():
        _, config = await load_notifications(session)
        result = await _purge(session, config, datetime.now(UTC))
    return result.notifications
