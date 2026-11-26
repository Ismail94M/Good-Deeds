"""Deeds event handlers (docs/M5-DESIGN.md D89, §5).

``FlagDecisionFinal``: the fraud decision on these deeds is final for everyone on them (the
appeal window closed or the appeals were upheld, D100), so each is rejected through
``_reject.reject_for_fraud``. That skips a deed that is no longer verified (withdrawn, or
already rejected by a redelivered event), so the handler is idempotent. ``deeds`` imports only
this event from fraud (D78, ``tests/test_architecture.py``).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import subscribe
from app.modules.deeds._reject import reject_for_fraud
from app.modules.fraud.service import FlagDecisionFinal


@subscribe(FlagDecisionFinal)
async def reject_fraud_deeds(session: AsyncSession, event: FlagDecisionFinal) -> None:
    for deed_id in sorted(event.rejected_deed_ids):
        await reject_for_fraud(session, deed_id, event.case_id, event.actor_id)
