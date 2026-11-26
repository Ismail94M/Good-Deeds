"""Public interface of the deeds module for other modules (docs/M2-DESIGN.md §4).

Event payloads carry IDs only; consumers (Feed, Scoring, Fraud, ... from M3) fetch details here.
The views never contain coordinates (only ``has_location``) and never reveal the doer of a
pending deed. Self-logs are not exposed: they are private and never read by other modules.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds._areas import DEFAULT_AREA_NAME
from app.modules.deeds._common import ist_month
from app.modules.deeds._fraud_reads import (
    Edge,
    EdgeMatch,
    PairSignals,
    pair_signals,
    related_users,
    verified_edges,
)
from app.modules.deeds._limits import load_limits
from app.modules.deeds._reject import FRAUD_CONFIRMED_REASON, reject_for_fraud
from app.modules.deeds.events import (
    DeedCountsForPointsChanged,
    DeedEdited,
    DeedPrizeRankChanged,
    DeedRejected,
    DeedVerified,
    DeedWithdrawn,
)
from app.modules.deeds.models import (
    Area,
    Deed,
    DeedRevision,
    DeedStatus,
    Region,
    RegionClosure,
    RegionKind,
    SameActGroup,
    SelfLog,
    VerificationMethod,
)

__all__ = [
    "DEFAULT_AREA_NAME",
    "FRAUD_CONFIRMED_REASON",
    # Events other modules subscribe to (M3 feed projection §5; M4 scoring §5)
    "DeedContent",
    "DeedCountsForPointsChanged",
    "DeedEdited",
    "DeedPrizeRankChanged",
    "DeedRejected",
    "DeedStatus",
    "DeedVerified",
    "DeedView",
    "DeedWithdrawn",
    # Fraud reads and the fraud rejection (docs/M5-DESIGN.md §3.1, D89)
    "Edge",
    "EdgeMatch",
    "PairSignals",
    "RegionKind",
    "RegionView",
    "SameActGroupView",
    "ScoringInputs",
    "VerificationMethod",
    "area_name",
    "category_labels",
    "content_as_of",
    "deed_region",
    "deeds_for_doer",
    "default_region",
    "get_deed",
    "get_deeds",
    "pair_ordinal",
    "pair_signals",
    "region_ancestor_map",
    "region_ancestors",
    "region_by_code",
    "regions",
    "regions_by_id",
    "reject_for_fraud",
    "related_users",
    "revision_time",
    "same_act_group_view",
    "scoring_inputs",
    "self_log_owner",
    "verified_edges",
    "withdraw_window_hours",
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
    # Coarse public area (M3 D39); None shows as "Bangalore" (use ``area_name``). Never
    # coordinates and never the dataset's ward code.
    area_name: str | None = None


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


def area_name(view: DeedView) -> str:
    """The area to show for a deed: its area's display name, else "Bangalore"."""
    return view.area_name or DEFAULT_AREA_NAME


def _with_area() -> Select[Deed, str]:
    return select(Deed, Area.name).outerjoin(Area, Area.id == Deed.area_id)


def _view(deed: Deed, area: str | None = None) -> DeedView:
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
        area_name=area,
    )


async def category_labels(session: AsyncSession) -> dict[str, str]:
    """Display label of each category slug in the active ``deeds`` config (M3 §3.4b #11)."""
    _, limits = await load_limits(session)
    return {c.slug: c.label for c in limits.categories}


async def get_deed(session: AsyncSession, deed_id: uuid.UUID) -> DeedView | None:
    row = (await session.execute(_with_area().where(Deed.id == deed_id))).first()
    return None if row is None else _view(row[0], row[1])


async def get_deeds(
    session: AsyncSession, deed_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, DeedView]:
    """Views of the deeds found, by id, in one query (joined with their area). Unknown ids are
    left out; callers filter by status themselves."""
    ids = set(deed_ids)
    if not ids:
        return {}
    rows = await session.execute(_with_area().where(Deed.id.in_(ids)))
    return {deed.id: _view(deed, area) for deed, area in rows}


async def self_log_owner(session: AsyncSession, self_log_id: uuid.UUID) -> uuid.UUID | None:
    """The owner of a self-log, or None if it doesn't exist. No content: self-logs are private."""
    return await session.scalar(select(SelfLog.user_id).where(SelfLog.id == self_log_id))


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
    stmt = _with_area().where(Deed.doer_user_id == doer_id, Deed.status.in_(statuses))
    if occurred_before is not None:
        stmt = stmt.where(Deed.occurred_at < occurred_before)
    rows = await session.execute(stmt.order_by(Deed.occurred_at.desc(), Deed.id).limit(limit))
    return [_view(deed, area) for deed, area in rows]


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


# --------------------------------------------------- scoring reads (docs/M4-DESIGN.md §3.1)


@dataclass(frozen=True)
class ScoringInputs:
    """What scoring needs about one deed, read live. ``description`` is for in-process text
    rules only: scoring never stores or returns it. ``doer_id`` is None while pending."""

    id: uuid.UUID
    status: DeedStatus
    verification_method: VerificationMethod
    reporter_id: uuid.UUID
    doer_id: uuid.UUID | None
    category: str
    description: str
    beneficiaries_count: int | None
    reported_at: datetime
    verified_at: datetime | None
    same_act_group_id: uuid.UUID | None
    counts_for_points: bool
    prize_rank_eligible: bool


async def scoring_inputs(session: AsyncSession, deed_id: uuid.UUID) -> ScoringInputs | None:
    deed = await session.get(Deed, deed_id, populate_existing=True)
    if deed is None:
        return None
    return ScoringInputs(
        id=deed.id,
        status=deed.status,
        verification_method=deed.verification_method,
        reporter_id=deed.reporter_user_id,
        doer_id=None if deed.status == DeedStatus.PENDING else deed.doer_user_id,
        category=deed.category,
        description=deed.description,
        beneficiaries_count=deed.beneficiaries_count,
        reported_at=deed.reported_at,
        verified_at=deed.verified_at,
        same_act_group_id=deed.same_act_group_id,
        counts_for_points=deed.counts_for_points,
        prize_rank_eligible=deed.prize_rank_eligible,
    )


async def pair_ordinal(
    session: AsyncSession, doer_id: uuid.UUID, reporter_id: uuid.UUID, verified_at: datetime
) -> int:
    """The place of a report verified at ``verified_at`` among the pair's reports verified in
    that IST month (1 = first), for the pair multiplier (PO Q47). Every deed of the pair that was
    ever verified counts, whatever its status now, so a withdrawal never changes a later
    report's ordinal."""
    start, _ = ist_month(verified_at)
    count = await session.scalar(
        select(func.count())
        .select_from(Deed)
        .where(
            Deed.doer_user_id == doer_id,
            Deed.reporter_user_id == reporter_id,
            Deed.verified_at.is_not(None),
            Deed.verified_at >= start,
            Deed.verified_at <= verified_at,
        )
    )
    return int(count or 0)


async def day_order(
    session: AsyncSession,
    *,
    role: Literal["doer", "reporter"],
    user_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[uuid.UUID]:
    """The user's deeds that take a daily-cap place in ``[start, end)``, in cap order: by
    ``verified_at``, ties by deed id (docs/M4-DESIGN.md D52, PO QA3). As doer, every report that
    was ever verified and counted for points; as reporter, every report that was ever verified.
    Status is not filtered, so a withdrawal keeps its place and never moves a later report up."""
    owner = Deed.doer_user_id if role == "doer" else Deed.reporter_user_id
    query = select(Deed.id).where(
        owner == user_id,
        Deed.verified_at.is_not(None),
        Deed.verified_at >= start,
        Deed.verified_at < end,
    )
    if role == "doer":
        query = query.where(Deed.counts_for_points.is_(True))
    return list(await session.scalars(query.order_by(Deed.verified_at, Deed.id)))


async def revision_time(session: AsyncSession, revision_id: uuid.UUID) -> datetime | None:
    """When an edit was saved (its ``deed_revisions`` row), or None if unknown (D56)."""
    return await session.scalar(
        select(DeedRevision.created_at).where(DeedRevision.id == revision_id)
    )


@dataclass(frozen=True)
class DeedContent:
    """The scored content of a deed at some moment (in-process only, never stored by scoring)."""

    category: str
    description: str


async def content_as_of(
    session: AsyncSession, deed_id: uuid.UUID, at: datetime
) -> DeedContent | None:
    """The deed's category and description as they were at ``at``: the live values with every
    edit saved after ``at`` undone (from ``deed_revisions``). Scoring reads the content as of the
    end of the withdrawal window, so an edit after the window never changes points, whenever the
    re-score runs (D56). None if the deed doesn't exist."""
    deed = await session.get(Deed, deed_id)
    if deed is None:
        return None
    values: dict[str, str] = {"category": deed.category, "description": deed.description}
    later = await session.scalars(
        select(DeedRevision)
        .where(DeedRevision.deed_id == deed_id, DeedRevision.created_at > at)
        .order_by(DeedRevision.created_at.desc(), DeedRevision.id.desc())
    )
    for revision in later:
        for field in values:
            if field in revision.old_values:
                values[field] = revision.old_values[field]
    return DeedContent(category=values["category"], description=values["description"])


async def withdraw_window_hours(session: AsyncSession) -> int:
    """Hours after verification in which a reporter may withdraw a verified deed (D64), from the
    active ``deeds`` config, so the window is defined in one place."""
    _, limits = await load_limits(session)
    return limits.withdraw_window_hours


# --------------------------------------------------- regions (docs/M4-DESIGN.md §2.2, D65, D66)


@dataclass(frozen=True)
class RegionView:
    """A place in the location hierarchy. Never coordinates or a boundary."""

    id: int
    kind: RegionKind
    name: str
    code: str
    parent_id: int | None


def _region_view(region: Region) -> RegionView:
    return RegionView(
        id=region.id,
        kind=region.kind,
        name=region.name,
        code=region.code,
        parent_id=region.parent_id,
    )


async def region_by_code(session: AsyncSession, code: str) -> RegionView | None:
    region = await session.scalar(select(Region).where(Region.code == code))
    return None if region is None else _region_view(region)


async def deed_region(session: AsyncSession, deed_id: uuid.UUID) -> RegionView | None:
    """Where a deed counts (D66): the ward region of its area, or the ``fallback_region_code``
    region (the city) when it has no area. None if the deed doesn't exist."""
    row = (
        await session.execute(
            select(Deed.id, Region)
            .outerjoin(Area, Area.id == Deed.area_id)
            .outerjoin(Region, Region.id == Area.region_id)
            .where(Deed.id == deed_id)
        )
    ).first()
    if row is None:
        return None
    if row[1] is not None:
        return _region_view(row[1])
    _, limits = await load_limits(session)
    fallback = await region_by_code(session, limits.fallback_region_code)
    if fallback is None:
        raise RuntimeError(f"No region with fallback_region_code {limits.fallback_region_code!r}")
    return fallback


async def region_ancestors(session: AsyncSession, region_id: int) -> list[RegionView]:
    """The region itself and every ancestor, nearest first (ward, city, state, country).
    Empty if the region doesn't exist."""
    rows = await session.scalars(
        select(Region)
        .join(RegionClosure, RegionClosure.ancestor_id == Region.id)
        .where(RegionClosure.descendant_id == region_id)
        .order_by(RegionClosure.depth)
    )
    return [_region_view(r) for r in rows]


async def regions(
    session: AsyncSession, kind: RegionKind, q: str | None = None, *, limit: int = 50
) -> list[RegionView]:
    """Regions of one kind by name (the ward picker), optionally filtered by a case-insensitive
    substring of the name. ``q`` is matched literally (``%`` and ``_`` are not wildcards)."""
    stmt = select(Region).where(Region.kind == kind)
    if q is not None and q.strip():
        stmt = stmt.where(Region.name.icontains(q.strip(), autoescape=True))
    rows = await session.scalars(stmt.order_by(Region.name, Region.id).limit(limit))
    return [_region_view(r) for r in rows]


async def regions_by_id(session: AsyncSession, region_ids: Iterable[int]) -> dict[int, RegionView]:
    """Views of the regions found, by id, in one query. Unknown ids are left out."""
    ids = set(region_ids)
    if not ids:
        return {}
    rows = await session.scalars(select(Region).where(Region.id.in_(ids)))
    return {r.id: _region_view(r) for r in rows}


async def region_ancestor_map(session: AsyncSession, kind: RegionKind) -> dict[int, int]:
    """For every region at or below ``kind``, the id of its ancestor of that kind (a region of
    that kind maps to itself). Leaderboards use it so each deed counts once per level (D66):
    a region has exactly one ancestor of each kind."""
    rows = await session.execute(
        select(RegionClosure.descendant_id, RegionClosure.ancestor_id)
        .join(Region, Region.id == RegionClosure.ancestor_id)
        .where(Region.kind == kind)
    )
    return {descendant: ancestor for descendant, ancestor in rows}


async def default_region(session: AsyncSession) -> RegionView:
    """The region a deed without a ward counts in (``fallback_region_code``, the city): also the
    default leaderboard (docs/M4-DESIGN.md §3.3 #6)."""
    _, limits = await load_limits(session)
    region = await region_by_code(session, limits.fallback_region_code)
    if region is None:
        raise RuntimeError(f"No region with fallback_region_code {limits.fallback_region_code!r}")
    return region
