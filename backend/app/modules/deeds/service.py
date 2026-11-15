"""Public interface of the deeds module for other modules (docs/M2-DESIGN.md §4).

Event payloads carry IDs only; consumers (Feed, Scoring, Fraud, ... from M3) fetch details here.
The views never contain coordinates (only ``has_location``) and never reveal the doer of a
pending deed. Self-logs are not exposed: they are private and never read by other modules.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds.models import Deed, DeedStatus, SameActGroup, VerificationMethod

__all__ = [
    "DeedStatus",
    "DeedView",
    "SameActGroupView",
    "VerificationMethod",
    "deeds_for_doer",
    "get_deed",
    "same_act_group_view",
]


@dataclass(frozen=True)
class DeedView:
    id: uuid.UUID
    status: DeedStatus
    verification_method: VerificationMethod
    reporter_id: uuid.UUID
    # Always None while pending (PRD §6.5).
    doer_id: uuid.UUID | None
    category: str
    description: str
    beneficiaries_count: int | None
    occurred_at: datetime
    reported_at: datetime
    verified_at: datetime | None
    withdrawn_at: datetime | None
    last_edited_at: datetime | None
    same_act_group_id: uuid.UUID | None
    counts_for_points: bool
    prize_rank_eligible: bool
    has_location: bool
    limits_version: int


@dataclass(frozen=True)
class SameActGroupView:
    """Counters and parameters of one act. The centre is never exposed, only whether it has
    one (a group anchored by an invite deed without a location has none)."""

    id: uuid.UUID
    doer_id: uuid.UUID
    anchor_at: datetime
    radius_m: int
    window_minutes: int
    cap: int
    verified_count: int
    counted_count: int
    distinct_reporters: int
    has_centre: bool
    limits_version: int
    # Verified member deeds, oldest verification first.
    verified_deed_ids: tuple[uuid.UUID, ...]


def _view(deed: Deed) -> DeedView:
    return DeedView(
        id=deed.id,
        status=deed.status,
        verification_method=deed.verification_method,
        reporter_id=deed.reporter_user_id,
        doer_id=None if deed.status == DeedStatus.PENDING else deed.doer_user_id,
        category=deed.category,
        description=deed.description,
        beneficiaries_count=deed.beneficiaries_count,
        occurred_at=deed.occurred_at,
        reported_at=deed.reported_at,
        verified_at=deed.verified_at,
        withdrawn_at=deed.withdrawn_at,
        last_edited_at=deed.last_edited_at,
        same_act_group_id=deed.same_act_group_id,
        counts_for_points=deed.counts_for_points,
        prize_rank_eligible=deed.prize_rank_eligible,
        has_location=deed.location is not None,
        limits_version=deed.limits_version,
    )


async def get_deed(session: AsyncSession, deed_id: uuid.UUID) -> DeedView | None:
    deed = await session.get(Deed, deed_id)
    return None if deed is None else _view(deed)


async def deeds_for_doer(
    session: AsyncSession,
    doer_id: uuid.UUID,
    *,
    statuses: tuple[DeedStatus, ...] = (DeedStatus.VERIFIED,),
    limit: int = 50,
    occurred_before: datetime | None = None,
) -> list[DeedView]:
    """Deeds about a doer, newest ``occurred_at`` first (verified only by default). Pending
    deeds have no doer yet, so they never appear here."""
    stmt = select(Deed).where(Deed.doer_user_id == doer_id, Deed.status.in_(statuses))
    if occurred_before is not None:
        stmt = stmt.where(Deed.occurred_at < occurred_before)
    rows = await session.scalars(stmt.order_by(Deed.occurred_at.desc(), Deed.id).limit(limit))
    return [_view(d) for d in rows]


async def same_act_group_view(
    session: AsyncSession, group_id: uuid.UUID
) -> SameActGroupView | None:
    group = await session.get(SameActGroup, group_id)
    if group is None:
        return None
    deed_ids = await session.scalars(
        select(Deed.id)
        .where(Deed.same_act_group_id == group_id, Deed.status == DeedStatus.VERIFIED)
        .order_by(Deed.verified_at, Deed.id)
    )
    return SameActGroupView(
        id=group.id,
        doer_id=group.doer_user_id,
        anchor_at=group.anchor_at,
        radius_m=group.radius_m,
        window_minutes=group.window_minutes,
        cap=group.cap,
        verified_count=group.verified_count,
        counted_count=group.counted_count,
        distinct_reporters=group.distinct_reporters,
        has_centre=group.centre is not None,
        limits_version=group.limits_version,
        verified_deed_ids=tuple(deed_ids),
    )
