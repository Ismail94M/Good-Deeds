"""The deeds subscriber for ``FlagDecisionFinal`` (docs/M5-DESIGN.md D89): each deed is rejected
once, with the case decider as actor; a redelivered event or an already withdrawn deed changes
nothing."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit, handlers_for, run_handler
from app.modules.deeds.models import Deed, DeedStatus, VerificationEvent, VerificationEventKind
from app.modules.fraud.service import FlagDecisionFinal
from tests.deeds.helpers import count, members, outbox
from tests.fraud.helpers import days_ago, deed

HANDLER = "app.modules.deeds.handlers.reject_fraud_deeds"


async def _deliver(session: AsyncSession, event: FlagDecisionFinal) -> bool:
    row = emit(session, event)
    await session.flush()
    assert HANDLER in handlers_for(row.event_type)
    ran = await run_handler(session, row.id, HANDLER)
    await session.commit()
    return ran


async def test_final_decision_rejects_each_deed_once(session: AsyncSession) -> None:
    a, b, actor = await members(session, 3)
    first = await deed(session, a, b, days_ago(3))
    second = await deed(session, b, a, days_ago(2))
    case_id = uuid.uuid4()
    event = FlagDecisionFinal(
        case_id=case_id, rejected_deed_ids=[first, second], actor_id=actor.user_id
    )
    assert await _deliver(session, event)
    statuses = await session.scalars(
        select(Deed.status)
        .where(Deed.id.in_([first, second]))
        .execution_options(populate_existing=True)
    )
    assert set(statuses) == {DeedStatus.REJECTED}
    for deed_id in (first, second):
        (rejected,) = await outbox(session, "deeds.deed_rejected", deed_id)
        assert rejected.payload["reason"] == "fraud_confirmed"
        events = list(
            await session.scalars(
                select(VerificationEvent).where(
                    VerificationEvent.deed_id == deed_id,
                    VerificationEvent.kind == VerificationEventKind.REJECTED,
                )
            )
        )
        assert [e.actor_user_id for e in events] == [actor.user_id]
        assert events[0].evidence["case_id"] == str(case_id)

    # The same decision delivered again (a new outbox row) rejects nothing more.
    assert await _deliver(session, event)
    for deed_id in (first, second):
        assert len(await outbox(session, "deeds.deed_rejected", deed_id)) == 1
    assert (
        await count(
            session,
            VerificationEvent,
            VerificationEvent.deed_id.in_([first, second]),
            VerificationEvent.kind == VerificationEventKind.REJECTED,
        )
        == 2
    )


async def test_no_actor_and_unknown_deeds_are_fine(session: AsyncSession) -> None:
    a, b = await members(session, 2)
    deed_id = await deed(session, a, b, days_ago(1))
    event = FlagDecisionFinal(case_id=uuid.uuid4(), rejected_deed_ids=[uuid.uuid4(), deed_id])
    assert event.actor_id is None
    assert await _deliver(session, event)
    status = await session.scalar(
        select(Deed.status).where(Deed.id == deed_id).execution_options(populate_existing=True)
    )
    assert status == DeedStatus.REJECTED
