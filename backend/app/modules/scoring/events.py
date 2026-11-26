"""Domain events emitted by the scoring module (docs/M4-DESIGN.md §5).

Payloads are IDs, codes and numbers only (no names, text or locations). Nothing subscribed in M4;
M6 (notifications) will. From M5, ``fraud`` subscribes to ``ReporterConcentrationExceeded`` and
``DeedNeedsReview`` (docs/M5-DESIGN.md §5, D78), which it imports through ``scoring.service``.
"""

import uuid
from datetime import date
from typing import Literal

from app.core.events import DomainEvent


class PointsChanged(DomainEvent):
    """A user's points for one deed and role changed (an award, a reversal, or both, or a
    zero-point cap decision). ``delta`` is the net change; ``reason`` is the ledger reason.

    ``notify`` is false for a change nobody may be told about: only the deed's leaderboard
    eligibility flipped (``delta == 0``, reason ``prize_rank_changed``), which is what a prize
    hold or its release does. Flags are private (docs/M5-DESIGN.md D95), so M6 must never
    notify such an event."""

    event_type = "scoring.points_changed"
    user_id: uuid.UUID
    deed_id: uuid.UUID
    role: Literal["doer", "reporter"]
    delta: int
    reason: str
    # Added in M5 with a default, so events written before it still load.
    notify: bool = True


class ReporterConcentrationExceeded(DomainEvent):
    """One reporter's share of a doer's prize-rank points in an open month went over
    ``fraud.concentration.max_reporter_share_pct`` (docs/M5-DESIGN.md D85, §4.4). Emitted once
    per (doer, reporter, month) by ``scoring.check_concentration``; ``fraud`` opens a
    ``concentration`` case. ``deed_ids`` are the pair's prize-rank deeds that month."""

    event_type = "scoring.reporter_concentration_exceeded"
    doer_id: uuid.UUID
    reporter_id: uuid.UUID
    period_month: date
    # The reporter's share in basis points, and the doer's prize-rank points, at detection.
    share_bp: int
    doer_points: int
    deed_ids: list[uuid.UUID]


class DeedNeedsReview(DomainEvent):
    """A rule-based evaluation flagged the deed for human review (docs/M5-DESIGN.md D86; text
    check ``spam_or_empty``, M4 D55). Emitted once per deed: only when no earlier evaluation of
    it was flagged. ``fraud`` opens a ``content`` case for the doer and the reporter: the
    reporter wrote the description, and a rejection would reverse their points too, so both
    must be able to explain and appeal (PO principle 3)."""

    event_type = "scoring.deed_needs_review"
    deed_id: uuid.UUID
    doer_id: uuid.UUID
    reporter_id: uuid.UUID
    reason: Literal["spam_or_empty"]


class BadgeAwarded(DomainEvent):
    event_type = "scoring.badge_awarded"
    user_id: uuid.UUID
    badge_slug: str


class HeroTitleAwarded(DomainEvent):
    event_type = "scoring.hero_title_awarded"
    user_id: uuid.UUID
    period_month: date
    region_id: int
    level_kind: Literal["country", "state", "city", "ward"]
    place: int
    title_key: str


class MonthFinalized(DomainEvent):
    event_type = "scoring.month_finalized"
    period_month: date
