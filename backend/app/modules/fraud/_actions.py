"""What every admin action on a case shares (docs/M5-DESIGN.md D88, D90, §4.1): the case row
locked ``FOR UPDATE`` (404 for a conflicted admin), one append-only ``flag_case_decisions`` row
and one ``audit_log`` row (``entity_type="flag_case"``) per action."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.modules.fraud import _coi
from app.modules.fraud._common import case_not_found
from app.modules.fraud._limits import FraudConfig
from app.modules.fraud.models import (
    FlagCase,
    FlagCaseAction,
    FlagCaseDecision,
    FlagCaseDeed,
    FlagCaseUser,
)

# audit_log ``action`` per case action.
AUDIT_ACTIONS: dict[FlagCaseAction, str] = {
    FlagCaseAction.PARK: "flag_case.parked",
    FlagCaseAction.UNPARK: "flag_case.unparked",
    FlagCaseAction.REQUEST_EXPLANATION: "flag_case.explanation_requested",
    FlagCaseAction.CONFIRM: "flag_case.confirmed",
    FlagCaseAction.DISMISS: "flag_case.dismissed",
}


async def visible_case(
    session: AsyncSession,
    admin_id: uuid.UUID,
    case_id: uuid.UUID,
    config: FraudConfig,
    now: datetime,
    *,
    lock: bool = False,
) -> FlagCase:
    """The case, or 404 ``case_not_found`` when it doesn't exist or the admin is conflicted
    (D90). With ``lock`` the row is locked ``FOR UPDATE`` (the first lock of every action,
    §4.1)."""
    stmt = select(FlagCase).where(FlagCase.id == case_id)
    if lock:
        stmt = stmt.with_for_update()
    case = await session.scalar(stmt.execution_options(populate_existing=True))
    if case is None or await _coi.is_conflicted(session, admin_id, case_id, config, now):
        raise case_not_found()
    return case


async def subject_ids(session: AsyncSession, case_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        await session.scalars(select(FlagCaseUser.user_id).where(FlagCaseUser.case_id == case_id))
    )


async def deed_ids(session: AsyncSession, case_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        await session.scalars(select(FlagCaseDeed.deed_id).where(FlagCaseDeed.case_id == case_id))
    )


def record(
    session: AsyncSession,
    case: FlagCase,
    action: FlagCaseAction,
    *,
    actor_id: uuid.UUID,
    note: str,
    now: datetime,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
) -> FlagCaseDecision:
    """Add the decision row and the audit row (flushed by the caller)."""
    decision = FlagCaseDecision(
        id=uuid.uuid4(),
        case_id=case.id,
        action=action,
        actor_id=actor_id,
        note=note,
        created_at=now,
    )
    session.add(decision)
    entry = record_audit(
        session,
        entity_type="flag_case",
        entity_id=str(case.id),
        action=AUDIT_ACTIONS[action],
        actor_id=actor_id,
        old_value=old_value,
        new_value={"type": case.type.value, **(new_value or {})},
        reason=note,
    )
    entry.created_at = now  # the same time as the decision row
    return decision
