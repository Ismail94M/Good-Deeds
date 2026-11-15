"""Same-act grouping (docs/M2-DESIGN.md §3.4; PRD §6.4, §9, §10.1).

Reports of one doer within ``radius_m`` and ``window_minutes`` of a group's **anchor** (the
first report) form one act. The first ``cap`` verified reports, one per reporter, get
``counts_for_points``; later ones are saved and linked but don't count.

Runs when a deed becomes verified, inside the caller's transaction, under the per-doer
``same_act`` advisory lock so two verifications can never both take the last counted slot.

Not here yet (B6): step 7, the prize-rank upgrade of invite deeds when a group reaches two
distinct reporters. Only QR deeds exist before B6, and they are prize-rank eligible already.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import DateTime, exists, func, literal, select, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import advisory_xact_lock
from app.modules.deeds._limits import DeedLimits
from app.modules.deeds.models import Deed, DeedStatus, SameActGroup

_POINT = Geography(geometry_type="POINT", srid=4326, spatial_index=False)


@dataclass(frozen=True)
class GroupAssignment:
    group: SameActGroup
    counts_for_points: bool
    reporter_already_in_group: bool


async def lock_doer(session: AsyncSession, doer_id: uuid.UUID) -> None:
    """Serialise every change to this doer's groups until the transaction ends."""
    await advisory_xact_lock(session, "same_act", str(doer_id))


async def _matching_group(
    session: AsyncSession, doer_id: uuid.UUID, location: str, occurred_at: datetime
) -> SameActGroup | None:
    window = func.make_interval(0, 0, 0, 0, 0, SameActGroup.window_minutes)
    at = literal(occurred_at, DateTime(timezone=True))
    stmt = (
        select(SameActGroup)
        .where(
            SameActGroup.doer_user_id == doer_id,
            SameActGroup.centre.is_not(None),
            func.ST_DWithin(
                SameActGroup.centre, type_coerce(location, _POINT), SameActGroup.radius_m
            ),
            at.between(SameActGroup.anchor_at - window, SameActGroup.anchor_at + window),
        )
        # Nearest anchor in time wins; matching is against the anchor, never a member, so a
        # chain of reports can't make a group creep along.
        .order_by(
            func.abs(func.extract("epoch", at - SameActGroup.anchor_at)),
            SameActGroup.created_at,
            SameActGroup.id,
        )
        .limit(1)
        # The counters must be read fresh under the lock, not from the identity map.
        .execution_options(populate_existing=True)
    )
    return (await session.scalars(stmt)).first()


def _new_group(
    doer_id: uuid.UUID,
    location: str | None,
    occurred_at: datetime,
    limits: DeedLimits,
    limits_version: int,
) -> SameActGroup:
    """The parameters are copied from config so a later change never reshuffles the group."""
    return SameActGroup(
        doer_user_id=doer_id,
        centre=location,
        anchor_at=occurred_at,
        radius_m=limits.same_act_radius_m,
        window_minutes=limits.same_act_window_minutes,
        cap=limits.same_act_cap,
        verified_count=0,
        counted_count=0,
        distinct_reporters=0,
        limits_version=limits_version,
    )


async def assign_group(
    session: AsyncSession,
    *,
    doer_id: uuid.UUID,
    reporter_id: uuid.UUID,
    location: str | None,
    occurred_at: datetime,
    limits: DeedLimits,
    limits_version: int,
    exclude_deed_id: uuid.UUID | None = None,
) -> GroupAssignment:
    """Find or create the deed's group and take a counted slot if one is free.

    ``location`` is the deed's location as EWKT, or None for an invite deed without one, which
    gets a null-centre group of its own that nothing else can join. The caller sets
    ``same_act_group_id`` and ``counts_for_points`` on the deed (which must not be in the group
    yet; pass ``exclude_deed_id`` if it is already flushed).
    """
    await lock_doer(session, doer_id)
    group = (
        await _matching_group(session, doer_id, location, occurred_at)
        if location is not None
        else None
    )
    if group is None:
        group = _new_group(doer_id, location, occurred_at, limits, limits_version)
        session.add(group)
        await session.flush()
        already = False
    else:
        conditions = [
            Deed.same_act_group_id == group.id,
            Deed.reporter_user_id == reporter_id,
            Deed.status == DeedStatus.VERIFIED,
        ]
        if exclude_deed_id is not None:
            conditions.append(Deed.id != exclude_deed_id)
        already = bool(await session.scalar(select(exists().where(*conditions))))

    # PRD §4: each reporter counts at most once per act.
    counts = group.counted_count < group.cap and not already
    group.verified_count += 1
    group.counted_count += int(counts)
    group.distinct_reporters += int(not already)
    await session.flush()
    return GroupAssignment(group=group, counts_for_points=counts, reporter_already_in_group=already)
