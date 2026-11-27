"""Fraud public interface (docs/M5-DESIGN.md §3.1, D78), for scoring, deeds and M8 prizes.

Other modules may import only this file from fraud, and only what D78 lists:
``scoring`` -> ``DeedHoldChanged``, ``AppealDecided``, ``held_deed_ids``, ``is_held``,
``confirmed_case_for_user`` (and ``concentration_rule``, D97; ``override_rules`` and
``case_standing``, ``case_has_subject``, ``case_covers_month`` and ``case_visible_to`` for
overrides and revocations, D91, D92, SEC5-L3, L4, review N5); ``deeds`` -> ``FlagDecisionFinal``;
``notifications`` -> ``MemberReviewUpdate`` and ``ReviewsSeen`` (M6A D104, D112).
``tests/test_architecture.py`` enforces the lists. This file imports nothing from ``scoring``.

Nothing here may reach a public or member response: flags are private (D95).
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds.service import get_deeds
from app.modules.fraud import _coi
from app.modules.fraud._limits import load_fraud
from app.modules.fraud.events import (
    AppealDecided,
    DeedHoldChanged,
    FlagCaseResolved,
    FlagDecisionFinal,
    FlagRaised,
    MemberReviewUpdate,
    ReviewsSeen,
)
from app.modules.fraud.models import (
    Appeal,
    AppealOutcome,
    DecisionNotice,
    FlagCase,
    FlagCaseDeed,
    FlagCaseOutcome,
    FlagCaseStatus,
    FlagCaseUser,
    NoticeKind,
    PrizeHold,
)

CaseStanding = Literal["unknown", "not_confirmed", "not_final", "final"]

__all__ = [
    "AppealDecided",
    "CaseStanding",
    "ConcentrationRule",
    "DeedHoldChanged",
    "FlagCaseResolved",
    "FlagDecisionFinal",
    "FlagRaised",
    # Member-safe events notifications hears (M6A D104, D112).
    "MemberReviewUpdate",
    "OverrideRules",
    "ReviewsSeen",
    "case_covers_month",
    "case_has_subject",
    "case_standing",
    "case_visible_to",
    "concentration_rule",
    "confirmed_case_for_user",
    "held_deed_ids",
    "is_held",
    "open_case_user_ids",
    "override_rules",
]


async def is_held(session: AsyncSession, deed_id: uuid.UUID) -> bool:
    """Whether the deed has any unreleased prize hold (D79): out of boards and prize rank."""
    held = await session.scalar(
        select(exists().where(PrizeHold.deed_id == deed_id, PrizeHold.released_at.is_(None)))
    )
    return bool(held)


async def held_deed_ids(session: AsyncSession, deed_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """The deeds among ``deed_ids`` that are held, in one query."""
    ids = set(deed_ids)
    if not ids:
        return set()
    rows = await session.scalars(
        select(PrizeHold.deed_id)
        .where(PrizeHold.deed_id.in_(ids), PrizeHold.released_at.is_(None))
        .distinct()
    )
    return set(rows)


async def _notice_states(
    session: AsyncSession, case_id: uuid.UUID
) -> dict[uuid.UUID, tuple[bool, bool, bool]]:
    """Subject -> (final, appeal open, overturned) of their notice in ``case_id``."""
    out: dict[uuid.UUID, tuple[bool, bool, bool]] = {}
    for user_id, final_at, appeal_id, outcome in await session.execute(
        select(DecisionNotice.user_id, DecisionNotice.final_at, Appeal.id, Appeal.outcome)
        .outerjoin(Appeal, Appeal.notice_id == DecisionNotice.id)
        .where(DecisionNotice.case_id == case_id, DecisionNotice.kind == NoticeKind.CASE)
    ):
        appeal_open = appeal_id is not None and outcome is None
        out[user_id] = (
            final_at is not None and not appeal_open,
            appeal_open,
            outcome == AppealOutcome.OVERTURNED,
        )
    return out


async def _co_parties(
    session: AsyncSession, case_id: uuid.UUID, user_id: uuid.UUID
) -> set[uuid.UUID]:
    """The other subjects of ``case_id`` who share a case deed with ``user_id`` (the other
    party on it), whose decision is part of "final for everyone on it" (D100)."""
    subjects = set(
        await session.scalars(select(FlagCaseUser.user_id).where(FlagCaseUser.case_id == case_id))
    )
    deed_ids = set(
        await session.scalars(select(FlagCaseDeed.deed_id).where(FlagCaseDeed.case_id == case_id))
    )
    parties: set[uuid.UUID] = set()
    for view in (await get_deeds(session, deed_ids)).values():
        people = {p for p in (view.reporter_id, view.doer_id) if p is not None}
        if user_id in people:
            parties |= people
    return (parties & subjects) - {user_id}


async def case_standing(
    session: AsyncSession, case_id: uuid.UUID, user_id: uuid.UUID
) -> CaseStanding:
    """Where ``case_id`` stands for ``user_id`` (D91, D92, D100), so scoring can tell its
    errors apart:

    - ``unknown``: no such case;
    - ``not_confirmed``: not resolved ``confirmed``, the user is not a subject with an adverse
      outcome (no case notice), or the appeal of the user or of a co-subject who shares deeds
      with them in the case was overturned (an overturn restores the shared deeds, so it blocks
      revocation for that case, D100);
    - ``not_final``: confirmed, but the appeal window of the user or of such a co-subject is
      still open, or one of their appeals is pending (SEC5-M4);
    - ``final``: the decision is final for everyone on the user's deeds."""
    case = await session.get(FlagCase, case_id)
    if case is None:
        return "unknown"
    if case.status != FlagCaseStatus.RESOLVED or case.outcome != FlagCaseOutcome.CONFIRMED:
        return "not_confirmed"
    states = await _notice_states(session, case_id)
    own = states.get(user_id)
    if own is None:
        return "not_confirmed"
    # Co-parties without a notice had no adverse outcome: nothing of theirs can be appealed.
    involved = [own] + [
        states[p] for p in await _co_parties(session, case_id, user_id) if p in states
    ]
    if any(overturned for _, _, overturned in involved):
        return "not_confirmed"
    return "final" if all(final for final, _, _ in involved) else "not_final"


async def confirmed_case_for_user(
    session: AsyncSession, case_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Whether ``case_id`` lets an admin revoke ``user_id``'s titles and badges (D92, D100):
    resolved ``confirmed``, the user is a subject with an adverse outcome (a case notice), and
    the decision is final for the user **and** for every co-subject who shares a case deed with
    them: each such notice is final with no appeal pending, and none was overturned (SEC5-M4).
    The same as ``case_standing(...) == "final"``."""
    return await case_standing(session, case_id, user_id) == "final"


async def case_has_subject(session: AsyncSession, case_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Whether ``user_id`` is a subject of ``case_id`` (false for an unknown case)."""
    found = await session.scalar(
        select(exists().where(FlagCaseUser.case_id == case_id, FlagCaseUser.user_id == user_id))
    )
    return bool(found)


async def case_visible_to(
    session: AsyncSession,
    admin_id: uuid.UUID,
    case_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> bool:
    """Whether ``case_id`` exists and the admin may see it in the case queue (D90): the same
    rule as the queue (``_coi.visibility``), so a case with a subject related to the admin
    inside the lookback, or with a deed the admin reported or did (at any time), is not
    visible. False for an unknown case."""
    _, config = await load_fraud(session)
    visibility = await _coi.visibility(session, admin_id, config, now or datetime.now(UTC))
    found = await session.scalar(
        select(exists().where(FlagCase.id == case_id, visibility.condition()))
    )
    return bool(found)


async def case_covers_month(session: AsyncSession, case_id: uuid.UUID, period_month: date) -> bool:
    """Whether any deed of ``case_id`` was verified in the calendar month of ``period_month``
    in the ``fraud`` config's time zone (IST), so a title of that month may be revoked for it
    (SEC5-L4)."""
    _, config = await load_fraud(session)
    zone = config.zone
    start = datetime(period_month.year, period_month.month, 1, tzinfo=zone)
    end = (start + timedelta(days=32)).replace(day=1)
    deed_ids = set(
        await session.scalars(select(FlagCaseDeed.deed_id).where(FlagCaseDeed.case_id == case_id))
    )
    for view in (await get_deeds(session, deed_ids)).values():
        at = view.verified_at
        if at is not None and start <= at.astimezone(zone) < end:
            return True
    return False


async def open_case_user_ids(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """The users among ``user_ids`` who are a subject of an open or parked case (M8: an open
    case delays a payout, never denies it, A14)."""
    ids = set(user_ids)
    if not ids:
        return set()
    rows = await session.scalars(
        select(FlagCaseUser.user_id)
        .join(FlagCase, FlagCase.id == FlagCaseUser.case_id)
        .where(
            FlagCaseUser.user_id.in_(ids),
            FlagCase.status.in_((FlagCaseStatus.OPEN, FlagCaseStatus.PARKED)),
        )
        .distinct()
    )
    return set(rows)


@dataclass(frozen=True)
class ConcentrationRule:
    """The reporter-concentration rule (D85), kept in the ``fraud`` config so it lives in one
    place (D97). Scoring skips its check while ``enabled`` is false."""

    enabled: bool
    max_reporter_share_pct: int
    min_doer_points: int
    config_version: int


async def concentration_rule(session: AsyncSession) -> ConcentrationRule:
    version, config = await load_fraud(session)
    return ConcentrationRule(
        enabled=config.enabled("concentration"),
        max_reporter_share_pct=config.concentration.max_reporter_share_pct,
        min_doer_points=config.concentration.min_doer_points,
        config_version=version,
    )


@dataclass(frozen=True)
class OverrideRules:
    """The ``fraud`` config values for score overrides and revocations (D90, D91, Q77), kept in
    one namespace with the review rules (D97)."""

    reason_codes: tuple[str, ...]
    note_min_chars: int
    note_max_chars: int
    max_points: int
    second_admin_max_cut_points: int
    second_admin_cut_to_zero: bool
    second_admin_expire_days: int
    coi_lookback_days: int
    page_size: int
    rate_admin_read: str
    rate_admin_write: str


async def override_rules(session: AsyncSession) -> OverrideRules:
    _, config = await load_fraud(session)
    second = config.override_second_admin
    return OverrideRules(
        reason_codes=config.override_reason_codes,
        note_min_chars=config.note_min_chars,
        note_max_chars=config.note_max_chars,
        max_points=config.override_max_points,
        second_admin_max_cut_points=second.max_cut_points,
        second_admin_cut_to_zero=second.cut_to_zero,
        second_admin_expire_days=second.expire_days,
        coi_lookback_days=config.coi_lookback_days,
        page_size=config.page_size,
        rate_admin_read=config.rate_admin_read,
        rate_admin_write=config.rate_admin_write,
    )
