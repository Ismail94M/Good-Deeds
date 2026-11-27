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

Idempotent: each deed emitted is recorded in ``flag_rejections_sent`` in the same transaction,
and only ``reject`` outcomes without such a row are read (SEC5-L6: nothing depends on outbox
rows being kept). The whole run takes a transaction-level advisory lock so two runs can't both
emit.

Appeals at the window edge (SEC5-L1): the notices to make final are locked ``FOR UPDATE`` first
(the member's appeal locks the same row), then re-read in a new statement, which sees an appeal
committed meanwhile; a notice with any appeal is never made final here. Readiness also treats a
notice with an undecided appeal as not final.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit
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
    FlagRejectionSent,
    NoticeKind,
)

_LOCK_KEY = "fraud.finalize_decisions"


@dataclass(frozen=True)
class FinalizeResult:
    notices_final: int
    # case id -> deeds emitted for rejection in this run.
    rejected: dict[uuid.UUID, list[uuid.UUID]]


async def finalize_decisions(session: AsyncSession, now: datetime) -> FinalizeResult:
    """One run. The caller commits."""
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(_LOCK_KEY))))

    no_appeal = ~exists().where(Appeal.notice_id == DecisionNotice.id)
    # Lock the candidates first (an appeal being sent holds the same row lock), then decide in
    # a new statement, whose snapshot sees any appeal committed while we waited.
    due = list(
        await session.scalars(
            select(DecisionNotice.id)
            .where(DecisionNotice.final_at.is_(None), DecisionNotice.appeal_until <= now)
            .order_by(DecisionNotice.id)
            .with_for_update()
        )
    )
    notices_final = 0
    if due:
        made_final = await session.scalars(
            update(DecisionNotice)
            .where(
                DecisionNotice.id.in_(due),
                DecisionNotice.final_at.is_(None),
                no_appeal,
            )
            .values(final_at=now)
            .returning(DecisionNotice.id)
            .execution_options(synchronize_session=False)
        )
        notices_final = len(list(made_final))

    # Deeds marked reject in confirmed cases, not sent yet.
    sent = exists().where(
        FlagRejectionSent.case_id == FlagCaseDeedOutcome.case_id,
        FlagRejectionSent.deed_id == FlagCaseDeedOutcome.deed_id,
    )
    pending = list(
        await session.execute(
            select(FlagCaseDeedOutcome.case_id, FlagCaseDeedOutcome.deed_id, FlagCase.resolved_by)
            .join(FlagCase, FlagCase.id == FlagCaseDeedOutcome.case_id)
            .where(
                FlagCaseDeedOutcome.outcome == DeedOutcome.REJECT,
                FlagCase.status == FlagCaseStatus.RESOLVED,
                FlagCase.outcome == FlagCaseOutcome.CONFIRMED,
                ~sent,
            )
        )
    )
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
    for notice_case, user_id, final_at, appeal_id, outcome in await session.execute(
        select(
            DecisionNotice.case_id,
            DecisionNotice.user_id,
            DecisionNotice.final_at,
            Appeal.id,
            Appeal.outcome,
        )
        .outerjoin(Appeal, Appeal.notice_id == DecisionNotice.id)
        .where(
            DecisionNotice.kind == NoticeKind.CASE,
            DecisionNotice.case_id.in_(pending_cases),
        )
    ):
        if notice_case is not None:
            appeal_open = appeal_id is not None and outcome is None
            state[(notice_case, user_id)] = (
                final_at is not None and not appeal_open,
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
        await session.execute(
            insert(FlagRejectionSent)
            .values(
                [{"case_id": case_id, "deed_id": d, "sent_at": now} for d in sorted(ready[case_id])]
            )
            .on_conflict_do_nothing()
        )
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
