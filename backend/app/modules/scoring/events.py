"""Domain events emitted by the scoring module (docs/M4-DESIGN.md §5).

Payloads are IDs, codes and numbers only (no names, text or locations). Nothing subscribes in M4;
M6 (notifications) will.
"""

import uuid
from datetime import date
from typing import Literal

from app.core.events import DomainEvent


class PointsChanged(DomainEvent):
    """A user's points for one deed and role changed (an award, a reversal, or both, or a
    zero-point cap decision). ``delta`` is the net change; ``reason`` is the ledger reason."""

    event_type = "scoring.points_changed"
    user_id: uuid.UUID
    deed_id: uuid.UUID
    role: Literal["doer", "reporter"]
    delta: int
    reason: str


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
