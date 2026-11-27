"""Conflict of interest for fraud cases (docs/M5-DESIGN.md D90).

An admin is conflicted for a case when they are a subject, the reporter or doer of any case
deed, or have a verified deed with a subject (either role) in the last ``coi_lookback_days``.
Conflicted admins don't see the case in lists or counts and get 404 on its detail and actions.

``fraud`` can't read the deeds table (D78), so the list filter is built from
``deeds.service.related_users``:

- subjects related to the admin inside the lookback (or the admin themself) -> conflicted;
- a case deed the admin reported or did was verified once (case deeds come from verified
  deeds), so its other party is related to the admin at some time. Only cases with a subject
  related *before* the lookback need a per-deed check, done with ``deeds.service.get_deeds``.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import service as deeds
from app.modules.fraud._limits import FraudConfig
from app.modules.fraud.models import FlagCase, FlagCaseDeed, FlagCaseUser

# Every deed ever verified with the admin (for the deed-level check).
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Visibility:
    """The cases an admin may see: none whose subject is in ``blocked_users``, none of
    ``blocked_cases``."""

    blocked_users: frozenset[uuid.UUID]
    blocked_cases: frozenset[uuid.UUID]

    def condition(self) -> ColumnElement[bool]:
        """A filter on ``FlagCase`` rows."""
        subject_blocked = exists().where(
            FlagCaseUser.case_id == FlagCase.id, FlagCaseUser.user_id.in_(self.blocked_users)
        )
        if self.blocked_cases:
            return and_(~subject_blocked, FlagCase.id.not_in(self.blocked_cases))
        return ~subject_blocked


async def _deed_parties_blocked(
    session: AsyncSession, admin_id: uuid.UUID, case_ids: set[uuid.UUID]
) -> set[uuid.UUID]:
    """The cases among ``case_ids`` with a deed the admin reported or did."""
    if not case_ids:
        return set()
    rows = list(
        await session.execute(
            select(FlagCaseDeed.case_id, FlagCaseDeed.deed_id).where(
                FlagCaseDeed.case_id.in_(case_ids)
            )
        )
    )
    views = await deeds.get_deeds(session, {deed_id for _, deed_id in rows})
    blocked: set[uuid.UUID] = set()
    for case_id, deed_id in rows:
        view = views.get(deed_id)
        if view is not None and admin_id in (view.reporter_id, view.doer_id):
            blocked.add(case_id)
    return blocked


async def visibility(
    session: AsyncSession, admin_id: uuid.UUID, config: FraudConfig, now: datetime
) -> Visibility:
    recent = await deeds.related_users(
        session, admin_id, now - timedelta(days=config.coi_lookback_days)
    )
    blocked_users = frozenset({admin_id, *recent})
    older = await deeds.related_users(session, admin_id, _EPOCH) - blocked_users
    candidates: set[uuid.UUID] = set()
    if older:
        candidates = set(
            await session.scalars(
                select(FlagCaseUser.case_id).where(FlagCaseUser.user_id.in_(older)).distinct()
            )
        )
    blocked_cases = await _deed_parties_blocked(session, admin_id, candidates)
    return Visibility(blocked_users=blocked_users, blocked_cases=frozenset(blocked_cases))


async def is_conflicted(
    session: AsyncSession,
    admin_id: uuid.UUID,
    case_id: uuid.UUID,
    config: FraudConfig,
    now: datetime,
) -> bool:
    subjects = set(
        await session.scalars(select(FlagCaseUser.user_id).where(FlagCaseUser.case_id == case_id))
    )
    if admin_id in subjects:
        return True
    if await _deed_parties_blocked(session, admin_id, {case_id}):
        return True
    recent = await deeds.related_users(
        session, admin_id, now - timedelta(days=config.coi_lookback_days)
    )
    return bool(recent & subjects)
