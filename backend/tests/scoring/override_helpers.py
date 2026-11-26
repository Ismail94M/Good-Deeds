"""Helpers for the override and revocation tests (docs/M5-DESIGN.md D90 to D92, Q77)."""

import uuid
from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.events import OutboxEvent, handlers_for, run_handler
from app.modules.scoring.models import ScoreOverride
from tests.deeds.helpers import Member, members, scan_and_report
from tests.scoring.helpers import run_scoring

NOTE = "The photo shows a different street."
SCORING_PREFIX = "app.modules.scoring."


async def scored_deed(session: AsyncSession) -> tuple[Member, Member, uuid.UUID]:
    """A verified QR deed scored by the rules: doer 10, reporter 2. Committed."""
    reporter, doer = await members(session, 2)
    out = await scan_and_report(session, reporter, doer)
    assert out.status == "verified"
    await run_scoring(session, out.id)
    await session.commit()
    return reporter, doer, out.id


async def override(client: AsyncClient, admin: Member, deed_id: uuid.UUID, **body: Any) -> Response:
    payload: dict[str, Any] = {"role": "doer", "reason_code": "fake_photo", "note": NOTE}
    payload.update(body)
    return await client.post(
        f"/api/admin/scoring/deeds/{deed_id}/overrides", json=payload, headers=admin.headers
    )


async def decide(
    client: AsyncClient, admin: Member, request_id: str, decision: str, note: str = NOTE
) -> Response:
    return await client.post(
        f"/api/admin/scoring/override-requests/{request_id}/decision",
        json={"decision": decision, "note": note},
        headers=admin.headers,
    )


async def events(session: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.occurred_at, OutboxEvent.id)
    )
    return [r.payload for r in rows]


async def overridden_events(session: AsyncSession, deed_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        p
        for p in await events(session, "scoring.points_overridden")
        if p["deed_id"] == str(deed_id)
    ]


async def audits(session: AsyncSession, entity_type: str, entity_id: str) -> list[AuditLog]:
    rows = await session.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.created_at, AuditLog.id)
    )
    return list(rows)


async def overrides(session: AsyncSession, deed_id: uuid.UUID) -> list[ScoreOverride]:
    rows = await session.scalars(
        select(ScoreOverride)
        .where(ScoreOverride.deed_id == deed_id)
        .order_by(ScoreOverride.created_at, ScoreOverride.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def run_scoring_for(session: AsyncSession, event_type: str) -> int:
    """Run the scoring handlers not yet run for every outbox event of ``event_type`` (events
    without a ``deed_id``, such as ``fraud.appeal_decided``)."""
    rows = await session.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.occurred_at, OutboxEvent.id)
    )
    ran = 0
    for row in list(rows):
        for name in handlers_for(row.event_type):
            if name.startswith(SCORING_PREFIX) and await run_handler(session, row.id, name):
                ran += 1
    return ran
