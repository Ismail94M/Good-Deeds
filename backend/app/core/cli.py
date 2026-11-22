"""Operations command line (M4 SEC3 M1).

    uv run python -m app.core.cli redispatch [--older-than-minutes N]

Sends again every (event, handler) pair that was enqueued but never processed, dispatched more
than ``N`` minutes ago (default 0: all of them), for handlers still registered. Use it after an
outage when admin #10 shows pending scoring events past a month's cutoff; the hourly
``core.redispatch_stale`` job does the same for pairs older than an hour. Handling is
idempotent, so sending a pair twice is harmless.

In Docker: ``docker compose exec api python -m app.core.cli redispatch``.
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from app.core import events
from app.core.db import SessionLocal


async def _redispatch(older_than_minutes: int) -> int:
    from app.core import jobs  # loads every module's handlers and the job queue

    before = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    # One pass: the pairs stay unprocessed until the worker runs them, so a second pass would
    # find the same pairs again.
    async with jobs.job_app.open_async(), SessionLocal() as session, session.begin():
        return await events.redispatch_stale(
            session, jobs.enqueue_handler, before, batch_size=100_000
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="app.core.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    redispatch = sub.add_parser("redispatch", help="send unprocessed handler jobs again")
    redispatch.add_argument("--older-than-minutes", type=int, default=0)
    args = parser.parse_args(argv)
    if args.older_than_minutes < 0:
        parser.error("--older-than-minutes must be 0 or more")
    sent = asyncio.run(_redispatch(args.older_than_minutes))
    print(f"redispatched {sent} (event, handler) pairs")


if __name__ == "__main__":
    main()
