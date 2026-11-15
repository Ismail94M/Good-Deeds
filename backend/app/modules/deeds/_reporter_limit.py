"""The monthly reporting limit (D16, docs/M2-DESIGN.md §3.2 #3).

A reporter may create at most ``reporter_monthly_limit`` deeds per IST calendar month, across
all doers and both paths (QR and invite). Every ``deeds`` row the reporter created that month
counts, whatever its status now, so withdrawing can't free a slot. A failed submission creates
no row, so it doesn't count. Self-logs are a different table and never count.
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import advisory_xact_lock
from app.core.errors import TooManyRequests
from app.modules.deeds._common import ist_month
from app.modules.deeds._limits import DeedLimits
from app.modules.deeds.models import Deed


def _limit_reached(limit: int) -> TooManyRequests:
    return TooManyRequests(
        f"You can report {limit} deeds a month. You can report again from the 1st.",
        code="monthly_report_limit",
    )


async def reported_this_month(session: AsyncSession, reporter_id: uuid.UUID, now: datetime) -> int:
    """Deeds the reporter created in the IST month of ``now`` (index
    ``ix_deeds_reporter_reported_at``)."""
    start, end = ist_month(now)
    stmt = select(func.count()).where(
        Deed.reporter_user_id == reporter_id,
        Deed.reported_at >= start,
        Deed.reported_at < end,
    )
    return int(await session.scalar(stmt) or 0)


async def ensure_under_monthly_limit(
    session: AsyncSession, reporter_id: uuid.UUID, limits: DeedLimits, now: datetime
) -> None:
    """The authoritative check, run before a ``deeds`` row is created. The advisory lock is
    held until the transaction ends, so concurrent submits by one reporter serialise and can't
    together pass the count. Take it before any row lock (the scan, the invite counter)."""
    await advisory_xact_lock(session, "reporter_month", str(reporter_id))
    if await reported_this_month(session, reporter_id, now) >= limits.reporter_monthly_limit:
        raise _limit_reached(limits.reporter_monthly_limit)


async def precheck_monthly_limit(
    session: AsyncSession, reporter_id: uuid.UUID, limits: DeedLimits, now: datetime
) -> None:
    """An early, unlocked check at scan time, so a witness who is already at the limit learns
    before a scan is recorded (a scan occupies the pair's IST day even if no deed follows)."""
    if await reported_this_month(session, reporter_id, now) >= limits.reporter_monthly_limit:
        raise _limit_reached(limits.reporter_monthly_limit)
