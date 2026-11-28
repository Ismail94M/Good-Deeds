"""Helpers for fraud tests: real verified deeds (through the QR path), the fraud handler run as
the worker would, config versions published through the validated helper, and case reads."""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent, handlers_for, run_handler
from app.core.runtime_config import publish
from app.modules.fraud._cases import subject_key
from app.modules.fraud._limits import FraudConfig, load_fraud
from app.modules.fraud.models import (
    FlagCase,
    FlagCaseDeed,
    FlagCaseOutcome,
    FlagCaseStatus,
    FlagCaseType,
)
from tests.deeds.helpers import OUTBOX_WRITE_ORDER, Member, scan_and_report

FRAUD_PREFIX = "app.modules.fraud."
DAY = timedelta(days=1)


def days_ago(n: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=n)


async def deed(session: AsyncSession, reporter: Member, doer: Member, at: datetime) -> uuid.UUID:
    """A verified QR deed ``reporter -> doer`` at server time ``at``."""
    out = await scan_and_report(session, reporter, doer, at=at)
    assert out.status == "verified"
    return out.id


async def run_fraud(session: AsyncSession, *deed_ids: uuid.UUID) -> int:
    """Run every fraud handler not yet run for the outbox events about these deeds, oldest
    first. Returns how many ran."""
    rows = await session.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.payload["deed_id"].astext.in_([str(d) for d in deed_ids]))
        .order_by(*OUTBOX_WRITE_ORDER)
    )
    ran = 0
    for row in list(rows):
        for name in handlers_for(row.event_type):
            if name.startswith(FRAUD_PREFIX) and await run_handler(session, row.id, name):
                ran += 1
    return ran


async def publish_fraud(session: AsyncSession, **changes: Any) -> FraudConfig:
    """Publish a new ``fraud`` config version (validated, rolled back with the test). Nested
    sections are merged: ``reciprocal={"window_days": 7}`` changes that key only."""
    _, current = await load_fraud(session)
    values = current.model_dump(mode="json")
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(values.get(key), dict):
            values[key] = {**values[key], **value}
        else:
            values[key] = value
    new = FraudConfig.model_validate(values)
    await publish(session, new, actor_id=None, reason="test")
    await session.flush()
    return new


async def cases_for(
    session: AsyncSession, case_type: FlagCaseType, *people: Member
) -> list[FlagCase]:
    """Every case (any status) of this type about exactly these people (cases opened in one
    transaction share ``opened_at``, so don't rely on the order between them)."""
    key = subject_key(case_type.value, (m.user_id for m in people))
    rows = await session.scalars(
        select(FlagCase)
        .where(FlagCase.type == case_type, FlagCase.subject_key == key)
        .order_by(FlagCase.opened_at, FlagCase.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def case_deeds(session: AsyncSession, case_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await session.scalars(
        select(FlagCaseDeed.deed_id).where(FlagCaseDeed.case_id == case_id)
    )
    return set(rows)


async def cases_touching(session: AsyncSession, deed_ids: Iterable[uuid.UUID]) -> list[FlagCase]:
    rows = await session.scalars(
        select(FlagCase)
        .join(FlagCaseDeed, FlagCaseDeed.case_id == FlagCase.id)
        .where(FlagCaseDeed.deed_id.in_(set(deed_ids)))
        .distinct()
    )
    return list(rows)


async def dismiss(session: AsyncSession, case: FlagCase, admin: Member) -> None:
    """Resolve a case as dismissed (what the B41 decision endpoint will do)."""
    case.status = FlagCaseStatus.RESOLVED
    case.outcome = FlagCaseOutcome.DISMISSED
    case.resolved_at = datetime.now(UTC)
    case.resolved_by = admin.user_id
    await session.flush()


async def park(session: AsyncSession, case: FlagCase) -> None:
    case.status = FlagCaseStatus.PARKED
    await session.flush()
