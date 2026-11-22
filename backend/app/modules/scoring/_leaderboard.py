"""Leaderboards: one snapshot per (region, month) (docs/M4-DESIGN.md D59, D66, D67, §4.5).

A board is built from doer ledger rows that count for prize rank (reporter rows never do, D57)
in one IST month (``period_month``, D58):

1. Per deed, the net of its rows (awards minus reversals). A deed is counted when its net
   ``deed_units`` is positive; its points are the net points.
2. Each counted deed counts once per level (D66): its frozen leaf ``region_id`` is mapped to
   its one ancestor of that level through ``region_closure`` (``deeds.region_ancestor_map``). A
   deed without a ward was frozen at the city, so it never reaches a ward board.
3. Per (region, user): ``deed_count``, ``doer_points``, distinct reporters and distinct IST
   days of the counted deeds. Only active accounts are kept.
4. ``rank_score = weights.deeds * deed_count + weights.points * doer_points``; order by rank
   score, then distinct reporters, then distinct days (PO Q50); a full tie is a shared rank,
   numbered per ``shared_rank_style`` (dense 1, 1, 2 by PO Q58).

Snapshots stay provisional until ``finalize_month`` writes the final ones (D64). Reads prefer a
final snapshot and otherwise take the newest. Refresh takes no locks (it reads committed rows);
the job's queueing lock keeps one refresh at a time.

Never imports or reads AI evaluations (D62).
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import service as deeds
from app.modules.identity import service as identity
from app.modules.scoring._limits import LeaderboardConfig, LevelRule, load_leaderboard, load_scoring
from app.modules.scoring.models import (
    LeaderboardEntry,
    LeaderboardSnapshot,
    LedgerRole,
    MonthFinalization,
    PointsLedger,
)

# Non-final snapshots kept per (region, month), so a reader paging the previous one can finish.
KEEP_SNAPSHOTS = 2


def month_of(at: datetime, zone: ZoneInfo) -> date:
    """The first day of ``at``'s month in ``zone``."""
    return at.astimezone(zone).date().replace(day=1)


def previous_month(month: date) -> date:
    return (month - timedelta(days=1)).replace(day=1)


def next_month(month: date) -> date:
    return (month + timedelta(days=32)).replace(day=1)


@dataclass(frozen=True)
class _CountedDeed:
    user_id: uuid.UUID
    region_id: int
    reporter_id: uuid.UUID | None
    earned_on: date
    points: int


@dataclass(frozen=True)
class BoardRow:
    user_id: uuid.UUID
    rank: int
    rank_score: int
    deed_count: int
    doer_points: int
    distinct_reporters: int
    distinct_days: int

    def sort_key(self) -> tuple[int, int, int]:
        """Rank score, then distinct reporters, then distinct days (D67); higher first."""
        return (-self.rank_score, -self.distinct_reporters, -self.distinct_days)


async def _counted_deeds(session: AsyncSession, month: date) -> list[_CountedDeed]:
    """Each deed counted for prize rank in ``month``, with its net points (step 1)."""
    rows = await session.execute(
        select(
            PointsLedger.user_id,
            PointsLedger.region_id,
            PointsLedger.counterpart_user_id,
            PointsLedger.earned_on,
            func.sum(PointsLedger.points),
        )
        .where(
            PointsLedger.role == LedgerRole.DOER,
            PointsLedger.counts_for_prize_rank.is_(True),
            PointsLedger.period_month == month,
        )
        .group_by(
            PointsLedger.deed_id,
            PointsLedger.user_id,
            PointsLedger.region_id,
            PointsLedger.counterpart_user_id,
            PointsLedger.earned_on,
        )
        .having(func.sum(PointsLedger.deed_units) > 0)
    )
    return [
        _CountedDeed(user, region, reporter, day, int(points))
        for user, region, reporter, day, points in rows
    ]


def rank_rows(
    stats: Iterable[tuple[uuid.UUID, int, int, int, int]],
    config: LeaderboardConfig,
) -> list[BoardRow]:
    """Rank ``(user_id, deed_count, doer_points, distinct_reporters, distinct_days)`` tuples per
    D67 and ``shared_rank_style``. Users on a shared rank are ordered by id (stable paging)."""
    rows = [
        BoardRow(
            user_id=user,
            rank=0,
            rank_score=config.weights.deeds * count + config.weights.points * pts,
            deed_count=count,
            doer_points=pts,
            distinct_reporters=reporters,
            distinct_days=days,
        )
        for user, count, pts, reporters, days in stats
    ]
    rows.sort(key=lambda r: (r.sort_key(), str(r.user_id)))
    ranked: list[BoardRow] = []
    rank = 0
    previous: tuple[int, int, int] | None = None
    for position, row in enumerate(rows, start=1):
        key = row.sort_key()
        if key != previous:
            rank = rank + 1 if config.shared_rank_style == "dense" else position
            previous = key
        ranked.append(
            BoardRow(
                user_id=row.user_id,
                rank=rank,
                rank_score=row.rank_score,
                deed_count=row.deed_count,
                doer_points=row.doer_points,
                distinct_reporters=row.distinct_reporters,
                distinct_days=row.distinct_days,
            )
        )
    return ranked


def enabled_levels(config: LeaderboardConfig) -> list[LevelRule]:
    return [level for level in config.levels if level.enabled]


async def compute_boards(
    session: AsyncSession, month: date, config: LeaderboardConfig
) -> dict[int, tuple[LevelRule, list[BoardRow]]]:
    """Every board of ``month`` with at least one counted deed, by region id, for each enabled
    level (steps 2 to 4)."""
    counted = await _counted_deeds(session, month)
    active = await identity.active_user_ids(session, {d.user_id for d in counted})
    counted = [d for d in counted if d.user_id in active]
    boards: dict[int, tuple[LevelRule, list[BoardRow]]] = {}
    for level in enabled_levels(config):
        ancestor_of = await deeds.region_ancestor_map(session, deeds.RegionKind(level.kind))
        groups: dict[tuple[int, uuid.UUID], list[_CountedDeed]] = defaultdict(list)
        for deed in counted:
            ancestor = ancestor_of.get(deed.region_id)
            if ancestor is not None:  # a city deed has no ward ancestor
                groups[(ancestor, deed.user_id)].append(deed)
        per_region: dict[int, list[tuple[uuid.UUID, int, int, int, int]]] = defaultdict(list)
        for (region_id, user_id), items in groups.items():
            per_region[region_id].append(
                (
                    user_id,
                    len(items),
                    sum(d.points for d in items),
                    len({d.reporter_id for d in items if d.reporter_id is not None}),
                    len({d.earned_on for d in items}),
                )
            )
        for region_id, stats in per_region.items():
            boards[region_id] = (level, rank_rows(stats, config))
    return boards


async def write_snapshot(
    session: AsyncSession,
    *,
    region_id: int,
    month: date,
    rows: Sequence[BoardRow],
    config_version: int,
    final: bool,
    now: datetime,
) -> LeaderboardSnapshot:
    snapshot = LeaderboardSnapshot(
        id=uuid.uuid4(),
        region_id=region_id,
        period_month=month,
        computed_at=now,
        config_version=config_version,
        final=final,
        entry_count=len(rows),
    )
    session.add(snapshot)
    await session.flush()
    if rows:
        await session.execute(
            insert(LeaderboardEntry),
            [
                {
                    "snapshot_id": snapshot.id,
                    "user_id": r.user_id,
                    "rank": r.rank,
                    "rank_score": r.rank_score,
                    "deed_count": r.deed_count,
                    "doer_points": r.doer_points,
                    "distinct_reporters": r.distinct_reporters,
                    "distinct_days": r.distinct_days,
                }
                for r in rows
            ],
        )
    return snapshot


async def is_finalized(session: AsyncSession, month: date) -> bool:
    return await session.get(MonthFinalization, month) is not None


async def refresh_month(
    session: AsyncSession,
    month: date,
    *,
    config: LeaderboardConfig,
    config_version: int,
    now: datetime,
) -> int:
    """Write a provisional snapshot of every board of ``month`` and prune old ones. A region
    that had a provisional board but no longer has any counted deed gets an empty snapshot, so
    a board never shows deeds that were since withdrawn. Returns the snapshots written."""
    boards = await compute_boards(session, month, config)
    had_board = set(
        await session.scalars(
            select(LeaderboardSnapshot.region_id)
            .where(LeaderboardSnapshot.period_month == month, LeaderboardSnapshot.final.is_(False))
            .distinct()
        )
    )
    written = 0
    for region_id in sorted(set(boards) | had_board):
        rows = boards[region_id][1] if region_id in boards else []
        await write_snapshot(
            session,
            region_id=region_id,
            month=month,
            rows=rows,
            config_version=config_version,
            final=False,
            now=now,
        )
        written += 1
    await _prune(session, month)
    return written


async def _prune(session: AsyncSession, month: date) -> None:
    """Keep the newest ``KEEP_SNAPSHOTS`` provisional snapshots per region of ``month``."""
    ranked = (
        select(
            LeaderboardSnapshot.id,
            func.row_number()
            .over(
                partition_by=LeaderboardSnapshot.region_id,
                order_by=(LeaderboardSnapshot.computed_at.desc(), LeaderboardSnapshot.id.desc()),
            )
            .label("n"),
        )
        .where(LeaderboardSnapshot.period_month == month, LeaderboardSnapshot.final.is_(False))
        .subquery()
    )
    await session.execute(
        delete(LeaderboardSnapshot).where(
            LeaderboardSnapshot.id.in_(select(ranked.c.id).where(ranked.c.n > KEEP_SNAPSHOTS))
        )
    )


async def refresh_due(session: AsyncSession, *, now: datetime | None = None) -> list[date]:
    """The ``scoring.refresh_leaderboards`` job: refresh each open month (the current one, and
    the previous one until it is finalized) whose newest snapshot is at least
    ``refresh_seconds`` old. Returns the months where snapshots were written (a month with no
    board and no counted deed writes none)."""
    now = now or datetime.now(UTC)
    _, scoring = await load_scoring(session)
    version, config = await load_leaderboard(session)
    current = month_of(now, scoring.zone)
    done: list[date] = []
    for month in (previous_month(current), current):
        if await is_finalized(session, month):
            continue
        latest = await session.scalar(
            select(func.max(LeaderboardSnapshot.computed_at)).where(
                LeaderboardSnapshot.period_month == month
            )
        )
        if latest is not None and now - latest < timedelta(seconds=config.refresh_seconds):
            continue
        if await refresh_month(session, month, config=config, config_version=version, now=now):
            done.append(month)
    return done


# --------------------------------------------------- reads


async def latest_snapshot(
    session: AsyncSession, region_id: int, month: date
) -> LeaderboardSnapshot | None:
    """The final snapshot of the board if there is one, else the newest provisional one."""
    return await session.scalar(
        select(LeaderboardSnapshot)
        .where(
            LeaderboardSnapshot.region_id == region_id, LeaderboardSnapshot.period_month == month
        )
        .order_by(
            LeaderboardSnapshot.final.desc(),
            LeaderboardSnapshot.computed_at.desc(),
            LeaderboardSnapshot.id.desc(),
        )
        .limit(1)
    )


async def user_board_entries(
    session: AsyncSession, user_id: uuid.UUID, months: Sequence[date]
) -> list[tuple[LeaderboardSnapshot, LeaderboardEntry]]:
    """The user's row on the current snapshot (final, else newest) of every board of
    ``months`` they are on."""
    current = (
        select(LeaderboardSnapshot.id)
        .where(LeaderboardSnapshot.period_month.in_(months))
        .ext(distinct_on(LeaderboardSnapshot.region_id, LeaderboardSnapshot.period_month))
        .order_by(
            LeaderboardSnapshot.region_id,
            LeaderboardSnapshot.period_month,
            LeaderboardSnapshot.final.desc(),
            LeaderboardSnapshot.computed_at.desc(),
            LeaderboardSnapshot.id.desc(),
        )
        .subquery()
    )
    rows = await session.execute(
        select(LeaderboardSnapshot, LeaderboardEntry)
        .join(LeaderboardEntry, LeaderboardEntry.snapshot_id == LeaderboardSnapshot.id)
        .where(
            LeaderboardSnapshot.id.in_(select(current.c.id)), LeaderboardEntry.user_id == user_id
        )
        .order_by(LeaderboardSnapshot.period_month.desc(), LeaderboardSnapshot.region_id)
    )
    return [(snapshot, entry) for snapshot, entry in rows]
