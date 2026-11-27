"""Typed values of the ``media`` config namespace (docs/M3-DESIGN.md §2.5, D41).

Every key is required and has no default: the ``config_versions`` rows (seeded as ``media`` v1 by
migration 0014; v2 adds the profile-picture keys, migration 0031, docs/M6-DESIGN.md §2.5) are the
only source of these values. Change them by publishing a new version.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import RuntimeConfig, load

# The pipeline can only decode these (magic-byte checks in §4.4); config may narrow the list.
SUPPORTED_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
# Rate specs are "<count>/<seconds>".
RATE_SPEC = r"^[1-9][0-9]*/[1-9][0-9]*$"
# The square profile-picture variants (D116); config sets their sizes.
AVATAR_VARIANT_NAMES = frozenset({"avatar_sm", "avatar_md", "avatar_lg"})


class Variant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,19}$")
    max_side_px: int = Field(gt=0)


class MediaLimits(RuntimeConfig):
    """Config namespace ``media``."""

    namespace = "media"

    # Moved here from ``deeds`` (D41); the options endpoint and every upload read it
    photos_enabled: bool
    # Photos per deed or self-log, and the add/remove window after sending
    photos_per_owner: int = Field(gt=0)
    photo_edit_window_hours: int = Field(gt=0)
    # Uploads (D32)
    upload_allowed_types: tuple[str, ...] = Field(min_length=1)
    upload_max_bytes: int = Field(gt=0)
    upload_url_ttl_seconds: int = Field(gt=0)
    pending_upload_ttl_hours: int = Field(gt=0)
    max_pending_uploads_per_user: int = Field(gt=0)
    # Processing (§4.4)
    max_image_pixels: int = Field(gt=0)
    min_image_side_px: int = Field(gt=0)
    variants: tuple[Variant, ...] = Field(min_length=1)
    webp_quality: int = Field(ge=1, le=100)
    blur_max_regions: int = Field(ge=0)
    blur_min_region: float = Field(gt=0, le=1)
    processing_max_attempts: int = Field(gt=0)
    processing_stuck_minutes: int = Field(gt=0)
    # Signed GET URLs (D34): signing time floored to a step, valid for the TTL
    url_ttl_minutes: int = Field(gt=0)
    url_step_minutes: int = Field(gt=0)
    # Reports and admin decisions (D42)
    report_details_max: int = Field(gt=0, le=500)  # the column is varchar(500)
    decision_note_max: int = Field(ge=5)
    report_outcome_display_days: int = Field(gt=0)
    removed_photo_retention_days: int = Field(gt=0)
    # Rate limits ("<count>/<seconds>")
    rate_upload_user: str = Field(pattern=RATE_SPEC)
    rate_upload_ip: str = Field(pattern=RATE_SPEC)
    rate_photo_write_user: str = Field(pattern=RATE_SPEC)
    rate_report_user: str = Field(pattern=RATE_SPEC)
    rate_report_ip: str = Field(pattern=RATE_SPEC)
    rate_report_ip_daily: str = Field(pattern=RATE_SPEC)
    rate_admin_decision_user: str = Field(pattern=RATE_SPEC)
    # Profile pictures (M6A, D116 to D123; media v2). Upload type and size reuse
    # ``upload_allowed_types`` and ``upload_max_bytes``.
    avatars_enabled: bool
    avatar_variants: tuple[Variant, ...] = Field(min_length=1)
    # The crop's side in pixels on the oriented image.
    avatar_min_side_px: int = Field(gt=0)
    # Signed GET URLs for avatars (D118): longer than deed photos'.
    avatar_url_ttl_minutes: int = Field(gt=0)
    avatar_url_step_minutes: int = Field(gt=0)
    # Refs per ``POST /api/media/avatars/urls``.
    avatar_sign_max_refs: int = Field(gt=0)
    # After an admin removal, a new upload waits this long (Q95).
    avatar_reupload_cooldown_hours: int = Field(ge=0)
    rate_avatar_change_user: str = Field(pattern=RATE_SPEC)
    rate_avatar_sign_user: str = Field(pattern=RATE_SPEC)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        unsupported = set(self.upload_allowed_types) - SUPPORTED_TYPES
        if unsupported:
            raise ValueError(f"upload_allowed_types has unsupported types: {sorted(unsupported)}")
        if len(set(self.upload_allowed_types)) != len(self.upload_allowed_types):
            raise ValueError("upload_allowed_types has duplicates")
        names = [v.name for v in self.variants]
        if len(set(names)) != len(names):
            raise ValueError("variant names must be unique")
        if not {"thumb", "display"} <= set(names):
            raise ValueError("variants must include 'thumb' and 'display'")
        # A URL signed at the start of a step must still be valid at the end of it.
        if self.url_step_minutes >= self.url_ttl_minutes:
            raise ValueError("url_step_minutes must be less than url_ttl_minutes")
        avatar_names = [v.name for v in self.avatar_variants]
        if len(set(avatar_names)) != len(avatar_names):
            raise ValueError("avatar variant names must be unique")
        if set(avatar_names) != AVATAR_VARIANT_NAMES:
            raise ValueError(f"avatar_variants must be exactly {sorted(AVATAR_VARIANT_NAMES)}")
        if self.avatar_url_step_minutes >= self.avatar_url_ttl_minutes:
            raise ValueError("avatar_url_step_minutes must be less than avatar_url_ttl_minutes")
        return self

    def variant(self, name: str) -> Variant:
        return next(v for v in self.variants if v.name == name)

    def avatar_variant(self, name: str) -> Variant:
        return next(v for v in self.avatar_variants if v.name == name)


async def load_limits(session: AsyncSession) -> tuple[int, MediaLimits]:
    """``(version, limits)`` of the active ``media`` config. Fails closed if it is missing."""
    return await load(session, MediaLimits)
