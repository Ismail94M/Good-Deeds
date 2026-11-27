"""Domain events emitted by the fraud module (docs/M5-DESIGN.md §5).

Payloads are ids, types and counts only: no names, text, coordinates or hashes (D95). Other
modules import them through ``fraud.service`` (D78).
"""

import uuid
from datetime import datetime
from typing import Literal

from app.core.events import DomainEvent

CaseOutcome = Literal["confirmed", "dismissed"]


class FlagRaised(DomainEvent):
    """A detector opened a new case (never for a deed joining an existing one). Nothing
    subscribes in M5 (M8 analytics)."""

    event_type = "fraud.flag_raised"
    case_id: uuid.UUID
    type: str
    user_ids: list[uuid.UUID]
    deed_ids: list[uuid.UUID]


class DeedHoldChanged(DomainEvent):
    """A deed became held (its first unreleased hold) or no longer held (its last hold was
    released). Scoring re-scores the deed (D79)."""

    event_type = "fraud.deed_hold_changed"
    deed_id: uuid.UUID
    held: bool
    case_id: uuid.UUID


class FlagCaseResolved(DomainEvent):
    """An admin confirmed or dismissed a case (D88). Nothing subscribes in M5 (M6, M8)."""

    event_type = "fraud.flag_case_resolved"
    case_id: uuid.UUID
    type: str
    outcome: CaseOutcome
    user_ids: list[uuid.UUID]
    pending_reject_deed_ids: list[uuid.UUID]
    excluded_deed_ids: list[uuid.UUID]
    released_deed_ids: list[uuid.UUID]
    actor_id: uuid.UUID


class FlagDecisionFinal(DomainEvent):
    """The decision on these deeds is final for everyone on them (D100): ``deeds`` rejects them
    (D89)."""

    event_type = "fraud.flag_decision_final"
    case_id: uuid.UUID
    rejected_deed_ids: list[uuid.UUID]
    # The admin who decided the case (the rejection's actor), if known.
    actor_id: uuid.UUID | None = None


class AppealDecided(DomainEvent):
    """A second admin decided an appeal (D100). On ``overturned`` for an override notice,
    scoring writes a clearing override (actor: ``reviewer_id``)."""

    event_type = "fraud.appeal_decided"
    appeal_id: uuid.UUID
    notice_kind: Literal["case", "override"]
    case_id: uuid.UUID | None
    override_id: uuid.UUID | None
    user_id: uuid.UUID
    outcome: Literal["upheld", "overturned"]
    reviewer_id: uuid.UUID


# --------------------------------------------------- member-safe review updates (M6A D112)

type ReviewUpdateKind = Literal[
    "explanation_requested",
    "explanation_reminder",
    "decision_notice",
    "appeal_decided",
    "review_closed",
]


class MemberReviewUpdate(DomainEvent):
    """Something changed for this member in *Profile -> Reviews* (docs/M6-DESIGN.md D112).

    Member-safe by construction (D101): only the member, what kind of update it is, and the id
    of the member's own explanation request or decision notice (``item_id``). Never a case id,
    a case type, a detector, another person or an admin. ``notifications`` turns it into a
    neutral row that links to Reviews; it may import no other fraud event."""

    event_type = "fraud.member_review_update"
    user_id: uuid.UUID
    kind: ReviewUpdateKind
    item_id: uuid.UUID
    reply_due_at: datetime | None = None


class ReviewsSeen(DomainEvent):
    """The member opened *Profile -> Reviews* (``POST /api/reviews/seen``): ``notifications``
    marks their review rows read up to ``seen_at`` (M6A D112). Member-safe like
    ``MemberReviewUpdate``."""

    event_type = "fraud.reviews_seen"
    user_id: uuid.UUID
    seen_at: datetime
