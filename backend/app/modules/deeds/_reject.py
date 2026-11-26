"""Rejecting a verified deed after a confirmed fraud decision (docs/M5-DESIGN.md D89, §4.7).

Called only once the decision is final (D100: the appeal window closed, or the appeal was
upheld); until then ``fraud`` holds the deed instead. Only ``deeds`` writes deed status, so the
fraud module reaches this through ``deeds.service.reject_for_fraud`` (later from the
``FlagDecisionFinal`` subscriber in ``deeds/handlers.py``).

Lock order: ``same_act`` advisory lock (per doer) -> deed row, the same order as withdrawal, so
the two can't deadlock. Unlike a withdrawal there is **no promotion** of the next report in the
act: in a confirmed fraud case the act's other reports are suspect too, and a promotion after the
appeal window would move points in a closed period. The existing ``feed`` and ``scoring``
handlers for ``DeedRejected`` remove the post and reverse the points.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.events import emit
from app.modules.deeds import _grouping
from app.modules.deeds.events import DeedRejected
from app.modules.deeds.models import Deed, DeedStatus, VerificationEvent, VerificationEventKind

FRAUD_CONFIRMED_REASON = "fraud_confirmed"


async def reject_for_fraud(
    session: AsyncSession, deed_id: uuid.UUID, case_id: uuid.UUID, actor_id: uuid.UUID | None
) -> bool:
    """Reject a verified deed for a confirmed fraud case. Returns True if it was rejected now,
    False if skipped (unknown deed, or no longer ``verified``: already rejected, withdrawn, ...),
    so a redelivered event or a second call changes nothing. ``actor_id`` is the admin who
    decided the case. The caller commits."""
    # A verified deed's doer never changes, so the unlocked peek gives the right lock key.
    peek = await session.get(Deed, deed_id, populate_existing=True)
    if peek is None or peek.status != DeedStatus.VERIFIED or peek.doer_user_id is None:
        return False
    await _grouping.lock_doer(session, peek.doer_user_id)
    deed = (
        await session.scalars(
            select(Deed)
            .where(Deed.id == deed_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()
    # Re-checked under the locks: a withdrawal may have won the race.
    if deed.status != DeedStatus.VERIFIED:
        return False

    deed.status = DeedStatus.REJECTED
    await _grouping.leave_group(
        session,
        deed,
        promote=False,
        revoked_reason=_grouping.PRIZE_REVOKED_REJECTED_REASON,
    )
    session.add(
        VerificationEvent(
            deed_id=deed.id,
            kind=VerificationEventKind.REJECTED,
            actor_user_id=actor_id,
            evidence={"previous_status": DeedStatus.VERIFIED.value, "case_id": str(case_id)},
        )
    )
    record_audit(
        session,
        entity_type="deed",
        entity_id=str(deed.id),
        action="deed.rejected",
        actor_id=actor_id,
        old_value={"status": DeedStatus.VERIFIED.value},
        new_value={"case_id": str(case_id)},
        reason=FRAUD_CONFIRMED_REASON,
    )
    emit(
        session,
        DeedRejected(
            deed_id=deed.id,
            reporter_id=deed.reporter_user_id,
            doer_id=deed.doer_user_id,
            new_status="rejected",
            reason=FRAUD_CONFIRMED_REASON,
        ),
    )
    await session.flush()
    return True
