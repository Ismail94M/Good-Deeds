"""Member-safe review updates (docs/M6-DESIGN.md D112, PO Q91; M5 D101).

``MemberReviewUpdate`` is emitted at exactly five points, in the transaction of the change:

- ``explanation_requested``: an admin asked the member to explain (``_explain``), with the due
  date;
- ``explanation_reminder``: ``fraud.remind_explanations``, once per request, while there is no
  reply (``_reminders``);
- ``decision_notice``: a confirmed case or a lowering score override gave the member a notice
  (``_notices``);
- ``appeal_decided``: their appeal was decided (``_appeals``), with the notice id;
- ``review_closed``: a case the member was asked about ended with nothing for them (dismissed,
  or confirmed with no adverse deed of theirs): "We've finished looking" (``_review``).

Nothing is emitted when a case opens, is parked or held, or is dismissed without anyone being
asked. The payload is the member, the kind and the member's own request or notice id only.
"""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit
from app.modules.fraud.events import MemberReviewUpdate, ReviewUpdateKind


def review_update(
    session: AsyncSession,
    user_id: uuid.UUID,
    kind: ReviewUpdateKind,
    item_id: uuid.UUID,
    *,
    reply_due_at: datetime | None = None,
) -> None:
    emit(
        session,
        MemberReviewUpdate(user_id=user_id, kind=kind, item_id=item_id, reply_due_at=reply_due_at),
    )
