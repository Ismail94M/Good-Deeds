"""Request and response shapes of the media API (docs/M3-DESIGN.md §3.2, §3.3, §3.4b).

Responses carry storage information only as signed URLs, and user information only as
``UserRef`` (handle and display name). No coordinates, IDs of people, hashes or keys.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class UserRef(_Out):
    handle: str
    display_name: str


class PhotoOut(_Out):
    """A ready, public photo (feed, deed page, public page)."""

    id: uuid.UUID
    thumb_url: str
    display_url: str
    width: int
    height: int
    urls_expire_at: datetime


class OwnerPhoto(_Out):
    id: uuid.UUID
    position: int
    status: Literal["processing", "ready", "failed"]
    thumb_url: str | None
    display_url: str | None
    blurred_count: int
    can_remove: bool


class Outcome(_Out):
    decision: Literal["removed", "kept", "removed_by_doer"]
    removal_reason: str | None
    # Only for ``removed_by_doer`` (Q29).
    removed_by: UserRef | None
    decided_at: datetime
    photo_count: int


class ReportReasonOut(_Out):
    id: str
    label: str
    priority: int


class OptionsOut(_Out):
    photos_enabled: bool
    photos_per_owner: int
    photo_edit_window_hours: int
    upload_allowed_types: list[str]
    upload_max_bytes: int
    blur_max_regions: int
    # Smallest blur width and height, as a fraction of the photo (§4.5).
    blur_min_region: float
    decision_note_max: int
    report_reasons: list[ReportReasonOut]


# --------------------------------------------------- uploads


class UploadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["deed", "self_log"]
    content_type: str = Field(max_length=100)
    size_bytes: int = Field(gt=0)


class SignedUpload(_Out):
    url: str
    method: Literal["PUT"]
    headers: dict[str, str]


class UploadOut(_Out):
    photo_id: uuid.UUID
    upload: SignedUpload
    expires_at: datetime


class CompleteOut(_Out):
    photo_id: uuid.UUID
    status: Literal["uploaded"]


# --------------------------------------------------- attach


class BlurRegionIn(BaseModel):
    """A normalised ellipse's bounding box (fractions of the upright displayed image, §4.5).
    Range checks that depend on config (``blur_min_region``) run in the service."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)
    w: float = Field(gt=0, le=1, allow_inf_nan=False)
    h: float = Field(gt=0, le=1, allow_inf_nan=False)


class AttachItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    photo_id: uuid.UUID
    blur_regions: list[BlurRegionIn] = Field(default_factory=list, max_length=64)


class AttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The upper bound is config (``photos_per_owner``), checked in the service.
    photos: list[AttachItem] = Field(min_length=1, max_length=64)


# --------------------------------------------------- owner reads


class AttachOut(_Out):
    photos: list[OwnerPhoto]


class DeedPhotosOut(_Out):
    photos: list[OwnerPhoto]
    can_add: bool
    add_until: datetime | None
    outcomes: list[Outcome]


class SelfLogPhotosOut(_Out):
    photos: list[OwnerPhoto]


# --------------------------------------------------- photo reports (#10)


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal[
        "not_consented", "child_no_guardian_consent", "private_details", "inappropriate", "other"
    ]
    # The product limit (``report_details_max``) is checked in the service; 500 is the column.
    details: str | None = Field(default=None, max_length=2000)


class ReportReceived(_Out):
    status: Literal["received"] = "received"


# --------------------------------------------------- admin (#11 to #14, §3.4b #3, #4, #6)


class CaseOut(_Out):
    case_id: uuid.UUID
    status: Literal["open", "resolved", "closed"]
    priority: int
    top_reason: str
    report_count: int
    opened_at: datetime
    resolved_at: datetime | None
    decision: Literal["kept", "removed"] | None
    removal_reason: str | None


class AdminDeedRef(_Out):
    doer: UserRef | None
    category: str
    area_name: str


class CaseListItem(CaseOut):
    oldest_report_at: datetime
    photo_thumb_url: str | None
    deed: AdminDeedRef


class CasePage(_Out):
    items: list[CaseListItem]
    next_cursor: str | None
    open_count: int
    resolved_count: int


class AdminPhoto(_Out):
    id: uuid.UUID
    display_url: str | None
    position: int | None
    photo_count: int
    uploaded_at: datetime | None
    uploader: UserRef | None
    blurred_count: int
    state: Literal["visible", "removed"]
    restorable_until: datetime | None
    purged_at: datetime | None


class AdminDeed(_Out):
    doer: UserRef | None
    reporter: UserRef | None
    category: str
    area_name: str
    status: str
    listed: bool


class ReporterRef(_Out):
    kind: Literal["member", "visitor"]
    handle: str | None = None


class AdminReport(_Out):
    reason: str
    details: str | None
    reporter: ReporterRef
    created_at: datetime


class AdminDecision(_Out):
    action: Literal["keep", "remove", "restore"]
    note: str
    actor: UserRef | None
    created_at: datetime


class CaseDetail(_Out):
    case: CaseOut
    photo: AdminPhoto
    deed: AdminDeed
    reports: list[AdminReport]
    decisions: list[AdminDecision]


class DecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["keep", "remove"]
    # Length rules use config (5..decision_note_max), checked in the service.
    note: str = Field(max_length=10000)
    removal_reason: (
        Literal[
            "not_consented",
            "child_no_guardian_consent",
            "private_details",
            "inappropriate",
            "other",
        ]
        | None
    ) = None


class RestoreCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(max_length=10000)
