"""Data retention for the deeds module (B9; PRD Q8: unconsumed scans are kept 7 days).

An unconsumed scan holds a precise witness location but backs no deed. Once its submit window
has closed it can never be consumed (the consume UPDATE requires ``expires_at > now``), so
deleting it can't race a deed submission. Consumed scans are evidence for their deed and stay.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds._common import utcnow
from app.modules.deeds._limits import load_limits
from app.modules.deeds.models import QrScan

PURGE_BATCH_SIZE = 1000


async def purge_unconsumed_scans_batch(
    session: AsyncSession, *, now: datetime | None = None, batch_size: int = PURGE_BATCH_SIZE
) -> int:
    """Delete up to ``batch_size`` unconsumed scans whose submit window closed more than
    ``unconsumed_scan_retention_days`` ago. Returns the number deleted.

    Uses the partial index on ``expires_at WHERE consumed_at IS NULL``; ``SKIP LOCKED`` lets a
    concurrent run (or a submit that is checking the row) pass without waiting.
    """
    _, limits = await load_limits(session)
    cutoff = (now or utcnow()) - timedelta(days=limits.unconsumed_scan_retention_days)
    # A MATERIALIZED CTE runs the locking LIMIT query exactly once. As a plain ``IN (subquery)``
    # the planner may pick a nested-loop semi join that re-runs it per outer row, deleting more
    # than ``batch_size`` rows (seen with small table statistics).
    batch = (
        select(QrScan.id)
        .where(QrScan.consumed_at.is_(None), QrScan.expires_at < cutoff)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .cte("batch")
        .prefix_with("MATERIALIZED")
    )
    result = await session.execute(
        delete(QrScan)
        .where(QrScan.id.in_(select(batch.c.id)))
        .returning(QrScan.id)
        .execution_options(synchronize_session=False)
    )
    return len(result.all())
