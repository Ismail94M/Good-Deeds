"""Request and response models for the deeds API (docs/M2-DESIGN.md §3).

Organised by section, matching ``router.py``. No response model contains coordinates (M2).
Hard ceilings are enforced here (422); configurable limits (categories, description length,
beneficiaries) are checked by the service against the ``deeds`` config (400).
"""

import uuid
from datetime import datetime
from typing import Literal, Self

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


# B6 adds ``InviteDeedCreate`` (method="invite") and turns this into a discriminated union:
#   DeedCreate = Annotated[QrDeedCreate | InviteDeedCreate, Field(discriminator="method")]
DeedCreate = QrDeedCreate


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
    can_edit: bool
    can_withdraw: bool
    # Reporter only.
    invite: InviteOut | None


# --------------------------------------------------- invites (B6)

# --------------------------------------------------- self-logs and profile counts (B7)

# --------------------------------------------------- read endpoints and options (B8)

# --------------------------------------------------- edit and withdraw (B11)
