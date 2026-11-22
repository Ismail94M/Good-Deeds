"""Scoring API shapes (docs/M4-DESIGN.md §3.2, §3.3, §3.4).

Public shapes (``PublicScoring``, ``LeaderboardRow``, ``Leaderboard``, ``Heroes``) never contain
points or rank scores (D69, PO Q55); points appear only in the owner's shapes (``MyScoring``,
``LedgerEntry``, ``DeedPoints``) and in the admin-only shapes. Months are ``YYYY-MM``.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

RegionKindName = Literal["country", "state", "city", "ward"]


class UserSummary(BaseModel):
    """The public name of a user (same shape as identity's ``UserSummary``). On final boards and
    title lists an account that is no longer active shows as ``handle=None`` and
    ``display_name="Unavailable account"`` (SEC3 L6): its place and title stay, its name goes."""

    handle: str | None
    display_name: str


class TierOut(BaseModel):
    slug: str
    label: str


class TierOption(TierOut):
    min_points: int


class BadgeOut(BaseModel):
    slug: str
    label: str


class BadgeOption(BadgeOut):
    description: str


class LevelOption(BaseModel):
    """A level of the hierarchy (§3.4 #11): title minimums and the places that get a title."""

    kind: RegionKindName
    enabled: bool
    hero_min_deeds: int
    hero_min_reporters: int
    titled_places: list[int]


class ScoringOptions(BaseModel):
    """#1 (§3.4 #1). Every value comes from config."""

    tiers: list[TierOption]
    badges: list[BadgeOption]
    levels: list[LevelOption]
    withdraw_window_hours: int
    doer_daily_cap: int
    reporter_daily_cap: int
    reason_labels: dict[str, str]


class TitleOut(BaseModel):
    label: str
    period_month: str
    region_name: str
    place: int


class PublicScoring(BaseModel):
    """#4: public (Q55). No points, no ranks."""

    tier: TierOut
    badges_total: int
    titles_total: int
    badges: list[BadgeOut]
    titles: list[TitleOut]


class RegionRef(BaseModel):
    id: int
    code: str
    name: str
    kind: RegionKindName


class MyBoard(BaseModel):
    region: RegionRef
    period_month: str
    rank: int | None
    deed_count: int
    final: bool


class ReporterToday(BaseModel):
    used: int
    cap: int


class MyScoring(PublicScoring):
    """#2: the owner only."""

    doer_points: int
    reporter_points: int
    total_points: int
    provisional_points: int
    boards: list[MyBoard]
    reporter_today: ReporterToday


class LedgerEntryOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    deed_id: uuid.UUID
    role: Literal["doer", "reporter"]
    kind: Literal["award", "decision", "reversal"]
    points: int
    reason: str
    reason_label: str
    provisional: bool
    provisional_until: datetime | None
    category_label: str
    doer: UserSummary | None
    reporter: UserSummary | None


class LedgerPage(BaseModel):
    items: list[LedgerEntryOut]
    next_cursor: str | None


class DeedPoints(BaseModel):
    """#5: the caller's own row only."""

    points: int
    reason: str
    reason_label: str
    capped: bool
    provisional_until: datetime | None


class BoardRegion(BaseModel):
    code: str
    name: str
    kind: RegionKindName


class LeaderboardRow(BaseModel):
    """Rank, name, tier and verified-deed count; never points or the rank score (D69)."""

    rank: int
    shared: bool
    user: UserSummary
    tier: TierOut
    deed_count: int


class BoardTitle(BaseModel):
    place: int
    label: str
    users: list[UserSummary]


class Leaderboard(BaseModel):
    """#6. ``computed_at`` is null while a board has no snapshot yet (an empty board)."""

    region: BoardRegion
    month: str
    computed_at: datetime | None
    final: bool
    # The month's earliest finalization time (§3.4 #10); null once the month is final.
    finalizes_at: datetime | None
    items: list[LeaderboardRow]
    me: LeaderboardRow | None
    titles: list[BoardTitle]
    next_cursor: str | None


class RegionOption(BaseModel):
    code: str
    name: str


class HeroMonth(BaseModel):
    period_month: str
    titles: list[BoardTitle]


class Heroes(BaseModel):
    """#8 (§3.4 #2): final months only, newest first."""

    region: BoardRegion
    items: list[HeroMonth]


class AdminLedgerEntry(LedgerEntryOut):
    breakdown: dict[str, Any]
    config_version: int
    reverses_entry_id: uuid.UUID | None


class AdminEvaluation(BaseModel):
    id: uuid.UUID
    method: str
    mode: str
    status: str
    created_at: datetime
    config_version: int
    rule_points: int | None
    text_check: str | None
    impact_score: int | None
    raw_score: int | None
    sub_scores: dict[str, Any] | None
    evidence_level: str
    evidence_cap_applied: bool
    impact_explanation: str | None
    estimated_beneficiaries: int | None
    evaluation_confidence: float | None
    human_review_required: bool
    model_version: str | None
    prompt_version: str | None


class AdminDeedScoring(BaseModel):
    """#9 (§3.4 #3)."""

    deed_id: uuid.UUID
    ledger: list[AdminLedgerEntry]
    evaluations: list[AdminEvaluation]


class AdminMonth(BaseModel):
    """#10 (§3.4 #3)."""

    period_month: str
    cutoff_at: datetime
    pending_scoring_events: int
    finalized_at: datetime | None
    final_snapshots: int
