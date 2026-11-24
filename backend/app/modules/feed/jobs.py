"""Background jobs of the feed module (Procrastinate). Organised by section."""

from app.core.db import SessionLocal
from app.core.jobs import job_app
from app.modules.feed import _comments, _top
from app.modules.feed._limits import load_limits

# --------------------------------------------------- Top snapshots (Q26)


@job_app.periodic(cron="* * * * *", periodic_id="feed_refresh_top")
@job_app.task(name="feed.refresh_top", queueing_lock="feed_refresh_top")
async def refresh_top(timestamp: int) -> list[str]:
    """Every minute: recompute each period (week, month, year) whose newest snapshot is older
    than its ``top_{period}_refresh_minutes``, so the intervals follow config."""
    async with SessionLocal() as session, session.begin():
        _, limits = await load_limits(session)
        return [p.value for p in await _top.refresh_due(session, limits)]


# --------------------------------------------------- deleted comment text (Q38)


@job_app.periodic(cron="47 3 * * *", periodic_id="feed_erase_deleted_comments")
@job_app.task(name="feed.erase_deleted_comments", queueing_lock="feed_erase_deleted_comments")
async def erase_deleted_comments(timestamp: int) -> int:
    """Nightly: erase the text of comments deleted by the doer, reporter or moderation more than
    ``deleted_comment_retention_days`` ago. One batch per transaction, until a batch is short."""
    total = 0
    while True:
        async with SessionLocal() as session, session.begin():
            _, limits = await load_limits(session)
            count = await _comments.erase_deleted_batch(session, limits)
        total += count
        if count < _comments.ERASE_BATCH_SIZE:
            return total
