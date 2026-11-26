"""Decision notices for a confirmed case (docs/M5-DESIGN.md D100, D101, §3.3 "Decision
effects").

One ``decision_notices`` row per subject with an adverse item (``exclude`` or ``reject`` on a
deed they reported or did). Items hold ids and effect codes only: ``excluded`` (not counted for
leaderboards) and ``pending_removal`` (removed once the decision is final). The member text comes
from ``member_reason_labels[reason_key]`` when the notice is read (B45). Lowering-override
notices, appeals and ``finalize_decisions`` are B45.
"""

import uuid
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import service as deeds
from app.modules.fraud._limits import FraudConfig
from app.modules.fraud.models import DecisionNotice, DeedOutcome, NoticeKind

CASE_REASON_KEY = "case"
EFFECTS: dict[DeedOutcome, str] = {
    DeedOutcome.EXCLUDE: "excluded",
    DeedOutcome.REJECT: "pending_removal",
}


def adverse_people(
    actions: Mapping[uuid.UUID, DeedOutcome],
    views: Mapping[uuid.UUID, deeds.DeedView],
    subjects: set[uuid.UUID],
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Subject -> their deeds with an adverse outcome, oldest verification first. Only subjects
    get a notice (a notice and an explanation request are for a person in the case)."""
    out: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    ordered = sorted(
        actions,
        key=lambda d: (
            (v.verified_at.isoformat() if (v := views.get(d)) and v.verified_at else ""),
            str(d),
        ),
    )
    for deed_id in ordered:
        if actions[deed_id] == DeedOutcome.RELEASE:
            continue
        view = views.get(deed_id)
        if view is None:
            continue
        for person in {view.reporter_id, view.doer_id}:
            if person is not None and person in subjects:
                out[person].append(deed_id)
    return dict(out)


def create_case_notices(
    session: AsyncSession,
    case_id: uuid.UUID,
    actions: Mapping[uuid.UUID, DeedOutcome],
    people: Mapping[uuid.UUID, list[uuid.UUID]],
    *,
    config: FraudConfig,
    now: datetime,
) -> int:
    """Add one notice per person in ``people`` (from ``adverse_people``). Returns how many."""
    appeal_until = now + timedelta(days=config.appeal.window_days)
    for user_id in sorted(people):
        items: list[dict[str, Any]] = [
            {"deed_id": str(d), "effect": EFFECTS[actions[d]]} for d in people[user_id]
        ]
        session.add(
            DecisionNotice(
                user_id=user_id,
                kind=NoticeKind.CASE,
                case_id=case_id,
                items=items,
                reason_key=CASE_REASON_KEY,
                created_at=now,
                appeal_until=appeal_until,
            )
        )
    return len(people)
