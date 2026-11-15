"""Domain events emitted by the deeds module (docs/M2-DESIGN.md §4).

Payload rules: no PII, no coordinates, no free text (descriptions). Outbox rows are long-lived,
so consumers fetch details through ``deeds.service``. Nothing subscribes in M2.

A QR deed emits ``DeedSubmitted`` and ``DeedVerified`` in the same transaction; dispatch order
between them is not guaranteed, so ``DeedSubmitted.status`` carries the current status.
"""

import uuid
from datetime import datetime
from typing import Literal

from app.core.events import DomainEvent

Method = Literal["qr_scan", "invite_accept"]


class DeedSubmitted(DomainEvent):
    """A report about someone else was created (QR or invite). Never for self-logs."""

    event_type = "deeds.deed_submitted"
    deed_id: uuid.UUID
    reporter_id: uuid.UUID
    doer_id: uuid.UUID | None
    method: Method
    status: Literal["pending", "verified"]
    category: str
    occurred_at: datetime


class DeedVerified(DomainEvent):
    """QR submit or invite accept."""

    event_type = "deeds.deed_verified"
    deed_id: uuid.UUID
    reporter_id: uuid.UUID
    doer_id: uuid.UUID
    method: Method
    verified_at: datetime
    same_act_group_id: uuid.UUID
    counts_for_points: bool
    prize_rank_eligible: bool
    limits_version: int


class DeedRejected(DomainEvent):
    """Invite expiry (``new_status="unverified"``, ``reason="invite_expired"``); later moderator
    or fraud rejections."""

    event_type = "deeds.deed_rejected"
    deed_id: uuid.UUID
    reporter_id: uuid.UUID
    doer_id: uuid.UUID | None
    new_status: Literal["unverified", "rejected"]
    reason: str


class DeedPrizeRankChanged(DomainEvent):
    """An invite deed gains prize-rank eligibility (second independent report, §3.4) or loses it
    (that report was withdrawn, §3.4b)."""

    event_type = "deeds.prize_rank_changed"
    deed_id: uuid.UUID
    doer_id: uuid.UUID
    prize_rank_eligible: bool
    reason: str


class DeedEdited(DomainEvent):
    """The reporter edited a deed. Field names only, never the text."""

    event_type = "deeds.deed_edited"
    deed_id: uuid.UUID
    revision_id: uuid.UUID
    changed_fields: list[str]


class DeedCountsForPointsChanged(DomainEvent):
    """An uncounted report was promoted after a counted report in its group was withdrawn
    (D13, §3.4b)."""

    event_type = "deeds.counts_for_points_changed"
    deed_id: uuid.UUID
    doer_id: uuid.UUID
    reporter_id: uuid.UUID
    same_act_group_id: uuid.UUID
    counts_for_points: bool
    reason: str


class DeedWithdrawn(DomainEvent):
    """The reporter withdrew a deed."""

    event_type = "deeds.deed_withdrawn"
    deed_id: uuid.UUID
    reporter_id: uuid.UUID
    doer_id: uuid.UUID | None
    previous_status: Literal["pending", "verified"]
    same_act_group_id: uuid.UUID | None
