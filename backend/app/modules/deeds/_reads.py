"""Read endpoints (§3.2 #4, #5, #6, #10): one deed, my deeds, my invites, form options.

Privacy: only the reporter or the doer may read a deed; anyone else gets 404 (not 403), so the
response never confirms that a deed exists. The doer sees only ``verified`` deeds about them (D24,
Q18): a withdrawn, removed or unverified report disappears from their list and is 404 by ID, while
the reporter still sees every status. The one exception is ``rejected`` (M5 D101): a deed rejected
after a confirmed review stays in the doer's own list and is readable by ID, so My deeds can show
"Removed after review" linked to its decision notice. Q18 hid reports the *reporter* took back;
a rejection is a decision about the doer, who must be able to see and appeal it. This is
owner-only: public reads (feed, profiles, boards) never show a rejected deed. ``deed_out``
never includes coordinates and never reveals the doer of a pending deed. The invite (without its
raw link) is shown to the reporter only.
"""

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.modules.deeds import _cursor
from app.modules.deeds._areas import area_names
from app.modules.deeds._common import ist_day, ist_month, utcnow
from app.modules.deeds._invites import invite_out
from app.modules.deeds._limits import DeedLimits, load_limits
from app.modules.deeds._reporter_limit import reported_this_month
from app.modules.deeds._views import deed_out
from app.modules.deeds.models import Deed, DeedStatus, Invite, InviteStatus, SameActGroup
from app.modules.deeds.schemas import (
    CategoryOut,
    DeedOptions,
    DeedOut,
    DeedPage,
    InvitePage,
)
from app.modules.identity import service as identity

Role = Literal["doer", "reporter"]
InviteFilter = Literal["pending", "accepted", "expired"]


# Statuses the doer sees about themselves (D24 + M5 D101).
_DOER_VISIBLE = (DeedStatus.VERIFIED, DeedStatus.REJECTED)


def _deed_not_found() -> NotFound:
    return NotFound("We couldn't find that deed.", code="deed_not_found")


async def _deed_outs(
    session: AsyncSession, deeds: list[Deed], viewer_id: uuid.UUID
) -> list[DeedOut]:
    """Build responses for a page of deeds with one query each for names, groups and invites."""
    if not deeds:
        return []
    user_ids = {d.reporter_user_id for d in deeds} | {
        d.doer_user_id for d in deeds if d.doer_user_id is not None
    }
    summaries = await identity.public_summaries(session, user_ids)
    areas = await area_names(session, (d.area_id for d in deeds))
    group_ids = {d.same_act_group_id for d in deeds if d.same_act_group_id is not None}
    groups: dict[uuid.UUID, SameActGroup] = {}
    if group_ids:
        rows = await session.scalars(select(SameActGroup).where(SameActGroup.id.in_(group_ids)))
        groups = {g.id: g for g in rows}
    # Invites are shown to the reporter only.
    own_ids = [d.id for d in deeds if d.reporter_user_id == viewer_id]
    invites: dict[uuid.UUID, Invite] = {}
    if own_ids:
        found = await session.scalars(select(Invite).where(Invite.deed_id.in_(own_ids)))
        invites = {i.deed_id: i for i in found}
    _, limits = await load_limits(session)
    now = utcnow()
    out: list[DeedOut] = []
    for deed in deeds:
        invite = invites.get(deed.id)
        out.append(
            await deed_out(
                session,
                deed,
                viewer_id,
                group=groups.get(deed.same_act_group_id) if deed.same_act_group_id else None,
                invite=invite_out(invite) if invite is not None else None,
                summaries=summaries,
                areas=areas,
                window_hours=limits.withdraw_window_hours,
                now=now,
            )
        )
    return out


async def get_deed(session: AsyncSession, viewer_id: uuid.UUID, deed_id: uuid.UUID) -> DeedOut:
    deed = await session.get(Deed, deed_id)
    if deed is None:
        raise _deed_not_found()
    is_reporter = viewer_id == deed.reporter_user_id
    is_doer = viewer_id == deed.doer_user_id and deed.status in _DOER_VISIBLE  # D24, D101
    if not (is_reporter or is_doer):
        raise _deed_not_found()
    return (await _deed_outs(session, [deed], viewer_id))[0]


async def my_deeds(
    session: AsyncSession,
    viewer_id: uuid.UUID,
    *,
    role: Role,
    cursor: str | None = None,
    limit: int = _cursor.DEFAULT_PAGE_SIZE,
) -> DeedPage:
    """The viewer's deeds as reporter (every status), or as doer (``verified`` and, from M5,
    ``rejected``: D24, D101; a pending deed has no doer yet anyway)."""
    if role == "reporter":
        stmt = select(Deed).where(Deed.reporter_user_id == viewer_id)
    else:
        stmt = select(Deed).where(Deed.doer_user_id == viewer_id, Deed.status.in_(_DOER_VISIBLE))
    if cursor is not None:
        at, last_id = _cursor.decode(cursor)
        stmt = stmt.where(tuple_(Deed.reported_at, Deed.id) < tuple_(at, last_id))
    rows = list(
        await session.scalars(
            stmt.order_by(Deed.reported_at.desc(), Deed.id.desc()).limit(limit + 1)
        )
    )
    page, more = rows[:limit], len(rows) > limit
    return DeedPage(
        items=await _deed_outs(session, page, viewer_id),
        next_cursor=_cursor.encode(page[-1].reported_at, page[-1].id) if more else None,
    )


async def my_invites(
    session: AsyncSession,
    reporter_id: uuid.UUID,
    *,
    status: InviteFilter | None = None,
    cursor: str | None = None,
    limit: int = _cursor.DEFAULT_PAGE_SIZE,
) -> InvitePage:
    stmt = select(Invite).where(Invite.reporter_user_id == reporter_id)
    if status is not None:
        stmt = stmt.where(Invite.status == InviteStatus(status))
    if cursor is not None:
        at, last_id = _cursor.decode(cursor)
        stmt = stmt.where(tuple_(Invite.created_at, Invite.id) < tuple_(at, last_id))
    rows = list(
        await session.scalars(
            stmt.order_by(Invite.created_at.desc(), Invite.id.desc()).limit(limit + 1)
        )
    )
    page, more = rows[:limit], len(rows) > limit
    return InvitePage(
        items=[invite_out(i) for i in page],
        next_cursor=_cursor.encode(page[-1].created_at, page[-1].id) if more else None,
    )


async def options(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    limits: DeedLimits,
    now: datetime | None = None,
) -> DeedOptions:
    """Values the deed forms need. Every invite created today counts, whatever its status."""
    now = now or utcnow()
    today = ist_day(now)
    reported = await reported_this_month(session, user_id, now)
    used = await session.scalar(
        select(func.count())
        .select_from(Invite)
        .where(Invite.reporter_user_id == user_id, Invite.created_day == today)
    )
    return DeedOptions(
        categories=[CategoryOut(slug=c.slug, label=c.label) for c in limits.categories],
        invites_enabled=limits.invites_enabled,
        invites_remaining_today=max(0, limits.invites_per_day - int(used or 0)),
        invite_expiry_hours=limits.invite_expiry_hours,
        reports_remaining_this_month=max(0, limits.reporter_monthly_limit - reported),
        reporter_monthly_limit=limits.reporter_monthly_limit,
        reporter_doer_monthly_limit=limits.reporter_doer_monthly_limit,
        reporter_monthly_warn_at=limits.reporter_monthly_warn_at,
        reporter_doer_monthly_warn_at=limits.reporter_doer_monthly_warn_at,
        month_resets_at=ist_month(now)[1],
        description_min=limits.description_min,
        description_max=limits.description_max,
        beneficiaries_max=limits.beneficiaries_max,
        scan_submit_minutes=limits.scan_submit_minutes,
        qr_occurred_max_age_minutes=limits.qr_occurred_max_age_minutes,
        max_location_accuracy_m=limits.max_location_accuracy_m,
        service_area_radius_km=limits.service_area.radius_km,
        same_act_cap=limits.same_act_cap,
    )
