"""Same-act grouping (docs/M2-DESIGN.md §3.4; PRD §6.4, §9, §10.1).

Reports of one doer within ``radius_m`` and ``window_minutes`` of a group's **anchor** (the
first report) form one act. Time is always a server-recorded time, never the reporter's "When?"
(``occurred_at``, which can be backdated): a QR deed's scan ``scanned_at``, an invite deed's
``reported_at`` (security L7, PO decision L7-A). ``occurred_at`` is for display only.
The first ``cap`` verified reports, one per reporter, get ``counts_for_points``; later ones
are saved and linked but don't count.

Runs when a deed becomes verified, inside the caller's transaction, under the per-doer
``same_act`` advisory lock so two verifications can never both take the last counted slot.

Step 7 (``upgrade_prize_rank``): once a group has two distinct reporters, its verified invite
deeds become prize-rank eligible (a second independent report). ``withdraw_from_group`` is the
reverse path (§3.4b): it releases a withdrawn deed's slot, promotes the next eligible report
(D13) and revokes the invite deeds' eligibility when fewer than two reporters remain. Every flip
is audited and emitted.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import DateTime, exists, func, literal, select, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.audit import record_audit
from app.core.db import advisory_xact_lock
from app.core.events import emit
from app.modules.deeds._limits import DeedLimits
from app.modules.deeds.events import DeedCountsForPointsChanged, DeedPrizeRankChanged
from app.modules.deeds.models import Deed, DeedStatus, SameActGroup, VerificationMethod

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
    session: AsyncSession, doer_id: uuid.UUID, location: str, grouped_at: datetime
) -> SameActGroup | None:
    window = func.make_interval(0, 0, 0, 0, 0, SameActGroup.window_minutes)
    at = literal(grouped_at, DateTime(timezone=True))
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
    grouped_at: datetime,
    limits: DeedLimits,
    limits_version: int,
) -> SameActGroup:
    """The parameters are copied from config so a later change never reshuffles the group."""
    return SameActGroup(
        doer_user_id=doer_id,
        centre=location,
        anchor_at=grouped_at,
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
    grouped_at: datetime,
    limits: DeedLimits,
    limits_version: int,
    exclude_deed_id: uuid.UUID | None = None,
) -> GroupAssignment:
    """Find or create the deed's group and take a counted slot if one is free.

    ``location`` is the deed's location as EWKT, or None for an invite deed without one, which
    gets a null-centre group of its own that nothing else can join. ``grouped_at`` is the deed's
    server-recorded grouping time (scan time or invite ``reported_at``), never ``occurred_at``
    (L7-A); a new group's ``anchor_at`` is set from it. The caller sets
    ``same_act_group_id`` and ``counts_for_points`` on the deed (which must not be in the group
    yet; pass ``exclude_deed_id`` if it is already flushed).
    """
    await lock_doer(session, doer_id)
    group = (
        await _matching_group(session, doer_id, location, grouped_at)
        if location is not None
        else None
    )
    if group is None:
        group = _new_group(doer_id, location, grouped_at, limits, limits_version)
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


# --------------------------------------------------- prize rank (§3.4 step 7, §3.4b step 4)

PRIZE_GRANTED_REASON = "second_independent_report"
PRIZE_REVOKED_REASON = "second_report_withdrawn"
PROMOTED_REASON = "promoted_after_withdrawal"
PRIZE_REVOKED_REJECTED_REASON = "second_report_rejected"


async def _verified_invite_deeds(
    session: AsyncSession, group_id: uuid.UUID, *, eligible: bool
) -> list[Deed]:
    stmt = (
        select(Deed)
        .where(
            Deed.same_act_group_id == group_id,
            Deed.status == DeedStatus.VERIFIED,
            Deed.verification_method == VerificationMethod.INVITE_ACCEPT,
            Deed.prize_rank_eligible.is_(eligible),
        )
        .order_by(Deed.verified_at, Deed.id)
        .execution_options(populate_existing=True)
    )
    return list(await session.scalars(stmt))


def _flip_prize_rank(session: AsyncSession, deed: Deed, eligible: bool, reason: str) -> None:
    assert deed.doer_user_id is not None
    deed.prize_rank_eligible = eligible
    record_audit(
        session,
        entity_type="deed",
        entity_id=str(deed.id),
        action="prize_rank_eligible.granted" if eligible else "prize_rank_eligible.revoked",
        actor_id=None,
        old_value={"prize_rank_eligible": not eligible},
        new_value={"prize_rank_eligible": eligible},
        reason=reason,
    )
    emit(
        session,
        DeedPrizeRankChanged(
            deed_id=deed.id,
            doer_id=deed.doer_user_id,
            prize_rank_eligible=eligible,
            reason=reason,
        ),
    )


async def upgrade_prize_rank(session: AsyncSession, group: SameActGroup) -> list[uuid.UUID]:
    """Step 7: with two or more distinct reporters, every verified invite deed in the group that
    isn't prize-rank eligible yet becomes eligible. Call under the ``same_act`` lock, after the
    newly verified deed is flushed. Returns the IDs of the deeds that flipped."""
    if group.distinct_reporters < 2:
        return []
    deeds = await _verified_invite_deeds(session, group.id, eligible=False)
    for deed in deeds:
        _flip_prize_rank(session, deed, True, PRIZE_GRANTED_REASON)
    await session.flush()
    return [d.id for d in deeds]


# --------------------------------------------------- withdrawal (§3.4b)


async def _promotion_candidate(session: AsyncSession, group_id: uuid.UUID) -> Deed | None:
    """The oldest still-verified uncounted report whose reporter has no counted verified deed
    in the group (D13)."""
    other = aliased(Deed)
    reporter_counted = exists().where(
        other.same_act_group_id == group_id,
        other.reporter_user_id == Deed.reporter_user_id,
        other.status == DeedStatus.VERIFIED,
        other.counts_for_points.is_(True),
    )
    stmt = (
        select(Deed)
        .where(
            Deed.same_act_group_id == group_id,
            Deed.status == DeedStatus.VERIFIED,
            Deed.counts_for_points.is_(False),
            ~reporter_counted,
        )
        .order_by(Deed.verified_at, Deed.id)
        .limit(1)
        .execution_options(populate_existing=True)
    )
    return (await session.scalars(stmt)).first()


async def withdraw_from_group(session: AsyncSession, deed: Deed) -> SameActGroup:
    """Release a withdrawn deed's place in its group (§3.4b). The caller holds the per-doer
    ``same_act`` lock (taken before the deed's row lock, see ``_edits``) and has already set the
    deed's status to ``withdrawn``; its ``counts_for_points`` is kept for history."""
    return await leave_group(session, deed, promote=True, revoked_reason=PRIZE_REVOKED_REASON)


async def leave_group(
    session: AsyncSession, deed: Deed, *, promote: bool, revoked_reason: str
) -> SameActGroup:
    """Take a deed that is no longer verified out of its group's counters (§3.4b): a withdrawal
    (``promote=True``, D13) or a fraud rejection (``promote=False``, M5 D89). The caller holds
    the per-doer ``same_act`` lock and has already changed the deed's status; its
    ``counts_for_points`` is kept for history. When fewer than two distinct reporters remain,
    the group's verified invite deeds lose prize-rank eligibility, audited with
    ``revoked_reason``."""
    assert deed.doer_user_id is not None and deed.same_act_group_id is not None
    await session.flush()
    group = (
        await session.scalars(
            select(SameActGroup)
            .where(SameActGroup.id == deed.same_act_group_id)
            .execution_options(populate_existing=True)
        )
    ).one()
    # Recount first (the deed is already flushed as withdrawn), then change all counters in
    # one UPDATE so the counter CHECK constraints hold at every step.
    distinct = int(
        await session.scalar(
            select(func.count(func.distinct(Deed.reporter_user_id))).where(
                Deed.same_act_group_id == group.id, Deed.status == DeedStatus.VERIFIED
            )
        )
        or 0
    )
    group.verified_count -= 1
    if deed.counts_for_points:
        group.counted_count -= 1
    group.distinct_reporters = distinct
    await session.flush()

    # D13: at most one promotion, and only into the slot this deed gave up.
    if promote and deed.counts_for_points and group.counted_count < group.cap:
        promoted = await _promotion_candidate(session, group.id)
        if promoted is not None:
            assert promoted.doer_user_id is not None
            promoted.counts_for_points = True
            group.counted_count += 1
            record_audit(
                session,
                entity_type="deed",
                entity_id=str(promoted.id),
                action="counts_for_points.granted",
                actor_id=None,
                old_value={"counts_for_points": False},
                new_value={"counts_for_points": True},
                reason=PROMOTED_REASON,
            )
            emit(
                session,
                DeedCountsForPointsChanged(
                    deed_id=promoted.id,
                    doer_id=promoted.doer_user_id,
                    reporter_id=promoted.reporter_user_id,
                    same_act_group_id=group.id,
                    counts_for_points=True,
                    reason=PROMOTED_REASON,
                ),
            )

    # In M2 an invite deed's eligibility only ever comes from the second-report rule.
    if group.distinct_reporters < 2:
        for revoked in await _verified_invite_deeds(session, group.id, eligible=True):
            _flip_prize_rank(session, revoked, False, revoked_reason)
    await session.flush()
    return group
