"""Scoring API shapes (docs/M4-DESIGN.md §3.2, §3.3, §3.4).

Public shapes (``PublicScoring``, ``LeaderboardRow``, ``Leaderboard``, ``Heroes``) never contain
points or rank scores (D69, PO Q55); points appear only in the owner's shapes (``MyScoring``,
``LedgerEntry``, ``DeedPoints``) and in the admin-only shapes. Months are ``YYYY-MM``.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

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
    # Which level's board the title is from (D75): lets the profile style City and Area titles
    # differently without reading the wording. Added 2026-11-24; existing fields unchanged.
    level: RegionKindName


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


class FeaturedCityRef(BaseModel):
    key: str
    name: str


class FeaturedRow(BaseModel):
    """A featured city's top performer (D72). ``points`` (net doer points counted for rank this
    month) is shown only when the city's config says so; otherwise null."""

    rank: int
    shared: bool
    user: UserSummary
    tier: TierOut
    deed_count: int
    points: int | None


class FeaturedCityBoard(BaseModel):
    """#11 ``GET /leaderboards/featured``: the top performers of a featured city this month.
    ``cities`` lists every enabled featured city (the first is the default) so a city switcher
    can be added without an API change."""

    city: FeaturedCityRef
    cities: list[FeaturedCityRef]
    month: str
    final: bool
    items: list[FeaturedRow]


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


class UserRank(BaseModel):
    """One level of a person's ranking this month (D76). ``enabled`` false means the level is not
    open yet ("Coming soon"); ``rank`` null means they are on no board of that level this month.
    Read from the same snapshots as the boards, so it always matches the leaderboard."""

    level: RegionKindName
    enabled: bool
    region: BoardRegion | None
    rank: int | None
    shared: bool


class UserRanks(BaseModel):
    """#12 (D76). ``levels`` in order Area (ward), City, State, Country."""

    month: str
    final: bool
    levels: list[UserRank]


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


class OverrideOut(BaseModel):
    """One ``score_overrides`` row (docs/M5-DESIGN.md §3.4 #13). ``points`` null: cleared."""

    id: uuid.UUID
    role: Literal["doer", "reporter"]
    points: int | None
    previous_points: int
    reason_code: str
    note: str
    actor: UserSummary | None
    case_id: uuid.UUID | None
    request_id: uuid.UUID | None
    created_at: datetime


class AdminDeedScoring(BaseModel):
    """#9 (§3.4 #3). M5: ``held`` (a prize hold from a confirmed decision, D79) and the deed's
    overrides, oldest first (D91). ``period_month`` (``YYYY-MM``) is the IST month the deed's
    ledger rows belong to (null with no rows); ``month_final`` is true once that month is
    finalized, so a change shows in points but not on the final leaderboard."""

    deed_id: uuid.UUID
    held: bool
    period_month: str | None
    month_final: bool
    ledger: list[AdminLedgerEntry]
    evaluations: list[AdminEvaluation]
    overrides: list[OverrideOut]


class AdminMonth(BaseModel):
    """#10 (§3.4 #3). The pending counts are what finalization waits for (M4 D64, M5 D93):
    events before the cutoff not yet handled by scoring, the same for the fraud detectors, and
    prize-hold changes since the month's start not yet scored."""

    period_month: str
    cutoff_at: datetime
    pending_scoring_events: int
    pending_fraud_events: int
    pending_hold_events: int
    finalized_at: datetime | None
    final_snapshots: int


# --------------------------------------------------- M5 overrides and revocation (§3.4 #13-#18)


class OverrideIn(BaseModel):
    """#13. ``points`` null clears the override in force. Lengths and ranges come from the
    ``fraud`` config and are checked by the service (422 with a code)."""

    role: Literal["doer", "reporter"]
    points: int | None
    reason_code: str = Field(max_length=30)
    note: str = Field(max_length=10_000)
    case_id: uuid.UUID | None = None


class OverrideRequestOut(BaseModel):
    """#13 (202) and #17: a big cut waiting for a second admin (Q77)."""

    id: uuid.UUID
    deed_id: uuid.UUID
    role: Literal["doer", "reporter"]
    user: UserSummary | None
    previous_points: int
    points: int
    reason_code: str
    note: str
    case_id: uuid.UUID | None
    requested_by: UserSummary | None
    created_at: datetime
    expires_at: datetime
    status: Literal["pending", "approved", "declined", "expired"]
    decided_by: UserSummary | None
    decision_note: str | None
    decided_at: datetime | None
    override_id: uuid.UUID | None


class OverrideRequestCounts(BaseModel):
    pending: int
    decided: int


class OverrideRequestPage(BaseModel):
    items: list[OverrideRequestOut]
    counts: OverrideRequestCounts
    next_cursor: str | None


class OverrideDecisionIn(BaseModel):
    decision: Literal["approve", "decline"]
    note: str = Field(max_length=10_000)


class OverrideDecisionOut(BaseModel):
    """#18: the request, plus the #9 body of the deed when approved."""

    request: OverrideRequestOut
    deed: AdminDeedScoring | None


class AdminTitle(BaseModel):
    id: uuid.UUID
    label: str
    period_month: str
    region_name: str
    level: RegionKindName
    place: int
    revoked_at: datetime | None
    revoked_reason: str | None


class AdminBadge(BaseModel):
    id: uuid.UUID
    slug: str
    label: str
    awarded_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None


class AdminUserScoring(BaseModel):
    """#14: a person's titles and badges (revoked ones included) and points, for revocation."""

    user: UserSummary
    titles: list[AdminTitle]
    badges: list[AdminBadge]
    doer_points: int
    reporter_points: int


class RevokeIn(BaseModel):
    """#15, #16."""

    case_id: uuid.UUID
    note: str = Field(max_length=10_000)
