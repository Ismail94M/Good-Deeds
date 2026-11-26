"""Request and response shapes of the fraud admin API (docs/M5-DESIGN.md §3.2, §3.3).

Admin-only. No coordinates, phone numbers, device or IP hashes anywhere (D95): shared devices and
networks are booleans per pair. Points are not here (``fraud`` can't read scoring, D78); the UI
asks ``GET /api/admin/scoring/deeds/{id}``.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Request bodies are bounded here; the config bounds (``note_max_chars``) are checked in the
# service so they can change without a deploy.
_MAX_BODY_TEXT = 10_000
_MAX_DEEDS = 1_000


class UserRef(BaseModel):
    handle: str
    display_name: str


# --------------------------------------------------- #1 options


class CaseTypeOption(BaseModel):
    id: str
    label: str
    enabled: bool
    holds_on_open: bool


class ReasonCodeOption(BaseModel):
    id: str
    label: str


class FraudOptions(BaseModel):
    types: list[CaseTypeOption]
    statuses: list[str]
    outcomes: list[str]
    deed_outcomes: list[str]
    note_min_chars: int
    note_max_chars: int
    override_reason_codes: list[ReasonCodeOption]
    override_max_points: int
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
    last_run: DetectorRunOut | None


class FraudStatus(BaseModel):
    config_version: int
    detectors: list[DetectorStatus]
    # Open and parked cases this admin may see, by type.
    open_cases_by_type: dict[str, int]


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
