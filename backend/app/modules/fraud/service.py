"""Fraud public interface (docs/M5-DESIGN.md §3.1, D78), for scoring, deeds and M8 prizes.

Other modules may import only this file from fraud, and only what D78 lists:
``scoring`` -> ``DeedHoldChanged``, ``AppealDecided``, ``held_deed_ids``, ``is_held``,
``confirmed_case_for_user`` (and ``concentration_rule``, D97); ``deeds`` -> ``FlagDecisionFinal``.
``tests/test_architecture.py`` enforces the lists. This file imports nothing from ``scoring``.

Nothing here may reach a public or member response: flags are private (D95).
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

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
    "ConcentrationRule",
    "DeedHoldChanged",
    "FlagCaseResolved",
    "FlagDecisionFinal",
    "FlagRaised",
    "concentration_rule",
    "confirmed_case_for_user",
    "held_deed_ids",
    "is_held",
    "open_case_user_ids",
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
