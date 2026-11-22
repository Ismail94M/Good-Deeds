"""Monthly finalization and Kindness Hero titles (docs/M4-DESIGN.md D64, D67, D70, §4.6).

``finalize_month(month)`` freezes a month once nothing in it can move any more:

1. ``now >= cutoff``: the first day of the next month 00:00 (IST) + ``withdraw_window_hours``
   + ``finalize_margin_minutes``. November finalizes at about 2 December 01:00 IST.
2. ``core.events.unprocessed_before(<scoring handlers>, cutoff) == 0``: every outbox event from
   before the cutoff has been handled by every scoring handler (closes the outbox delay; a
   stuck event blocks finalization rather than finalizing wrong, and admin #10 shows it).

It then, in one transaction under the advisory lock ``finalize_month:(month)`` and only if no
``month_finalizations`` row exists: writes a final snapshot of every board of every enabled
level, awards titles, writes ``month_finalizations`` and emits ``MonthFinalized`` and one
``HeroTitleAwarded`` per title. Running it again does nothing. It never takes the ledger's day
locks (§4.1).

Titles: for each final board and each title of its level, every user on the title's places
(dense shared places included, PO Q58) who meets the level's minimums (counted deeds and
distinct reporters on that board: the ward minimum is 3 deeds from 2 reporters, PO Q54). A
place nobody qualifies for has no title; it never passes to the next person.

Never imports or reads AI evaluations (D62).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import advisory_xact_lock
from app.core.events import emit, handler_names_with_prefix, unprocessed_before
from app.modules.deeds import service as deeds
from app.modules.scoring._leaderboard import (
    BoardRow,
    compute_boards,
    month_of,
    next_month,
    write_snapshot,
)
from app.modules.scoring._limits import LevelRule, load_leaderboard, load_scoring
from app.modules.scoring.events import HeroTitleAwarded, MonthFinalized
from app.modules.scoring.models import HeroTitle, MonthFinalization, PointsLedger, TitleLevel

log = logging.getLogger(__name__)

SCORING_HANDLER_PREFIX = "app.modules.scoring."


class Outcome(StrEnum):
    FINALIZED = "finalized"
    ALREADY_FINALIZED = "already_finalized"
    TOO_EARLY = "too_early"
    EVENTS_PENDING = "events_pending"


@dataclass(frozen=True)
class FinalizeResult:
    month: date
    outcome: Outcome
    cutoff_at: datetime
    pending_events: int = 0
    snapshots: int = 0
    titles: int = 0


async def cutoff_for(session: AsyncSession, month: date) -> datetime:
    """When ``month`` may be finalized (step 1)."""
    _, scoring = await load_scoring(session)
    _, config = await load_leaderboard(session)
    window = await deeds.withdraw_window_hours(session)
    month_end = datetime.combine(next_month(month), time(0), tzinfo=scoring.zone)
    return (month_end + timedelta(hours=window, minutes=config.finalize_margin_minutes)).astimezone(
        UTC
    )


async def pending_scoring_events(session: AsyncSession, cutoff: datetime) -> int:
    """Outbox events from before ``cutoff`` some scoring handler has not processed yet."""
    return await unprocessed_before(
        session, handler_names_with_prefix(SCORING_HANDLER_PREFIX), cutoff
    )


def qualifies(row: BoardRow, level: LevelRule) -> bool:
    return (
        row.deed_count >= level.min_counted_deeds
        and row.distinct_reporters >= level.min_distinct_reporters
    )


async def finalize_month(
    session: AsyncSession, month: date, *, now: datetime | None = None
) -> FinalizeResult:
    """Finalize ``month`` if it is due (see the module docstring). The caller commits."""
    now = now or datetime.now(UTC)
    await advisory_xact_lock(session, "finalize_month", month.isoformat())
    cutoff = await cutoff_for(session, month)
    if await session.get(MonthFinalization, month, populate_existing=True) is not None:
        return FinalizeResult(month, Outcome.ALREADY_FINALIZED, cutoff)
    if now < cutoff:
        return FinalizeResult(month, Outcome.TOO_EARLY, cutoff)
    pending = await pending_scoring_events(session, cutoff)
    if pending > 0:
        log.warning("finalize_month %s waits for %d scoring events", month, pending)
        return FinalizeResult(month, Outcome.EVENTS_PENDING, cutoff, pending_events=pending)

    version, config = await load_leaderboard(session)
    boards = await compute_boards(session, month, config)
    titles = 0
    for region_id in sorted(boards):
        level, rows = boards[region_id]
        snapshot = await write_snapshot(
            session,
            region_id=region_id,
            month=month,
            rows=rows,
            config_version=version,
            final=True,
            now=now,
        )
        for title in level.titles:
            for row in rows:
                if row.rank not in title.places or not qualifies(row, level):
                    continue
                session.add(
                    HeroTitle(
                        period_month=month,
                        region_id=region_id,
                        level_kind=TitleLevel(level.kind),
                        place=row.rank,
                        title_key=title.key,
                        user_id=row.user_id,
                        snapshot_id=snapshot.id,
                        awarded_at=now,
                        config_version=version,
                    )
                )
                emit(
                    session,
                    HeroTitleAwarded(
                        user_id=row.user_id,
                        period_month=month,
                        region_id=region_id,
                        level_kind=level.kind,
                        place=row.rank,
                        title_key=title.key,
                    ),
                )
                titles += 1
    session.add(
        MonthFinalization(
            period_month=month,
            cutoff_at=cutoff,
            finalized_at=now,
            config_version=version,
            snapshot_count=len(boards),
            titles_awarded=titles,
        )
    )
    emit(session, MonthFinalized(period_month=month))
    await session.flush()
    log.info("finalized %s: %d boards, %d titles", month, len(boards), titles)
    return FinalizeResult(month, Outcome.FINALIZED, cutoff, snapshots=len(boards), titles=titles)


async def oldest_open_month(session: AsyncSession, now: datetime) -> date | None:
    """The oldest past month with ledger rows and no ``month_finalizations`` row."""
    _, scoring = await load_scoring(session)
    current = month_of(now, scoring.zone)
    return await session.scalar(
        select(func.min(PointsLedger.period_month)).where(
            PointsLedger.period_month < current,
            ~select(MonthFinalization.period_month)
            .where(MonthFinalization.period_month == PointsLedger.period_month)
            .exists(),
        )
    )


async def finalize_due(
    session: AsyncSession, *, now: datetime | None = None
) -> FinalizeResult | None:
    """The ``scoring.finalize_month`` job: try the oldest open past month (months finalize in
    order, so a blocked month holds the later ones back). None when there is nothing to do."""
    now = now or datetime.now(UTC)
    month = await oldest_open_month(session, now)
    if month is None:
        return None
    return await finalize_month(session, month, now=now)
