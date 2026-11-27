"""One reminder per explanation request (docs/M6-DESIGN.md D112, PO Q91; migration 0033).

``fraud.remind_explanations`` (daily) finds requests with no reply whose reply is due within
``explanation.reminder_days_before_due`` days (and not yet past due) on a case that is still
open, writes the insert-only ``explanation_reminders`` row and emits a member-safe
``explanation_reminder`` update in the same transaction. The row's primary key makes the
reminder fire once, even when two runs overlap.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.fraud._limits import FraudConfig
from app.modules.fraud._updates import review_update
from app.modules.fraud.models import (
    ExplanationReminder,
    ExplanationRequest,
    FlagCase,
    FlagCaseStatus,
)


async def remind_explanations(session: AsyncSession, *, config: FraudConfig, now: datetime) -> int:
    """Send the due reminders. Returns how many were sent. The caller commits."""
    horizon = now + timedelta(days=config.explanation.reminder_days_before_due)
    due = await session.execute(
        select(ExplanationRequest.id, ExplanationRequest.user_id, ExplanationRequest.reply_due_at)
        .join(FlagCase, FlagCase.id == ExplanationRequest.case_id)
        .outerjoin(ExplanationReminder, ExplanationReminder.request_id == ExplanationRequest.id)
        .where(
            ExplanationRequest.replied_at.is_(None),
            ExplanationRequest.reply_due_at > now,
            ExplanationRequest.reply_due_at <= horizon,
            FlagCase.status != FlagCaseStatus.RESOLVED,
            ExplanationReminder.request_id.is_(None),
        )
        .order_by(ExplanationRequest.reply_due_at, ExplanationRequest.id)
    )
    sent = 0
    for request_id, user_id, reply_due_at in due.all():
        claimed = await session.scalar(
            insert(ExplanationReminder)
            .values(request_id=request_id, sent_at=now)
            .on_conflict_do_nothing(index_elements=[ExplanationReminder.request_id])
            .returning(ExplanationReminder.request_id)
        )
        if claimed is None:
            continue
        review_update(
            session, user_id, "explanation_reminder", request_id, reply_due_at=reply_due_at
        )
        sent += 1
    await session.flush()
    return sent
