"""Prize holds for scoring tests (docs/M5-DESIGN.md D79, D93). A hold comes only from an admin
confirming a case (D80); until the review endpoints exist, these write the confirmed case and
its ``prize_holds`` row directly through the fraud models and emit ``DeedHoldChanged`` as the
decision will, so scoring's handler can be run with ``run_scoring``."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit
from app.modules.fraud._cases import subject_key
from app.modules.fraud.models import (
    FlagCase,
    FlagCaseDeed,
    FlagCaseOutcome,
    FlagCaseStatus,
    FlagCaseType,
    HoldReleaseReason,
    PrizeHold,
)
from app.modules.fraud.service import DeedHoldChanged
from tests.deeds.helpers import Member


async def hold(session: AsyncSession, deed_id: uuid.UUID, admin: Member) -> uuid.UUID:
    """A confirmed case holding ``deed_id``; emits ``DeedHoldChanged(held=True)``. Returns the
    case id. Each call is a new case."""
    now = datetime.now(UTC)
    case = FlagCase(
        id=uuid.uuid4(),
        type=FlagCaseType.RECIPROCAL,
        status=FlagCaseStatus.RESOLVED,
        outcome=FlagCaseOutcome.CONFIRMED,
        subject_key=subject_key("reciprocal", [uuid.uuid4()]),
        signal={},
        detector_config_version=1,
        deed_count=1,
        held_count=1,
        resolved_at=now,
        resolved_by=admin.user_id,
    )
    session.add(case)
    await session.flush()
    session.add(FlagCaseDeed(case_id=case.id, deed_id=deed_id))
    session.add(PrizeHold(deed_id=deed_id, case_id=case.id))
    await session.flush()
    emit(session, DeedHoldChanged(deed_id=deed_id, held=True, case_id=case.id))
    await session.flush()
    return case.id


async def release(session: AsyncSession, deed_id: uuid.UUID, case_id: uuid.UUID) -> None:
    """Release the case's hold on the deed (as an overturned appeal does) and emit
    ``DeedHoldChanged(held=False)``, even if another case still holds the deed (scoring reads
    the live state, so the event's flag never matters)."""
    row = await session.scalar(
        select(PrizeHold).where(PrizeHold.deed_id == deed_id, PrizeHold.case_id == case_id)
    )
    assert row is not None and row.released_at is None
    row.released_at = datetime.now(UTC)
    row.release_reason = HoldReleaseReason.APPEAL_OVERTURNED
    await session.flush()
    emit(session, DeedHoldChanged(deed_id=deed_id, held=False, case_id=case_id))
    await session.flush()
