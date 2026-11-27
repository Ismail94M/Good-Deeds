"""Background jobs of the notifications module (docs/M6-DESIGN.md §5, D115).

Jobs run on the ``notifications`` queue. ``fanout_network`` (D110) works through the fan-outs
the ``DeedVerified`` handler records in ``notification_fanouts``: the row is written in the
handler's transaction, so a fan-out is never lost, and a restart resumes from its cursor.
"""

from datetime import UTC, datetime

from app.core.db import SessionLocal
from app.core.jobs import job_app
from app.modules.notifications._fanout import fanout_step
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


# Batches per run, so one run never holds the worker for long; the next run continues.
FANOUT_BATCHES_PER_RUN = 20


@job_app.periodic(cron="* * * * * */10", periodic_id="notifications_fanout_network")
@job_app.task(
    name="notifications.fanout_network",
    queue="notifications",
    queueing_lock="notifications_fanout_network",
)
async def fanout_network(timestamp: int) -> int:
    """Every 10 seconds: run up to ``FANOUT_BATCHES_PER_RUN`` batches of running network
    fan-outs, one batch (``network.fanout_batch`` people) per transaction. Returns how many
    batches ran."""
    ran = 0
    while ran < FANOUT_BATCHES_PER_RUN:
        async with SessionLocal() as session, session.begin():
            _, config = await load_notifications(session)
            step = await fanout_step(session, config=config, now=datetime.now(UTC))
        if step is None:
            break
        ran += 1
    return ran
