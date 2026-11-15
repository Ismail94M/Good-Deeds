"""Self-logs (D3, §2.7, §3.2 #11) and the public profile counts (D4, D12, §3.2 #13).

A self-log is a private record of the owner's own deed. It has no link to deeds, scans or
same-act groups, emits no events and is never read by grouping, scoring or prize code. Every
call is owner-only: another user's log is reported as not found (404), never forbidden.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFound
from app.core.ratelimit import RateLimiter
from app.modules.deeds import _cursor
from app.modules.deeds._common import point_ewkt, utcnow
from app.modules.deeds._limits import DeedLimits
from app.modules.deeds._qr_deeds import validate_content
from app.modules.deeds.models import Deed, DeedStatus, SelfLog
from app.modules.deeds.schemas import (
    DeedCountsOut,
    SelfLogCreate,
    SelfLogOut,
    SelfLogPage,
    SelfLogUpdate,
)
from app.modules.identity import service as identity

# §2.7: any past date; at most 5 minutes in the future (a fixed design rule, not a PRD limit).
FUTURE_TOLERANCE = timedelta(minutes=5)


def _not_found() -> NotFound:
    return NotFound("We couldn't find that log.", code="self_log_not_found")


def _out(log: SelfLog) -> SelfLogOut:
    return SelfLogOut(
        id=log.id,
        category=log.category,
        description=log.description,
        occurred_at=log.occurred_at,
        has_location=log.location is not None,
        created_at=log.created_at,
        updated_at=log.updated_at,
    )


def _check_occurred_at(occurred_at: datetime, now: datetime) -> None:
    if occurred_at > now + FUTURE_TOLERANCE:
        raise AppError("That date is in the future.", code="implausible_time")


async def enforce_write_limits(
    limiter: RateLimiter, limits: DeedLimits, user_id: uuid.UUID, ip: str
) -> None:
    """Account and IP limits on every self-log write (D1). Runs before any lookup."""
    await limiter.hit(f"selflog:u:{user_id}", limits.rate_self_log_user)
    await limiter.hit(f"selflog:ip:{ip}", limits.rate_self_log_ip)


async def _owned(session: AsyncSession, owner_id: uuid.UUID, log_id: uuid.UUID) -> SelfLog:
    log = await session.scalar(
        select(SelfLog).where(SelfLog.id == log_id, SelfLog.user_id == owner_id)
    )
    if log is None:
        raise _not_found()
    return log


async def create(
    session: AsyncSession,
    owner_id: uuid.UUID,
    data: SelfLogCreate,
    *,
    limits: DeedLimits,
    now: datetime | None = None,
) -> SelfLogOut:
    now = now or utcnow()
    description = validate_content(limits, data.category, data.description, None)
    _check_occurred_at(data.occurred_at, now)
    log = SelfLog(
        user_id=owner_id,
        category=data.category,
        description=description,
        occurred_at=data.occurred_at,
        location=(
            None if data.location is None else point_ewkt(data.location.lat, data.location.lng)
        ),
    )
    session.add(log)
    await session.flush()
    await session.refresh(log)
    return _out(log)


async def list_for_owner(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    cursor: str | None = None,
    limit: int = _cursor.DEFAULT_PAGE_SIZE,
) -> SelfLogPage:
    stmt = select(SelfLog).where(SelfLog.user_id == owner_id)
    if cursor is not None:
        at, last_id = _cursor.decode(cursor)
        stmt = stmt.where(tuple_(SelfLog.created_at, SelfLog.id) < tuple_(at, last_id))
    rows = list(
        await session.scalars(
            stmt.order_by(SelfLog.created_at.desc(), SelfLog.id.desc()).limit(limit + 1)
        )
    )
    page, more = rows[:limit], len(rows) > limit
    return SelfLogPage(
        items=[_out(log) for log in page],
        next_cursor=_cursor.encode(page[-1].created_at, page[-1].id) if more else None,
    )


async def update(
    session: AsyncSession,
    owner_id: uuid.UUID,
    log_id: uuid.UUID,
    data: SelfLogUpdate,
    *,
    limits: DeedLimits,
    now: datetime | None = None,
) -> SelfLogOut:
    now = now or utcnow()
    log = await _owned(session, owner_id, log_id)
    # Only changed fields are checked against the config, so retiring a category later doesn't
    # stop the owner from editing an old log's other fields (same rule as deed edits).
    if data.category is not None and data.category != log.category:
        if not limits.has_category(data.category):
            raise AppError("Please choose a category from the list.", code="invalid_category")
        log.category = data.category
    if data.description is not None:
        text = data.description.strip()
        if text != log.description:
            if not limits.description_min <= len(text) <= limits.description_max:
                raise AppError(
                    f"Please describe the deed in {limits.description_min} to "
                    f"{limits.description_max} characters.",
                    code="invalid_description",
                )
            log.description = text
    if data.occurred_at is not None:
        _check_occurred_at(data.occurred_at, now)
        log.occurred_at = data.occurred_at
    if "location" in data.model_fields_set:
        loc = data.location
        log.location = None if loc is None else point_ewkt(loc.lat, loc.lng)  # type: ignore[assignment]
    if session.is_modified(log):
        log.updated_at = now
    await session.flush()
    await session.refresh(log)
    return _out(log)


async def delete_log(session: AsyncSession, owner_id: uuid.UUID, log_id: uuid.UUID) -> None:
    result = await session.execute(
        delete(SelfLog)
        .where(SelfLog.id == log_id, SelfLog.user_id == owner_id)
        .returning(SelfLog.id)
    )
    if result.first() is None:
        raise _not_found()


# --------------------------------------------------- profile counts (§3.2 #13)


async def deed_counts(session: AsyncSession, handle: str) -> DeedCountsOut:
    """Both public profile counts. ``verified`` counts reports, not acts (D12): five witnesses
    of one act count five. Self-logs never contribute to it."""
    user = await identity.get_user_by_handle(session, handle)
    if user is None or user.status != "active":
        raise NotFound("We couldn't find that person.", code="user_not_found")
    self_logged = await session.scalar(
        select(func.count()).select_from(SelfLog).where(SelfLog.user_id == user.id)
    )
    verified = await session.scalar(
        select(func.count())
        .select_from(Deed)
        .where(Deed.doer_user_id == user.id, Deed.status == DeedStatus.VERIFIED)
    )
    return DeedCountsOut(self_logged=int(self_logged or 0), verified=int(verified or 0))
