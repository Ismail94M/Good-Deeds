"""Making decisions final (docs/M5-DESIGN.md D89, D100, §3.3 "Appeal decision", PO Q78).

``finalize_decisions`` (job ``fraud.finalize_decisions``, hourly):

1. Every notice past ``appeal_until`` with no appeal becomes final (``final_at``). A notice with
   an open appeal waits for its decision, however long that takes (Q78: with no eligible admin,
   permanent steps stay paused).
2. A deed marked ``reject`` in a confirmed case is ready once the notice of **every** case
   subject who is a party to it (reporter or doer) is final and none of them was overturned. An
   overturned party cancels the rejection for good. For each case with ready deeds,
   ``FlagDecisionFinal(case_id, rejected_deed_ids, actor_id = case decider)`` is emitted, and
   ``deeds`` rejects them (D89).

Idempotent without a marker table: a deed is emitted once because the ``FlagDecisionFinal``
outbox rows already written for the case are read back (outbox rows are never deleted), and
the whole run takes a transaction-level advisory lock so two runs can't both emit.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent, emit
from app.modules.deeds import service as deeds
from app.modules.fraud.events import FlagDecisionFinal
from app.modules.fraud.models import (
    Appeal,
    AppealOutcome,
    DecisionNotice,
    DeedOutcome,
    FlagCase,
    FlagCaseDeedOutcome,
    FlagCaseOutcome,
    FlagCaseStatus,
    FlagCaseUser,
    NoticeKind,
)

_LOCK_KEY = "fraud.finalize_decisions"


@dataclass(frozen=True)
class FinalizeResult:
    notices_final: int
    # case id -> deeds emitted for rejection in this run.
    rejected: dict[uuid.UUID, list[uuid.UUID]]


async def _emitted(
    session: AsyncSession, case_ids: set[uuid.UUID]
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """(case, deed) pairs already sent to ``deeds`` for rejection."""
    rows = await session.scalars(
        select(OutboxEvent.payload).where(
            OutboxEvent.event_type == FlagDecisionFinal.event_type,
            OutboxEvent.payload["case_id"].astext.in_([str(c) for c in case_ids]),
        )
    )
    out: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for payload in rows:
        case_id = uuid.UUID(str(payload["case_id"]))
        out |= {(case_id, uuid.UUID(str(d))) for d in payload.get("rejected_deed_ids", [])}
    return out


async def finalize_decisions(session: AsyncSession, now: datetime) -> FinalizeResult:
    """One run. The caller commits."""
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(_LOCK_KEY))))

    no_appeal = ~exists().where(Appeal.notice_id == DecisionNotice.id)
    made_final = await session.scalars(
        update(DecisionNotice)
        .where(
            DecisionNotice.final_at.is_(None),
            DecisionNotice.appeal_until <= now,
            no_appeal,
        )
        .values(final_at=now)
        .returning(DecisionNotice.id)
        .execution_options(synchronize_session=False)
    )
    notices_final = len(list(made_final))

    # Deeds marked reject in confirmed cases.
    rows = list(
        await session.execute(
            select(FlagCaseDeedOutcome.case_id, FlagCaseDeedOutcome.deed_id, FlagCase.resolved_by)
            .join(FlagCase, FlagCase.id == FlagCaseDeedOutcome.case_id)
            .where(
                FlagCaseDeedOutcome.outcome == DeedOutcome.REJECT,
                FlagCase.status == FlagCaseStatus.RESOLVED,
                FlagCase.outcome == FlagCaseOutcome.CONFIRMED,
            )
        )
    )
    if not rows:
        return FinalizeResult(notices_final=notices_final, rejected={})
    case_ids = {case_id for case_id, _, _ in rows}
    done = await _emitted(session, case_ids)
    pending = [(c, d, actor) for c, d, actor in rows if (c, d) not in done]
    if not pending:
        return FinalizeResult(notices_final=notices_final, rejected={})

    pending_cases = {c for c, _, _ in pending}
    subjects: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for case_id, user_id in await session.execute(
        select(FlagCaseUser.case_id, FlagCaseUser.user_id).where(
            FlagCaseUser.case_id.in_(pending_cases)
        )
    ):
        subjects[case_id].add(user_id)
    # (case, user) -> (final, overturned)
    state: dict[tuple[uuid.UUID, uuid.UUID], tuple[bool, bool]] = {}
    for notice_case, user_id, final_at, outcome in await session.execute(
        select(
            DecisionNotice.case_id,
            DecisionNotice.user_id,
            DecisionNotice.final_at,
            Appeal.outcome,
        )
        .outerjoin(Appeal, Appeal.notice_id == DecisionNotice.id)
        .where(
            DecisionNotice.kind == NoticeKind.CASE,
            DecisionNotice.case_id.in_(pending_cases),
        )
    ):
        if notice_case is not None:
            state[(notice_case, user_id)] = (
                final_at is not None,
                outcome == AppealOutcome.OVERTURNED,
            )

    views = await deeds.get_deeds(session, {d for _, d, _ in pending})
    ready: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    actors: dict[uuid.UUID, uuid.UUID | None] = {}
    for case_id, deed_id, actor in pending:
        view = views.get(deed_id)
        if view is None:
            continue
        parties = {p for p in (view.reporter_id, view.doer_id) if p is not None}
        parties &= subjects[case_id]
        if not parties:
            continue
        states = [state.get((case_id, p)) for p in parties]
        if all(s is not None and s[0] and not s[1] for s in states):
            ready[case_id].append(deed_id)
            actors[case_id] = actor
    for case_id in sorted(ready):
        emit(
            session,
            FlagDecisionFinal(
                case_id=case_id,
                rejected_deed_ids=sorted(ready[case_id]),
                actor_id=actors.get(case_id),
            ),
        )
    await session.flush()
    return FinalizeResult(
        notices_final=notices_final,
        rejected={c: sorted(ds) for c, ds in ready.items()},
    )
