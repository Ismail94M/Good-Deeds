"""Explain before a decision (docs/M5-DESIGN.md D99, §3.3 #10).

Before an admin confirms a case with an adverse outcome (``exclude`` or ``reject``) for a person,
that person is asked about their deeds and may reply within ``explanation.reply_days``. Confirm
is refused with 409 ``explanation_required`` when the person was never asked, and 409
``explanation_pending`` while they haven't replied and the due date hasn't passed. Dismissing
never needs it. The member side (seeing the request, replying) is ``_member.py``.
"""

import uuid
from collections.abc import Collection
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict
from app.modules.fraud import _actions
from app.modules.fraud._common import Invalid, clean_note
from app.modules.fraud._limits import FraudConfig
from app.modules.fraud._updates import review_update
from app.modules.fraud.models import (
    ExplanationRequest,
    FlagCaseAction,
    FlagCaseStatus,
)
from app.modules.fraud.schemas import ExplanationsIn
from app.modules.identity import service as identity


def case_resolved() -> Conflict:
    return Conflict("This case has already been decided.", code="case_resolved")


async def requests_for(
    session: AsyncSession, case_id: uuid.UUID
) -> dict[uuid.UUID, ExplanationRequest]:
    rows = await session.scalars(
        select(ExplanationRequest)
        .where(ExplanationRequest.case_id == case_id)
        .execution_options(populate_existing=True)
    )
    return {r.user_id: r for r in rows}


async def require_explanations(
    session: AsyncSession, case_id: uuid.UUID, user_ids: Collection[uuid.UUID], now: datetime
) -> None:
    """D99 for every person with an adverse outcome on one of their deeds."""
    if not user_ids:
        return
    requests = await requests_for(session, case_id)
    if any(u not in requests for u in user_ids):
        raise Conflict(
            "Ask the people involved for an explanation first.", code="explanation_required"
        )
    waiting = [
        r.reply_due_at
        for u in user_ids
        if (r := requests[u]).replied_at is None and r.reply_due_at > now
    ]
    if waiting:
        until = max(waiting).date().isoformat()
        raise Conflict(f"Waiting for a reply until {until}.", code="explanation_pending")


def _handle(raw: str) -> str:
    return raw.strip().lstrip("@").lower()


async def request_explanations(
    session: AsyncSession,
    admin_id: uuid.UUID,
    case_id: uuid.UUID,
    data: ExplanationsIn,
    *,
    config: FraudConfig,
    now: datetime,
) -> list[uuid.UUID]:
    """Ask the chosen subjects (default: every subject not asked yet) for an explanation.
    Returns who was asked. The caller commits."""
    note = clean_note(data.note, config)
    case = await _actions.visible_case(session, admin_id, case_id, config, now, lock=True)
    if case.status == FlagCaseStatus.RESOLVED:
        raise case_resolved()
    subjects = await _actions.subject_ids(session, case.id)
    asked = set((await requests_for(session, case.id)).keys())

    if data.handles is None:
        chosen = subjects - asked
    else:
        people = await identity.public_summaries(session, subjects)
        by_handle = {p.handle.lower(): uid for uid, p in people.items()}
        wanted = {_handle(h) for h in data.handles}
        if not wanted or any(h not in by_handle for h in wanted):
            raise Invalid("Choose people who are part of this case.", code="not_a_subject")
        chosen = {by_handle[h] for h in wanted}
        if chosen & asked:
            raise Conflict(
                "Some of these people have already been asked.", code="already_requested"
            )
    if not chosen:
        raise Conflict("Everyone in this case has already been asked.", code="already_requested")

    due = now + timedelta(days=config.explanation.reply_days)
    ordered = sorted(chosen)
    for user_id in ordered:
        request = ExplanationRequest(
            id=uuid.uuid4(),
            case_id=case.id,
            user_id=user_id,
            requested_by=admin_id,
            requested_at=now,
            reply_due_at=due,
        )
        session.add(request)
        # M6A D112: the member hears about it in-app (neutral words, their own request only).
        review_update(session, user_id, "explanation_requested", request.id, reply_due_at=due)
    _actions.record(
        session,
        case,
        FlagCaseAction.REQUEST_EXPLANATION,
        actor_id=admin_id,
        note=note,
        now=now,
        new_value={"user_ids": [str(u) for u in ordered], "reply_due_at": due.isoformat()},
    )
    case.updated_at = now
    await session.flush()
    return ordered
