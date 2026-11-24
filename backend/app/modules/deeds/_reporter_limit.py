"""The monthly reporting limits (D16 rules, D19 values; docs/M2-DESIGN.md §3.2 #1, #3).

Per IST calendar month a reporter may create at most ``reporter_monthly_limit`` deeds in total,
across all doers and both paths (QR and invite), and at most ``reporter_doer_monthly_limit``
deeds about any one doer. Every ``deeds`` row the reporter created that month counts, whatever
its status now, so withdrawing can't free a slot. A failed submission creates no row, so it
doesn't count. Self-logs are a different table and never count.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

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


def pair_limit_reached(limit: int, extra: Mapping[str, Any] | None = None) -> TooManyRequests:
    """The ``detail`` never names the doer; at scan time ``extra`` carries ``doer`` (D27)."""
    return TooManyRequests(
        f"You can report the same person {limit} times a month. "
        "You can report them again from the 1st.",
        code="pair_monthly_limit",
        extra=extra,
    )


async def reported_this_month(
    session: AsyncSession,
    reporter_id: uuid.UUID,
    now: datetime,
    *,
    doer_id: uuid.UUID | None = None,
) -> int:
    """Deeds the reporter created in the IST month of ``now``; only those about ``doer_id`` when
    it is given (index ``ix_deeds_reporter_reported_at``)."""
    start, end = ist_month(now)
    stmt = select(func.count()).where(
        Deed.reporter_user_id == reporter_id,
        Deed.reported_at >= start,
        Deed.reported_at < end,
    )
    if doer_id is not None:
        stmt = stmt.where(Deed.doer_user_id == doer_id)
    return int(await session.scalar(stmt) or 0)


async def pair_reports_remaining(
    session: AsyncSession,
    reporter_id: uuid.UUID,
    doer_id: uuid.UUID,
    limits: DeedLimits,
    now: datetime,
) -> int:
    """Reports the reporter may still create about ``doer_id`` this IST month (D20). Unlocked
    and informational; the locked check on deed create is the authoritative one."""
    used = await reported_this_month(session, reporter_id, now, doer_id=doer_id)
    return max(0, limits.reporter_doer_monthly_limit - used)


async def ensure_under_monthly_limits(
    session: AsyncSession,
    reporter_id: uuid.UUID,
    doer_id: uuid.UUID | None,
    limits: DeedLimits,
    now: datetime,
) -> None:
    """The authoritative checks, run before a ``deeds`` row is created: the total first, then
    (when the doer is known; ``None`` on invite create) the pair. The advisory lock is held until
    the transaction ends and every deed this reporter creates takes it, so concurrent submits by
    one reporter serialise and can't together pass either count. Take it before any row lock
    (the scan, the invite counter)."""
    await advisory_xact_lock(session, "reporter_month", str(reporter_id))
    if await reported_this_month(session, reporter_id, now) >= limits.reporter_monthly_limit:
        raise _limit_reached(limits.reporter_monthly_limit)
    if doer_id is not None and not await pair_reports_remaining(
        session, reporter_id, doer_id, limits, now
    ):
        raise pair_limit_reached(limits.reporter_doer_monthly_limit)


async def precheck_monthly_limit(
    session: AsyncSession, reporter_id: uuid.UUID, limits: DeedLimits, now: datetime
) -> None:
    """An early, unlocked check of the total at scan time, so a witness who is already at the
    limit learns before a scan is recorded (a scan occupies the pair's IST day even if no deed
    follows)."""
    if await reported_this_month(session, reporter_id, now) >= limits.reporter_monthly_limit:
        raise _limit_reached(limits.reporter_monthly_limit)
