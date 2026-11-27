"""Scoring public interface (docs/M4-DESIGN.md §3.1), for M5 (fraud review), M6
(notifications) and M8 (prizes). Other modules may import only this file from scoring.

Points here are internal values: callers must not put them in a public response (D69).
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scoring import _badges
from app.modules.scoring._limits import Tier
from app.modules.scoring.events import (
    BadgeAwarded,
    BadgeRevoked,
    DeedNeedsReview,
    HeroTitleAwarded,
    HeroTitleRevoked,
    MonthFinalized,
    OverrideAppealApplied,
    PointsChanged,
    PointsOverridden,
    ReporterConcentrationExceeded,
)
from app.modules.scoring.models import (
    HeroTitle,
    LeaderboardEntry,
    LeaderboardSnapshot,
    LedgerKind,
    LedgerRole,
    PointsLedger,
    UserBadge,
)

__all__ = [
    "BadgeAwarded",
    "BadgeRevoked",
    "BoardEntry",
    "DeedNeedsReview",
    "FinalSnapshot",
    "HeroTitleAwarded",
    "HeroTitleRevoked",
    "LedgerEntryView",
    "MonthFinalized",
    "OverrideAppealApplied",
    "PointsChanged",
    "PointsOverridden",
    "ReporterConcentrationExceeded",
    "Tier",
    "TitleView",
    "active_entries",
    "badges_for",
    "doer_points",
    "final_snapshot",
    "tier_for",
    "titles_for",
]


async def doer_points(session: AsyncSession, user_id: uuid.UUID, period: date | None = None) -> int:
    """The user's net doer points: all time, or in one IST month (``period`` is its first
    day). Includes provisional points."""
    stmt = select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
        PointsLedger.user_id == user_id, PointsLedger.role == LedgerRole.DOER
    )
    if period is not None:
        stmt = stmt.where(PointsLedger.period_month == period.replace(day=1))
    return int(await session.scalar(stmt) or 0)


@dataclass(frozen=True)
class LedgerEntryView:
    id: uuid.UUID
    user_id: uuid.UUID
    deed_id: uuid.UUID
    role: str
    kind: str
    points: int
    deed_units: int
    counts_for_prize_rank: bool
    reason: str
    region_id: int
    verified_at: datetime


async def active_entries(session: AsyncSession, deed_id: uuid.UUID) -> list[LedgerEntryView]:
    """The deed's active (not reversed) award or decision per role."""
    rows = list(await session.scalars(select(PointsLedger).where(PointsLedger.deed_id == deed_id)))
    reversed_ids = {r.reverses_entry_id for r in rows if r.kind == LedgerKind.REVERSAL}
    return [
        LedgerEntryView(
            id=r.id,
            user_id=r.user_id,
            deed_id=r.deed_id,
            role=r.role.value,
            kind=r.kind.value,
            points=r.points,
            deed_units=r.deed_units,
            counts_for_prize_rank=r.counts_for_prize_rank,
            reason=r.reason.value,
            region_id=r.region_id,
            verified_at=r.verified_at,
        )
        for r in sorted(rows, key=lambda r: r.role.value)
        if r.kind != LedgerKind.REVERSAL and r.id not in reversed_ids
    ]


@dataclass(frozen=True)
class BoardEntry:
    user_id: uuid.UUID
    rank: int
    deed_count: int
    doer_points: int
    distinct_reporters: int
    distinct_days: int


@dataclass(frozen=True)
class FinalSnapshot:
    id: uuid.UUID
    region_id: int
    period_month: date
    computed_at: datetime
    entries: tuple[BoardEntry, ...]


async def final_snapshot(
    session: AsyncSession, region_id: int, month: date
) -> FinalSnapshot | None:
    """The frozen board of a finalized month (D64), entries by rank; None if not final."""
    snapshot = await session.scalar(
        select(LeaderboardSnapshot).where(
            LeaderboardSnapshot.region_id == region_id,
            LeaderboardSnapshot.period_month == month.replace(day=1),
            LeaderboardSnapshot.final.is_(True),
        )
    )
    if snapshot is None:
        return None
    entries = await session.scalars(
        select(LeaderboardEntry)
        .where(LeaderboardEntry.snapshot_id == snapshot.id)
        .order_by(LeaderboardEntry.rank, LeaderboardEntry.user_id)
    )
    return FinalSnapshot(
        id=snapshot.id,
        region_id=snapshot.region_id,
        period_month=snapshot.period_month,
        computed_at=snapshot.computed_at,
        entries=tuple(
            BoardEntry(
                user_id=e.user_id,
                rank=e.rank,
                deed_count=e.deed_count,
                doer_points=e.doer_points,
                distinct_reporters=e.distinct_reporters,
                distinct_days=e.distinct_days,
            )
            for e in entries
        ),
    )


@dataclass(frozen=True)
class TitleView:
    period_month: date
    region_id: int
    level_kind: str
    place: int
    title_key: str


async def titles_for(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[TitleView]]:
    """Unrevoked Kindness Hero titles per user, newest month first (every id is a key)."""
    ids = set(user_ids)
    out: dict[uuid.UUID, list[TitleView]] = {uid: [] for uid in ids}
    if not ids:
        return out
    rows = await session.scalars(
        select(HeroTitle)
        .where(HeroTitle.user_id.in_(ids), HeroTitle.revoked_at.is_(None))
        .order_by(HeroTitle.period_month.desc(), HeroTitle.place)
    )
    for t in rows:
        out[t.user_id].append(
            TitleView(t.period_month, t.region_id, t.level_kind.value, t.place, t.title_key)
        )
    return out


async def badges_for(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Unrevoked badge slugs per user, in award order (every id is a key)."""
    ids = set(user_ids)
    out: dict[uuid.UUID, list[str]] = {uid: [] for uid in ids}
    if not ids:
        return out
    rows = await session.execute(
        select(UserBadge.user_id, UserBadge.badge_slug)
        .where(UserBadge.user_id.in_(ids), UserBadge.revoked_at.is_(None))
        .order_by(UserBadge.awarded_at, UserBadge.badge_slug)
    )
    for user_id, slug in rows:
        out[user_id].append(slug)
    return out


async def tier_for(
    session: AsyncSession, user_ids: Iterable[uuid.UUID], *, now: datetime | None = None
) -> dict[uuid.UUID, Tier]:
    """The public tier per user, from non-provisional all-time doer points (§4.6)."""
    return await _badges.tiers_for(session, user_ids, now=now or datetime.now(UTC))
