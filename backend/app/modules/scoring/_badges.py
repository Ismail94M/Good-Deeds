"""Badges and the public tier (docs/M4-DESIGN.md D70, §4.6, PO Q53, Q55).

Both read only **non-provisional** ledger rows: rows of deeds whose withdrawal window
(``verified_at + withdraw_window_hours``) has ended, so nothing here moves once shown.

- **Badges** (``scoring.award_badges``): ``doer_deeds`` badges count the user's counted deeds
  (net ``deed_units`` of doer rows), ``reports`` badges the reports that earned reporter points
  (net awards of reporter rows). A missing badge whose threshold is reached is inserted once
  (unique per user and badge), with the deed that crossed the threshold, and ``BadgeAwarded`` is
  emitted. Badges are permanent: later reversals never remove one (only an M5 revocation).
- **Tier:** computed on read from non-provisional all-time doer points.
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit
from app.modules.deeds import service as deeds
from app.modules.scoring._limits import Badge, LeaderboardConfig, Tier, load_leaderboard
from app.modules.scoring.events import BadgeAwarded
from app.modules.scoring.models import LedgerRole, PointsLedger, UserBadge


async def final_before(session: AsyncSession, now: datetime) -> datetime:
    """Rows verified at or before this moment are past their window (non-provisional)."""
    return now - timedelta(hours=await deeds.withdraw_window_hours(session))


def _role(badge: Badge) -> LedgerRole:
    return LedgerRole.DOER if badge.kind == "doer_deeds" else LedgerRole.REPORTER


async def _crossing_deed(
    session: AsyncSession, user_id: uuid.UUID, badge: Badge, cutoff: datetime
) -> uuid.UUID | None:
    """The deed that brought the user's count to the badge threshold: the threshold-th of the
    deeds that still count, in verification order."""
    # Doer deeds: net deed units. Reports: an award adds 1, its reversal takes it back
    # (decisions are 0 points).
    net = (
        func.sum(PointsLedger.deed_units)
        if badge.kind == "doer_deeds"
        else func.sum(func.sign(PointsLedger.points))
    )
    rows = await session.execute(
        select(PointsLedger.deed_id)
        .where(
            PointsLedger.user_id == user_id,
            PointsLedger.role == _role(badge),
            PointsLedger.verified_at <= cutoff,
        )
        .group_by(PointsLedger.deed_id, PointsLedger.verified_at)
        .having(net > 0)
        .order_by(PointsLedger.verified_at, PointsLedger.deed_id)
        .offset(badge.threshold - 1)
        .limit(1)
    )
    return rows.scalar()


async def award_badges(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Insert every badge now earned and not yet held. Idempotent. Returns badges awarded."""
    now = now or datetime.now(UTC)
    version, config = await load_leaderboard(session)
    if not config.badges:
        return 0
    cutoff = await final_before(session, now)
    doer_deeds = func.sum(
        case((PointsLedger.role == LedgerRole.DOER, PointsLedger.deed_units), else_=0)
    )
    reports = func.sum(
        case((PointsLedger.role == LedgerRole.REPORTER, func.sign(PointsLedger.points)), else_=0)
    )
    totals = await session.execute(
        select(PointsLedger.user_id, doer_deeds, reports)
        .where(PointsLedger.verified_at <= cutoff)
        .group_by(PointsLedger.user_id)
    )
    held: dict[uuid.UUID, set[str]] = {}
    awarded = 0
    for user_id, deed_total, report_total in totals:
        counts = {"doer_deeds": int(deed_total or 0), "reports": int(report_total or 0)}
        due = [b for b in config.badges if counts[b.kind] >= b.threshold]
        if not due:
            continue
        if user_id not in held:
            held[user_id] = set(
                await session.scalars(
                    select(UserBadge.badge_slug).where(UserBadge.user_id == user_id)
                )
            )
        for badge in due:
            if badge.slug in held[user_id]:
                continue
            trigger = await _crossing_deed(session, user_id, badge, cutoff)
            inserted = await session.scalar(
                insert(UserBadge)
                .values(
                    user_id=user_id,
                    badge_slug=badge.slug,
                    awarded_at=now,
                    config_version=version,
                    trigger_deed_id=trigger,
                )
                .on_conflict_do_nothing(constraint="uq_user_badges_user_badge")
                .returning(UserBadge.id)
            )
            held[user_id].add(badge.slug)
            if inserted is not None:
                emit(
                    session,
                    BadgeAwarded(user_id=user_id, badge_slug=badge.slug, badge_label=badge.label),
                )
                awarded += 1
    await session.flush()
    return awarded


# --------------------------------------------------- tier


def tier_of(points: int, config: LeaderboardConfig) -> Tier:
    """The highest tier whose ``min_points`` the points reach (the first tier starts at 0)."""
    current = config.tiers[0]
    for tier in config.tiers:
        if points >= tier.min_points:
            current = tier
    return current


async def final_doer_points(
    session: AsyncSession, user_ids: Iterable[uuid.UUID], *, now: datetime | None = None
) -> dict[uuid.UUID, int]:
    """Non-provisional all-time doer points per user (0 for users without any)."""
    ids = set(user_ids)
    if not ids:
        return {}
    cutoff = await final_before(session, now or datetime.now(UTC))
    rows = await session.execute(
        select(PointsLedger.user_id, func.sum(PointsLedger.points))
        .where(
            PointsLedger.user_id.in_(ids),
            PointsLedger.role == LedgerRole.DOER,
            PointsLedger.verified_at <= cutoff,
        )
        .group_by(PointsLedger.user_id)
    )
    totals = {user_id: int(total or 0) for user_id, total in rows}
    return {user_id: totals.get(user_id, 0) for user_id in ids}


async def tiers_for(
    session: AsyncSession, user_ids: Iterable[uuid.UUID], *, now: datetime | None = None
) -> dict[uuid.UUID, Tier]:
    _, config = await load_leaderboard(session)
    points = await final_doer_points(session, user_ids, now=now)
    return {user_id: tier_of(total, config) for user_id, total in points.items()}
