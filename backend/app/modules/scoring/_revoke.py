"""Revoking titles and badges for a confirmed case (docs/M5-DESIGN.md D90, D92, D100;
§3.4 #14 to #16).

A revocation needs ``case_id`` to be resolved ``confirmed`` with the person as a subject with an
adverse outcome, and the decision final for them (appeal window closed, or appeal upheld):
``fraud.service.confirmed_case_for_user``. Otherwise 409 ``decision_not_final`` while the window
is open or an appeal is pending, else ``case_not_confirmed`` (``fraud.service.case_standing``
tells the two apart). ``revoked_*`` is set once (the database refuses a second change); the title
is not passed to the next person and the final board is not recomputed (Q67): the board row
shows as "Unavailable account" (Q72, ``_reads``). Admins can't see or revoke their own, or a
related person's (D90: 404).

SEC5-L4: the admin must also be able to see the case in the case queue
(``fraud.service.case_visible_to``, the queue's own conflict rule, review N5), else 404 as for an
unknown title or badge. A title also
needs the case to cover its month (``fraud.service.case_covers_month``), else 409
``case_not_for_this_month``.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import Conflict, NotFound
from app.core.events import emit
from app.modules.deeds import service as deeds
from app.modules.fraud.service import (
    OverrideRules,
    case_covers_month,
    case_standing,
    case_visible_to,
    confirmed_case_for_user,
    override_rules,
)
from app.modules.identity import service as identity
from app.modules.scoring import _coi, _labels
from app.modules.scoring._limits import load_leaderboard
from app.modules.scoring._overrides import clean_note
from app.modules.scoring._reads import _TitleLabels
from app.modules.scoring.events import BadgeRevoked, HeroTitleRevoked
from app.modules.scoring.models import HeroTitle, LedgerRole, PointsLedger, UserBadge
from app.modules.scoring.schemas import (
    AdminBadge,
    AdminTitle,
    AdminUserScoring,
    RevokeIn,
    UserSummary,
)


def _user_not_found() -> NotFound:
    return NotFound("We couldn't find that person.", code="user_not_found")


async def _check_person(
    session: AsyncSession,
    admin_id: uuid.UUID,
    user_id: uuid.UUID,
    rules: OverrideRules,
    now: datetime,
    error: NotFound,
) -> None:
    if await _coi.conflicted(session, admin_id, {user_id}, rules.coi_lookback_days, now):
        raise error


async def _role_points(session: AsyncSession, user_id: uuid.UUID, role: LedgerRole) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
            PointsLedger.user_id == user_id, PointsLedger.role == role
        )
    )
    return int(total or 0)


async def user_view(session: AsyncSession, user_id: uuid.UUID) -> AdminUserScoring:
    """#14 body: every title and badge (revoked ones too) and the points."""
    found = await identity.public_summaries(session, {user_id})
    summary = found[user_id]
    titles = list(
        await session.scalars(
            select(HeroTitle)
            .where(HeroTitle.user_id == user_id)
            .order_by(HeroTitle.period_month.desc(), HeroTitle.place, HeroTitle.region_id)
        )
    )
    regions = await deeds.regions_by_id(session, {t.region_id for t in titles})
    labels = _TitleLabels(session)
    title_rows: list[AdminTitle] = []
    for t in titles:
        name = regions[t.region_id].name if t.region_id in regions else ""
        title_rows.append(
            AdminTitle(
                id=t.id,
                label=await labels.label(t, name),
                period_month=_labels.month_param(t.period_month),
                region_name=name,
                level=t.level_kind.value,
                place=t.place,
                revoked_at=t.revoked_at,
                revoked_reason=t.revoked_reason,
            )
        )
    _, config = await load_leaderboard(session)
    badge_labels = {b.slug: b.label for b in config.badges}
    badges = await session.scalars(
        select(UserBadge)
        .where(UserBadge.user_id == user_id)
        .order_by(UserBadge.awarded_at, UserBadge.badge_slug)
    )
    return AdminUserScoring(
        user=UserSummary(handle=summary.handle, display_name=summary.display_name),
        titles=title_rows,
        badges=[
            AdminBadge(
                id=b.id,
                slug=b.badge_slug,
                label=badge_labels.get(b.badge_slug, b.badge_slug),
                awarded_at=b.awarded_at,
                revoked_at=b.revoked_at,
                revoked_reason=b.revoked_reason,
            )
            for b in badges
        ],
        doer_points=await _role_points(session, user_id, LedgerRole.DOER),
        reporter_points=await _role_points(session, user_id, LedgerRole.REPORTER),
    )


async def admin_user(
    session: AsyncSession, admin_id: uuid.UUID, handle: str, *, now: datetime | None = None
) -> AdminUserScoring:
    """#14. 404 for an unknown handle, the admin themself and a related person (D90)."""
    now = now or datetime.now(UTC)
    rules = await override_rules(session)
    user = await identity.get_user_by_handle(session, handle)
    if user is None:
        raise _user_not_found()
    await _check_person(session, admin_id, user.id, rules, now, _user_not_found())
    return await user_view(session, user.id)


async def _check_case(session: AsyncSession, case_id: uuid.UUID, user_id: uuid.UUID) -> None:
    if await confirmed_case_for_user(session, case_id, user_id):
        return
    if await case_standing(session, case_id, user_id) == "not_final":
        raise Conflict(
            "This can be revoked once the decision is final: after the appeal window closes, or "
            "once an appeal is decided.",
            code="decision_not_final",
        )
    raise Conflict("Revoking needs a confirmed case about this person.", code="case_not_confirmed")


async def _case_hidden(
    session: AsyncSession,
    admin_id: uuid.UUID,
    case_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
) -> bool:
    """SEC5-L4, review N5: an existing case the admin can't see in the queue. An unknown case
    is left to ``_check_case`` (409 ``case_not_confirmed``)."""
    if await case_visible_to(session, admin_id, case_id, now=now):
        return False
    return await case_standing(session, case_id, user_id) != "unknown"


def _already_revoked() -> Conflict:
    return Conflict("This has already been revoked.", code="already_revoked")


async def revoke_title(
    session: AsyncSession,
    admin_id: uuid.UUID,
    title_id: uuid.UUID,
    body: RevokeIn,
    *,
    now: datetime | None = None,
) -> uuid.UUID:
    """#15. Returns the title's owner. The caller commits."""
    now = now or datetime.now(UTC)
    rules = await override_rules(session)
    note = clean_note(body.note, rules)
    not_found = NotFound("We couldn't find that title.", code="title_not_found")
    title = await session.get(HeroTitle, title_id, with_for_update=True, populate_existing=True)
    if title is None:
        raise not_found
    await _check_person(session, admin_id, title.user_id, rules, now, not_found)
    if title.revoked_at is not None:
        raise _already_revoked()
    if await _case_hidden(session, admin_id, body.case_id, title.user_id, now):
        raise not_found
    await _check_case(session, body.case_id, title.user_id)
    if not await case_covers_month(session, body.case_id, title.period_month):
        raise Conflict(
            "This case has no deeds from the title's month.", code="case_not_for_this_month"
        )
    title.revoked_at, title.revoked_reason, title.revoked_by = now, note, admin_id
    await session.flush()
    record_audit(
        session,
        entity_type="hero_title",
        entity_id=str(title.id),
        action="hero_title.revoked",
        actor_id=admin_id,
        old_value={"revoked": False},
        new_value={
            "revoked": True,
            "case_id": str(body.case_id),
            "user_id": str(title.user_id),
            "period_month": title.period_month.isoformat(),
            "region_id": title.region_id,
            "place": title.place,
        },
        reason=note,
    )
    emit(
        session,
        HeroTitleRevoked(
            title_id=title.id,
            user_id=title.user_id,
            period_month=title.period_month,
            region_id=title.region_id,
            case_id=body.case_id,
        ),
    )
    return title.user_id


async def revoke_badge(
    session: AsyncSession,
    admin_id: uuid.UUID,
    badge_id: uuid.UUID,
    body: RevokeIn,
    *,
    now: datetime | None = None,
) -> uuid.UUID:
    """#16. Returns the badge's owner. The caller commits."""
    now = now or datetime.now(UTC)
    rules = await override_rules(session)
    note = clean_note(body.note, rules)
    not_found = NotFound("We couldn't find that badge.", code="badge_not_found")
    badge = await session.get(UserBadge, badge_id, with_for_update=True, populate_existing=True)
    if badge is None:
        raise not_found
    await _check_person(session, admin_id, badge.user_id, rules, now, not_found)
    if badge.revoked_at is not None:
        raise _already_revoked()
    if await _case_hidden(session, admin_id, body.case_id, badge.user_id, now):
        raise not_found
    await _check_case(session, body.case_id, badge.user_id)
    badge.revoked_at, badge.revoked_reason, badge.revoked_by = now, note, admin_id
    await session.flush()
    record_audit(
        session,
        entity_type="user_badge",
        entity_id=str(badge.id),
        action="user_badge.revoked",
        actor_id=admin_id,
        old_value={"revoked": False},
        new_value={
            "revoked": True,
            "case_id": str(body.case_id),
            "user_id": str(badge.user_id),
            "badge_slug": badge.badge_slug,
        },
        reason=note,
    )
    emit(
        session,
        BadgeRevoked(
            badge_id=badge.id,
            user_id=badge.user_id,
            badge_slug=badge.badge_slug,
            case_id=body.case_id,
        ),
    )
    return badge.user_id
