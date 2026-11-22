"""Request and response models for the deeds API (docs/M2-DESIGN.md §3).

Organised by section, matching ``router.py``. No response model contains coordinates (M2).
Hard ceilings are enforced here (422); configurable limits (categories, description length,
beneficiaries) are checked by the service against the ``deeds`` config (400).
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.modules.deeds.models import DeedStatus, InviteSentVia, InviteStatus, VerificationMethod

# --------------------------------------------------- shared


class Location(BaseModel):
    """A client-reported position. Stored precisely, never returned."""

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: int = Field(ge=1, le=100_000)


class UserRef(BaseModel):
    """The public name of another user (same shape as identity's ``UserSummary``)."""

    handle: str
    display_name: str


# --------------------------------------------------- scans (§3.2 #1, #2)


class ScanCreate(BaseModel):
    """Exactly one identity form: a QR ``token``, or a typed ``short_id`` plus ``code``.

    Unknown fields are ignored (not rejected), so a client-sent timestamp has no effect: the
    server clock is the only time source.
    """

    token: str | None = Field(default=None, min_length=1, max_length=100)
    short_id: str | None = Field(default=None, min_length=1, max_length=20)
    code: str | None = Field(default=None, min_length=1, max_length=12)
    location: Location

    @model_validator(mode="after")
    def _one_identity_form(self) -> Self:
        manual = self.short_id is not None or self.code is not None
        if self.token is not None and manual:
            raise ValueError("Send either token, or short_id and code, not both")
        if self.token is None and (self.short_id is None or self.code is None):
            raise ValueError("Send either token, or both short_id and code")
        return self

    @property
    def is_manual(self) -> bool:
        return self.token is None


class ScanOut(BaseModel):
    scan_id: uuid.UUID
    doer: UserRef
    expires_at: datetime
    # Revision 5 (D20): the server's scan time (the form's "Earlier" choices are offsets from
    # it), the reports still allowed about this doer this IST month counted before this report
    # (1 = this is the last), and 00:00 IST on the 1st of next month (sent with +05:30).
    scanned_at: datetime
    pair_reports_remaining: int
    month_resets_at: datetime
    # M3 (§3.1): the area of the scan location ("Bangalore" when none), never coordinates.
    area_name: str


class ScanDetail(ScanOut):
    consumed: bool


# --------------------------------------------------- deed create (§3.2 #3)


class _DeedContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=1000)
    beneficiaries_count: int | None = Field(default=None, ge=1, le=10_000)


class QrDeedCreate(_DeedContent):
    method: Literal["qr_scan"]
    scan_id: uuid.UUID
    # Defaults to the scan time.
    occurred_at: AwareDatetime | None = None


class InviteDeedCreate(_DeedContent):
    """A pending deed plus an invite link for a doer who isn't at hand (D5: up to 7 days ago,
    optional location that need not be the reporter's current position)."""

    method: Literal["invite"]
    occurred_at: AwareDatetime
    location: Location | None = None
    sent_via: InviteSentVia


DeedCreate = Annotated[QrDeedCreate | InviteDeedCreate, Field(discriminator="method")]


# --------------------------------------------------- deed responses (§3.3)


class SameActOut(BaseModel):
    report_count: int
    counts_for_points: bool


class InviteOut(BaseModel):
    id: uuid.UUID
    deed_id: uuid.UUID
    status: InviteStatus
    sent_via: InviteSentVia
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    # The raw link: only on create and reissue.
    url: str | None = None


class DeedOut(BaseModel):
    id: uuid.UUID
    status: DeedStatus
    verification_method: VerificationMethod
    category: str
    description: str
    beneficiaries_count: int | None
    occurred_at: datetime
    reported_at: datetime
    verified_at: datetime | None
    withdrawn_at: datetime | None
    last_edited_at: datetime | None
    reporter: UserRef
    # Always null while pending (PRD §6.5).
    doer: UserRef | None
    same_act: SameActOut | None
    prize_rank_eligible: bool
    has_location: bool
    # M3 (§3.1, D39): the deed's area name, "Bangalore" when it has none. Never coordinates.
    area_name: str
    can_edit: bool
    # M4 (D64): false for a verified deed once its withdrawal window has ended.
    can_withdraw: bool
    # M4 (D64): a verified deed's ``verified_at + withdraw_window_hours`` (exclusive); null for
    # any other status (a pending invite can be cancelled at any time). Display only: the
    # server checks the window again under the deed row lock.
    withdrawable_until: datetime | None
    # Reporter only.
    invite: InviteOut | None


# --------------------------------------------------- invites (B6)


class InvitePreview(BaseModel):
    """Public preview of a usable invite (§3.2 #8). Never the doer or a location."""

    status: Literal["pending"]
    reporter: UserRef
    category: str
    description: str
    occurred_at: datetime
    expires_at: datetime


# --------------------------------------------------- self-logs and profile counts (B7)


class SelfLogCreate(BaseModel):
    """A private self-log (D3). Any past date; the service rejects more than 5 min ahead."""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=1000)
    occurred_at: AwareDatetime
    location: Location | None = None


class SelfLogUpdate(BaseModel):
    """Any subset of the create fields. ``location: null`` removes the stored location; the
    other fields can't be null."""

    model_config = ConfigDict(extra="forbid")

    category: str | None = Field(default=None, min_length=1, max_length=40)
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    occurred_at: AwareDatetime | None = None
    location: Location | None = None

    @model_validator(mode="after")
    def _no_null_content(self) -> Self:
        for name in ("category", "description", "occurred_at"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} can't be empty")
        return self


class SelfLogOut(BaseModel):
    id: uuid.UUID
    category: str
    description: str
    occurred_at: datetime
    has_location: bool
    created_at: datetime
    updated_at: datetime


class SelfLogPage(BaseModel):
    items: list[SelfLogOut]
    next_cursor: str | None


class DeedCountsOut(BaseModel):
    """Public profile count (D4). ``verified`` counts counted verified reports (D15). The
    Self-log Deeds count is no longer public (M4 D69, PO Q55)."""

    verified: int


# --------------------------------------------------- read endpoints and options (B8)


class DeedPage(BaseModel):
    items: list[DeedOut]
    next_cursor: str | None


class InvitePage(BaseModel):
    items: list[InviteOut]
    next_cursor: str | None


class CategoryOut(BaseModel):
    slug: str
    label: str


class DeedOptions(BaseModel):
    categories: list[CategoryOut]
    invites_enabled: bool
    invites_remaining_today: int
    invite_expiry_hours: int
    reports_remaining_this_month: int
    # Revision 5 (D20, D23): the limits and config values the F3 copy uses, so no number is
    # hard-coded in the frontend, and the month reset as an ISO timestamp (+05:30). Nothing
    # per-doer: the pair count comes with the scan.
    reporter_monthly_limit: int
    reporter_doer_monthly_limit: int
    # Warning thresholds (migration 0018): the hub warns at <= reporter_monthly_warn_at reports
    # left in total, the deed form at <= reporter_doer_monthly_warn_at left about the doer.
    reporter_monthly_warn_at: int
    reporter_doer_monthly_warn_at: int
    month_resets_at: datetime
    description_min: int
    description_max: int
    beneficiaries_max: int
    scan_submit_minutes: int
    qr_occurred_max_age_minutes: int
    max_location_accuracy_m: int
    service_area_radius_km: float
    # Reports of one act that count (Q20 extra-witness note: "the first 5 count").
    same_act_cap: int
    # photos_enabled moved to GET /api/media/options (M3 D41).


# --------------------------------------------------- edit and withdraw (B11)


class DeedEdit(BaseModel):
    """Reporter edit (§3.2 #14). Only the content fields; anything else (``occurred_at``,
    location, status, doer, ...) is rejected with 422. ``beneficiaries_count: null`` clears it."""

    model_config = ConfigDict(extra="forbid")

    category: str | None = Field(default=None, min_length=1, max_length=40)
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    beneficiaries_count: int | None = Field(default=None, ge=1, le=10_000)

    @model_validator(mode="after")
    def _no_null_content(self) -> Self:
        for name in ("category", "description"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} can't be empty")
        return self
