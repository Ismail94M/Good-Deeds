"""Helpers for leaderboard, finalization and badge tests: ledger rows written directly (with a
deed row each, for the foreign key), so a test controls month, day, region and reporter
exactly. ``reconcile_deed`` itself is covered by ``test_ledger.py``."""

import itertools
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import publish
from app.modules.deeds import service as deeds
from app.modules.deeds.models import Deed, DeedStatus, VerificationMethod
from app.modules.scoring._limits import LeaderboardConfig, load_leaderboard
from app.modules.scoring.models import (
    LeaderboardEntry,
    LedgerKind,
    LedgerReason,
    LedgerRole,
    PointsLedger,
)
from tests.deeds.helpers import Member, add_ward_regions

IST = ZoneInfo("Asia/Kolkata")
CITY = "IN-KA-BLR"
# Distinct report days, so the pair-day unique index never collides.
_days = itertools.count()


def ist(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=IST).astimezone(UTC)


async def ward(session: AsyncSession, code: str) -> int:
    """A test ward region under Bangalore; returns its id."""
    await add_ward_regions(session, code)
    region = await deeds.region_by_code(session, f"{CITY}-{code}")
    assert region is not None
    return region.id


async def city(session: AsyncSession) -> int:
    region = await deeds.region_by_code(session, CITY)
    assert region is not None
    return region.id


async def counted(
    session: AsyncSession,
    doer: Member,
    reporter: Member,
    *,
    region_id: int,
    verified_at: datetime,
    points: int = 10,
    prize: bool = True,
    reporter_row: bool = False,
) -> uuid.UUID:
    """A deed with a doer award (one counted deed) and optionally a reporter award."""
    deed = Deed(
        reporter_user_id=reporter.user_id,
        doer_user_id=doer.user_id,
        category="animals",
        description="Helped a stray dog across the road",
        occurred_at=verified_at - timedelta(minutes=5),
        reported_at=verified_at,
        report_day=date(2000, 1, 1) + timedelta(days=next(_days)),
        status=DeedStatus.REJECTED,
        verification_method=VerificationMethod.INVITE_ACCEPT,
        limits_version=1,
    )
    session.add(deed)
    await session.flush()
    day = verified_at.astimezone(IST).date()
    rows = [(LedgerRole.DOER, doer, reporter, points, 1, prize)]
    if reporter_row:
        rows.append((LedgerRole.REPORTER, reporter, doer, 2, 0, False))
    for role, user, other, pts, units, flag in rows:
        session.add(
            PointsLedger(
                id=uuid.uuid4(),
                user_id=user.user_id,
                deed_id=deed.id,
                role=role,
                kind=LedgerKind.AWARD,
                points=pts,
                deed_units=units,
                counts_for_prize_rank=flag,
                region_id=region_id,
                reason=LedgerReason.VERIFIED,
                breakdown={},
                counterpart_user_id=other.user_id,
                verified_at=verified_at,
                earned_on=day,
                period_month=day.replace(day=1),
                scoring_config_version=1,
            )
        )
        await session.flush()
    return deed.id


async def reverse(session: AsyncSession, deed_id: uuid.UUID) -> None:
    """Reverse the deed's active doer award (a withdrawal or an M5-style rejection)."""
    award = await session.scalar(
        select(PointsLedger).where(
            PointsLedger.deed_id == deed_id,
            PointsLedger.role == LedgerRole.DOER,
            PointsLedger.kind == LedgerKind.AWARD,
        )
    )
    assert award is not None
    session.add(
        PointsLedger(
            id=uuid.uuid4(),
            user_id=award.user_id,
            deed_id=deed_id,
            role=award.role,
            kind=LedgerKind.REVERSAL,
            points=-award.points,
            deed_units=-award.deed_units,
            counts_for_prize_rank=award.counts_for_prize_rank,
            region_id=award.region_id,
            reason=LedgerReason.WITHDRAWN,
            breakdown={},
            counterpart_user_id=award.counterpart_user_id,
            reverses_entry_id=award.id,
            verified_at=award.verified_at,
            earned_on=award.earned_on,
            period_month=award.period_month,
            scoring_config_version=1,
        )
    )
    await session.flush()


async def publish_board(session: AsyncSession, **changes: Any) -> LeaderboardConfig:
    """Publish a new ``leaderboard`` config version (rolled back with the test)."""
    _, current = await load_leaderboard(session)
    new = current.model_copy(update=changes)
    await publish(session, new, actor_id=None, reason="test")
    await session.flush()
    return new


async def entries(
    session: AsyncSession, snapshot_id: uuid.UUID
) -> dict[uuid.UUID, LeaderboardEntry]:
    rows = await session.scalars(
        select(LeaderboardEntry).where(LeaderboardEntry.snapshot_id == snapshot_id)
    )
    return {e.user_id: e for e in rows}


def month(year: int, m: int) -> date:
    return date(year, m, 1)
