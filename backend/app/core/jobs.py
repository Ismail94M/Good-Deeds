"""Background jobs (Procrastinate, stored in Postgres).

Run the worker with:  uv run procrastinate --app=app.core.jobs.job_app worker
"""

import uuid

from procrastinate import App, PsycopgConnector, RetryStrategy

from app.core import events
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.modules.registry import load_modules

job_app = App(connector=PsycopgConnector(conninfo=get_settings().database_dsn))

# Handlers subscribe to events when their module is imported.
load_modules()


async def _enqueue_handler(event_id: uuid.UUID, handler_name: str) -> None:
    await handle_event.defer_async(event_id=str(event_id), handler=handler_name)


@job_app.periodic(cron="* * * * * */5", periodic_id="dispatch_outbox")
@job_app.task(name="core.dispatch_outbox", queueing_lock="dispatch_outbox")
async def dispatch_outbox(timestamp: int) -> int:
    """Every 5 seconds: turn new outbox events into one handler job each."""
    total = 0
    while True:
        async with SessionLocal() as session, session.begin():
            count = await events.dispatch_pending(session, _enqueue_handler)
        total += count
        if count == 0:
            return total


@job_app.task(
    name="core.handle_event",
    retry=RetryStrategy(max_attempts=8, exponential_wait=2),
)
async def handle_event(event_id: str, handler: str) -> None:
    async with SessionLocal() as session, session.begin():
        await events.run_handler(session, uuid.UUID(event_id), handler)
