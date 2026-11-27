"""Request and response shapes of the fraud admin API (docs/M5-DESIGN.md §3.2, §3.3).

Admin-only. No coordinates, phone numbers, device or IP hashes anywhere (D95): shared devices and
networks are booleans per pair. Points are not here (``fraud`` can't read scoring, D78); the UI
asks ``GET /api/admin/scoring/deeds/{id}``.
"""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Request bodies are bounded here; the config bounds (``note_max_chars``) are checked in the
# service so they can change without a deploy.
_MAX_BODY_TEXT = 10_000
_MAX_DEEDS = 1_000


class UserRef(BaseModel):
    handle: str
    display_name: str
    # Opaque profile-picture reference (M6A D117, D124), never a URL; null for no picture or an
    # unavailable account (``_GONE``).
    avatar: str | None = None


# --------------------------------------------------- #1 options


class CaseTypeOption(BaseModel):
    id: str
    label: str
    enabled: bool
    holds_on_open: bool


class ReasonCodeOption(BaseModel):
    id: str
    label: str


class SecondAdminRule(BaseModel):
    """Q77: overrides that need a second admin's approval (to 0 when ``cut_to_zero``, or a cut
    of more than ``max_cut_points``); requests expire after ``expire_days``."""

    max_cut_points: int
    cut_to_zero: bool
    expire_days: int


class FraudOptions(BaseModel):
    types: list[CaseTypeOption]
    statuses: list[str]
    outcomes: list[str]
    deed_outcomes: list[str]
    note_min_chars: int
    note_max_chars: int
    override_reason_codes: list[ReasonCodeOption]
    override_max_points: int
    override_second_admin: SecondAdminRule
    explanation_reply_days: int
    appeal_window_days: int


# --------------------------------------------------- #2 queue, #3 detail


class CaseRow(BaseModel):
    case_id: uuid.UUID
    type: str
    status: str
    outcome: str | None
    opened_at: datetime
    updated_at: datetime
    review_by: datetime | None
    resolved_at: datetime | None
    # The first 5 subjects (by display name); ``user_count`` is all of them.
    users: list[UserRef]
    user_count: int
    deed_count: int
    held_count: int
    # Server-made, e.g. "Priya and Ahmed recognised each other 3 times in 12 days".
    summary: str


class CaseCounts(BaseModel):
    open: int
    parked: int
    resolved: int


class CasePage(BaseModel):
    items: list[CaseRow]
    counts: CaseCounts
    next_cursor: str | None


class CaseHeader(CaseRow):
    signal: dict[str, Any]
    detector_config_version: int


class CaseDeed(BaseModel):
    deed_id: uuid.UUID
    reporter: UserRef
    doer: UserRef
    category_label: str
    area_name: str
    description: str
    verified_at: datetime | None
    status: str
    counts_for_points: bool
    held: bool
    outcome: Literal["exclude", "reject", "release"] | None
    # Another verified report of the same act came from someone who is not in the case (D88:
    # "Independent witness too").
    independent_witness: bool


class CaseEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: UserRef = Field(alias="from")
    to: UserRef
    deeds: int
    shared_device: bool
    shared_network: bool


class CaseUser(BaseModel):
    user: UserRef
    joined_at: datetime
    # Verified deeds in ``CaseDetail.window`` (any counterpart).
    verified_as_doer_in_window: int
    verified_as_reporter_in_window: int
    other_open_cases: int
    earlier_confirmed_cases: int


class CaseDecision(BaseModel):
    action: str
    note: str
    actor: UserRef
    created_at: datetime


class FinalizationMonth(BaseModel):
    period_month: str
    finalizes_at: datetime
    final: bool


class CaseExplanation(BaseModel):
    user: UserRef
    requested_at: datetime
    reply_due_at: datetime
    reply: str | None
    replied_at: datetime | None


class CountWindow(BaseModel):
    since: datetime
    until: datetime


class CaseDetail(BaseModel):
    case: CaseHeader
    users: list[CaseUser]
    deeds: list[CaseDeed]
    edges: list[CaseEdge]
    decisions: list[CaseDecision]
    explanations: list[CaseExplanation]
    finalization: list[FinalizationMonth]
    # The period the per-user counts cover.
    window: CountWindow


# --------------------------------------------------- #4 to #6, #10 actions


class NoteIn(BaseModel):
    note: str = Field(max_length=_MAX_BODY_TEXT)


class DeedAction(BaseModel):
    deed_id: uuid.UUID
    action: Literal["exclude", "reject", "release"]


class DecisionIn(BaseModel):
    outcome: Literal["confirmed", "dismissed"]
    note: str = Field(max_length=_MAX_BODY_TEXT)
    deeds: list[DeedAction] | None = Field(default=None, max_length=_MAX_DEEDS)


class ExplanationsIn(BaseModel):
    # Default: every subject who hasn't been asked yet.
    handles: list[str] | None = Field(default=None, max_length=50)
    note: str = Field(max_length=_MAX_BODY_TEXT)


# --------------------------------------------------- #7 status


class DetectorRunOut(BaseModel):
    status: str
    started_at: datetime
    finished_at: datetime | None
    edges_scanned: int
    cases_opened: int


class DetectorStatus(BaseModel):
    detector: str
    enabled: bool
    # The last sweep or CLI run.
    last_run: DetectorRunOut | None
    # Per-deed checks that hit a search limit in the last 7 days and flagged nothing (SEC5-M6);
    # the nightly sweep retries them with a bigger budget.
    truncated_checks_7d: int


class FraudStatus(BaseModel):
    config_version: int
    detectors: list[DetectorStatus]
    # Open and parked cases this admin may see, by type.
    open_cases_by_type: dict[str, int]
    # Open appeals no active admin may decide (each is the decider or conflicted): they wait,
    # and permanent steps stay paused (PO Q78). The UI shows "Appeals need a second admin".
    appeals_waiting_for_second_admin: int


# --------------------------------------------------- #8 audit view


class AuditItem(BaseModel):
    id: uuid.UUID
    created_at: datetime
    entity_type: str
    entity_id: str
    action: str
    actor: UserRef | None
    reason: str | None
    old_value: dict[str, Any] | None
    new_value: dict[str, Any] | None


class AuditPage(BaseModel):
    items: list[AuditItem]
    next_cursor: str | None


# --------------------------------------------------- #11, #12 appeals (admin)


class AppealRow(BaseModel):
    appeal_id: uuid.UUID
    user: UserRef
    kind: Literal["case", "override"]
    case_id: uuid.UUID | None
    deed_count: int
    created_at: datetime
    original_decider: UserRef
    outcome: Literal["upheld", "overturned"] | None
    decided_at: datetime | None


class AppealCounts(BaseModel):
    open: int
    decided: int


class AppealPage(BaseModel):
    items: list[AppealRow]
    counts: AppealCounts
    next_cursor: str | None


class AppealNoticeItem(BaseModel):
    deed_id: uuid.UUID
    # ``excluded``, ``pending_removal`` or ``points_changed``.
    effect: str
    old_points: int | None
    new_points: int | None
    reporter: UserRef
    doer: UserRef
    category_label: str
    area_name: str
    occurred_on: date | None
    status: str | None


class AppealNotice(BaseModel):
    notice_id: uuid.UUID
    created_at: datetime
    reason_key: str
    # What the person was told (D101).
    reason_text: str
    appeal_until: datetime
    final_at: datetime | None
    items: list[AppealNoticeItem]


class AppealDetail(BaseModel):
    appeal_id: uuid.UUID
    user: UserRef
    kind: Literal["case", "override"]
    case_id: uuid.UUID | None
    override_id: uuid.UUID | None
    # The person's written reason.
    reason: str
    created_at: datetime
    original_decider: UserRef
    notice: AppealNotice
    outcome: Literal["upheld", "overturned"] | None
    reviewer: UserRef | None
    reviewer_note: str | None
    decided_at: datetime | None


class AppealDecisionIn(BaseModel):
    outcome: Literal["upheld", "overturned"]
    note: str = Field(max_length=_MAX_BODY_TEXT)


# --------------------------------------------------- M1 to M4 member reviews (D99 to D101)
#
# Member shapes never name a case type, a detector, the other people in a case or an admin,
# and never say "fraud" (D101). ``tests/fraud/test_member_reviews.py`` scans them.


class MyDeedRef(BaseModel):
    """One of the member's own deeds, with the other person on that deed."""

    deed_id: uuid.UUID
    role: Literal["doer", "reporter"]
    category_label: str
    area_name: str
    occurred_on: date
    other_person: UserRef


class MyExplanation(BaseModel):
    id: uuid.UUID
    requested_at: datetime
    reply_due_at: datetime
    replied_at: datetime | None
    reply: str | None
    status: Literal["waiting", "replied", "closed"]
    # "We've finished looking. No changes." once the look ended with nothing for this person.
    outcome_text: str | None
    deeds: list[MyDeedRef]


class MyNoticeItem(BaseModel):
    deed: MyDeedRef
    effect_text: str


class MyAppeal(BaseModel):
    created_at: datetime
    reason: str
    outcome: Literal["upheld", "overturned"] | None
    # "We looked again and restored your deeds" / "We looked again and the decision stays".
    outcome_text: str | None
    decided_at: datetime | None


class MyNotice(BaseModel):
    id: uuid.UUID
    created_at: datetime
    reason_text: str
    items: list[MyNoticeItem]
    appeal_until: datetime
    can_appeal: bool
    appeal: MyAppeal | None
    final: bool


class MyReviewLimits(BaseModel):
    reply_max_chars: int
    appeal_reason_min_chars: int
    appeal_reason_max_chars: int


class MyReviews(BaseModel):
    explanations: list[MyExplanation]
    notices: list[MyNotice]
    # Items new or changed since the member last opened the page (the Profile tab dot).
    unread: int
    limits: MyReviewLimits


class ReplyIn(BaseModel):
    reply: str = Field(max_length=_MAX_BODY_TEXT)


class AppealIn(BaseModel):
    reason: str = Field(max_length=_MAX_BODY_TEXT)
