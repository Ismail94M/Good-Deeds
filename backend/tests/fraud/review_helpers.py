"""Helpers for the admin review tests (docs/M5-DESIGN.md §3.3): admins, cases opened straight
from a ``Finding`` (the detectors have their own tests), explanation replies and reads."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.fraud import _explain
from app.modules.fraud._cases import raise_flag
from app.modules.fraud._limits import load_fraud
from app.modules.fraud.detectors.base import Finding
from app.modules.fraud.models import (
    ExplanationRequest,
    FlagCase,
    FlagCaseDecision,
    FlagCaseType,
)
from app.modules.fraud.schemas import ExplanationsIn
from app.modules.identity.models import Role, User
from tests.deeds.helpers import Member, make_member, members
from tests.fraud.helpers import DAY, days_ago, deed

NOTE = "Looked at the deeds and the people involved."


async def with_role(session: AsyncSession, member: Member, role: Role) -> Member:
    await session.execute(update(User).where(User.id == member.user_id).values(role=role))
    await session.commit()
    return member


async def admin(session: AsyncSession) -> Member:
    return await with_role(session, await make_member(session), Role.ADMIN)


async def open_case(
    session: AsyncSession,
    case_type: FlagCaseType,
    subjects: list[Member],
    deed_ids: list[uuid.UUID],
    signal: dict[str, Any] | None = None,
) -> uuid.UUID:
    version, config = await load_fraud(session)
    result = await raise_flag(
        session,
        Finding(
            case_type=case_type,
            subject_ids=frozenset(m.user_id for m in subjects),
            deed_ids=frozenset(deed_ids),
            signal=signal or {},
        ),
        config,
        config_version=version,
    )
    assert result is not None
    await session.commit()
    return result.case_id


@dataclass(frozen=True)
class Pair:
    a: Member
    b: Member
    a_to_b: uuid.UUID
    b_to_a: uuid.UUID
    case_id: uuid.UUID


async def reciprocal_case(session: AsyncSession) -> Pair:
    a, b = await members(session, 2)
    t0 = days_ago(5)
    ab = await deed(session, a, b, t0)
    ba = await deed(session, b, a, t0 + DAY)
    case_id = await open_case(
        session,
        FlagCaseType.RECIPROCAL,
        [a, b],
        [ab, ba],
        {"a_to_b": 1, "b_to_a": 1, "first_gap_hours": 24},
    )
    return Pair(a, b, ab, ba, case_id)


@dataclass(frozen=True)
class Ring:
    people: list[Member]
    # deeds[i]: people[i] -> people[i + 1]
    deeds: list[uuid.UUID]
    case_id: uuid.UUID


async def ring_case(session: AsyncSession) -> Ring:
    people = await members(session, 3)
    t0 = days_ago(6)
    made = [await deed(session, people[i], people[(i + 1) % 3], t0 + i * DAY) for i in range(3)]
    case_id = await open_case(
        session,
        FlagCaseType.RING,
        people,
        made,
        {"size": 3, "internal_share_pct": 100, "edges": 3},
    )
    return Ring(people, made, case_id)


async def ask(
    session: AsyncSession,
    who: Member,
    case_id: uuid.UUID,
    *people: Member,
    now: datetime | None = None,
) -> None:
    """Request explanations through the service (``now`` in the past makes the due date pass)."""
    _, config = await load_fraud(session)
    await _explain.request_explanations(
        session,
        who.user_id,
        case_id,
        ExplanationsIn(handles=[p.handle for p in people] if people else None, note=NOTE),
        config=config,
        now=now or datetime.now(UTC),
    )
    await session.commit()


async def reply(session: AsyncSession, case_id: uuid.UUID, person: Member) -> None:
    """What the member reply endpoint (B45) will write."""
    request = await session.scalar(
        select(ExplanationRequest).where(
            ExplanationRequest.case_id == case_id, ExplanationRequest.user_id == person.user_id
        )
    )
    assert request is not None
    request.reply = "We are neighbours and help each other."
    request.replied_at = datetime.now(UTC)
    await session.commit()


async def case_row(session: AsyncSession, case_id: uuid.UUID) -> FlagCase:
    row = await session.scalar(
        select(FlagCase).where(FlagCase.id == case_id).execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def decisions(session: AsyncSession, case_id: uuid.UUID) -> list[str]:
    rows = await session.scalars(
        select(FlagCaseDecision.action)
        .where(FlagCaseDecision.case_id == case_id)
        .order_by(FlagCaseDecision.created_at, FlagCaseDecision.id)
    )
    return [a.value for a in rows]


async def audit_actions(session: AsyncSession, case_id: uuid.UUID) -> list[str]:
    rows = await session.scalars(
        select(AuditLog.action)
        .where(AuditLog.entity_type == "flag_case", AuditLog.entity_id == str(case_id))
        .order_by(AuditLog.created_at, AuditLog.id)
    )
    return list(rows)


def confirm_body(actions: dict[uuid.UUID, str], note: str = NOTE) -> dict[str, Any]:
    return {
        "outcome": "confirmed",
        "note": note,
        "deeds": [{"deed_id": str(d), "action": a} for d, a in actions.items()],
    }


def dismiss_body(note: str = NOTE) -> dict[str, Any]:
    return {"outcome": "dismissed", "note": note}
