"""Fraud public interface (docs/M5-DESIGN.md §3.1, D78), for scoring, deeds and M8 prizes.

Other modules may import only this file from fraud, and only what D78 lists:
``scoring`` -> ``DeedHoldChanged``, ``AppealDecided``, ``held_deed_ids``, ``is_held``,
``confirmed_case_for_user`` (and ``concentration_rule``, D97; ``override_rules`` and
``case_standing`` for overrides and revocations, D91, D92); ``deeds`` -> ``FlagDecisionFinal``.
``tests/test_architecture.py`` enforces the lists. This file imports nothing from ``scoring``.

Nothing here may reach a public or member response: flags are private (D95).
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.fraud._limits import load_fraud
from app.modules.fraud.events import (
    AppealDecided,
    DeedHoldChanged,
    FlagCaseResolved,
    FlagDecisionFinal,
    FlagRaised,
)
from app.modules.fraud.models import (
    Appeal,
    AppealOutcome,
    DecisionNotice,
    FlagCase,
    FlagCaseOutcome,
    FlagCaseStatus,
    FlagCaseUser,
    PrizeHold,
)

__all__ = [
    "AppealDecided",
    "CaseStanding",
    "ConcentrationRule",
    "DeedHoldChanged",
    "FlagCaseResolved",
    "FlagDecisionFinal",
    "FlagRaised",
    "OverrideRules",
    "case_standing",
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


async def confirmed_case_for_user(
    session: AsyncSession, case_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Whether ``case_id`` lets an admin revoke ``user_id``'s titles and badges (D92): resolved
    ``confirmed``, the user is a subject with an adverse outcome (a case notice is written only
    for those), and the decision is final for them (D100: the notice's ``final_at`` is set) and
    was not overturned on appeal."""
    overturned = exists().where(
        Appeal.notice_id == DecisionNotice.id, Appeal.outcome == AppealOutcome.OVERTURNED
    )
    found = await session.scalar(
        select(
            exists()
            .where(
                FlagCase.id == case_id,
                FlagCase.status == FlagCaseStatus.RESOLVED,
                FlagCase.outcome == FlagCaseOutcome.CONFIRMED,
            )
            .where(exists().where(FlagCaseUser.case_id == case_id, FlagCaseUser.user_id == user_id))
            .where(
                exists().where(
                    DecisionNotice.case_id == case_id,
                    DecisionNotice.user_id == user_id,
                    DecisionNotice.final_at.is_not(None),
                    ~overturned,
                )
            )
        )
    )
    return bool(found)


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


CaseStanding = Literal["unknown", "not_confirmed", "not_final", "final"]


async def case_standing(
    session: AsyncSession, case_id: uuid.UUID, user_id: uuid.UUID
) -> CaseStanding:
    """Where ``case_id`` stands for ``user_id`` (D91, D92), so scoring can tell its errors apart:
    ``unknown`` (no such case), ``not_confirmed`` (not resolved ``confirmed``, the user is not a
    subject with an adverse outcome, or their appeal was overturned), ``not_final`` (confirmed,
    but the appeal window is open or an appeal is pending, D100) or ``final`` (the same as
    ``confirmed_case_for_user``)."""
    case = await session.get(FlagCase, case_id)
    if case is None:
        return "unknown"
    if case.status != FlagCaseStatus.RESOLVED or case.outcome != FlagCaseOutcome.CONFIRMED:
        return "not_confirmed"
    notice = await session.scalar(
        select(DecisionNotice).where(
            DecisionNotice.case_id == case_id, DecisionNotice.user_id == user_id
        )
    )
    if notice is None:
        return "not_confirmed"
    overturned = await session.scalar(
        select(
            exists().where(
                Appeal.notice_id == notice.id, Appeal.outcome == AppealOutcome.OVERTURNED
            )
        )
    )
    if overturned:
        return "not_confirmed"
    return "final" if notice.final_at is not None else "not_final"


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
