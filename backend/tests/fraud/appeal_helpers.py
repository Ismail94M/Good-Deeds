"""Helpers for the notices, appeals and member Reviews tests (docs/M5-DESIGN.md D99 to D101):
confirming a case through the service, appeals through the API, finalize runs and the worker's
handlers for the deed events that follow a rejection."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent, emit, handlers_for, run_handler
from app.modules.fraud import _review
from app.modules.fraud._finalize import FinalizeResult, finalize_decisions
from app.modules.fraud._limits import load_fraud
from app.modules.fraud.models import Appeal, DecisionNotice, FlagCaseType
from app.modules.fraud.schemas import DecisionIn, DeedAction
from app.modules.scoring.service import PointsOverridden
from tests.deeds.helpers import OUTBOX_WRITE_ORDER, Member, members
from tests.fraud.helpers import DAY, days_ago, deed
from tests.fraud.review_helpers import NOTE, Pair, ask, open_case

type Action = Literal["exclude", "reject", "release"]

REASON = "We are neighbours and really did help each other that week."
FRAUD_PREFIX = "app.modules.fraud."
FINAL_EVENT = "fraud.flag_decision_final"
APPEAL_EVENT = "fraud.appeal_decided"
HOLD_EVENT = "fraud.deed_hold_changed"


async def confirm(
    session: AsyncSession,
    who: Member,
    case_id: uuid.UUID,
    actions: dict[uuid.UUID, Action],
    *,
    at: datetime | None = None,
) -> None:
    """Confirm through the service at ``at`` (default now). Every subject is asked 20 days
    before, so the reply is past due and D99 is met without a reply."""
    at = at or datetime.now(UTC)
    await ask(session, who, case_id, now=at - 20 * DAY)
    _, config = await load_fraud(session)
    await _review.decide(
        session,
        who.user_id,
        case_id,
        DecisionIn(
            outcome="confirmed",
            note=NOTE,
            deeds=[DeedAction(deed_id=d, action=a) for d, a in actions.items()],
        ),
        config=config,
        now=at,
    )
    await session.commit()


async def pair(session: AsyncSession, first_days_ago: float = 5) -> Pair:
    """A reciprocal case for a <-> b (a_to_b first)."""
    a, b = await members(session, 2)
    t0 = days_ago(first_days_ago)
    ab = await deed(session, a, b, t0)
    ba = await deed(session, b, a, t0 + DAY)
    case_id = await open_case(session, FlagCaseType.RECIPROCAL, [a, b], [ab, ba])
    return Pair(a, b, ab, ba, case_id)


async def notice_of(session: AsyncSession, case_id: uuid.UUID, person: Member) -> DecisionNotice:
    row = await session.scalar(
        select(DecisionNotice)
        .where(DecisionNotice.case_id == case_id, DecisionNotice.user_id == person.user_id)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def appeal_of(session: AsyncSession, notice_id: uuid.UUID) -> Appeal:
    row = await session.scalar(
        select(Appeal)
        .where(Appeal.notice_id == notice_id)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def appeal(
    client: AsyncClient, person: Member, notice_id: uuid.UUID, reason: str = REASON
) -> Response:
    return await client.post(
        f"/api/reviews/notices/{notice_id}/appeal", json={"reason": reason}, headers=person.headers
    )


async def decide_appeal(
    client: AsyncClient, who: Member, appeal_id: uuid.UUID, outcome: str, note: str = NOTE
) -> Response:
    return await client.post(
        f"/api/admin/fraud/appeals/{appeal_id}/decision",
        json={"outcome": outcome, "note": note},
        headers=who.headers,
    )


async def finalize(session: AsyncSession, at: datetime | None = None) -> FinalizeResult:
    result = await finalize_decisions(session, at or datetime.now(UTC))
    await session.commit()
    return result


async def final_events(session: AsyncSession, case_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(OutboxEvent.payload)
        .where(
            OutboxEvent.event_type == FINAL_EVENT,
            OutboxEvent.payload["case_id"].astext == str(case_id),
        )
        .order_by(*OUTBOX_WRITE_ORDER)
    )
    return list(rows)


async def drain(
    session: AsyncSession, deed_ids: Sequence[uuid.UUID], case_ids: Sequence[uuid.UUID] = ()
) -> int:
    """Run every non-fraud handler (deeds, feed, scoring) for the outbox events about these
    deeds and cases, until nothing is left, as the worker would. Fraud's own detectors are left
    out so they don't open new cases on the same deeds."""
    deed_texts = [str(d) for d in deed_ids]
    case_texts = [str(c) for c in case_ids]
    total = 0
    while True:
        rows = list(
            await session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.payload["deed_id"].astext.in_(deed_texts)
                    | OutboxEvent.payload["case_id"].astext.in_(case_texts)
                )
                .order_by(*OUTBOX_WRITE_ORDER)
            )
        )
        ran = 0
        for row in rows:
            for name in handlers_for(row.event_type):
                if name.startswith(FRAUD_PREFIX):
                    continue
                if await run_handler(session, row.id, name):
                    ran += 1
        await session.commit()
        total += ran
        if ran == 0:
            return total


async def points_overridden(
    session: AsyncSession,
    *,
    deed_id: uuid.UUID,
    user: Member,
    actor: Member,
    old_points: int = 12,
    new_points: int = 8,
    reason_code: str = "fake_photo",
    override_id: uuid.UUID | None = None,
    requested_by: Member | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Emit a synthetic ``PointsOverridden`` (as scoring would) and run fraud's handler for it.
    Returns ``(override_id, outbox event id)``."""
    override_id = override_id or uuid.uuid4()
    row = emit(
        session,
        PointsOverridden(
            override_id=override_id,
            deed_id=deed_id,
            role="doer",
            user_id=user.user_id,
            old_points=old_points,
            new_points=new_points,
            reason_code=reason_code,
            actor_id=actor.user_id,
            requested_by_id=requested_by.user_id if requested_by else None,
        ),
    )
    await session.flush()
    for name in handlers_for(row.event_type):
        if name.startswith(FRAUD_PREFIX):
            await run_handler(session, row.id, name)
    await session.commit()
    return override_id, row.id
