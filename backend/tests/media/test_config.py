"""The ``media`` config namespace (docs/M3-DESIGN.md §2.5, §8 Config, migration 0014)."""

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import ConfigVersion
from app.modules.media._limits import MediaLimits, load_limits

DESIGN_V1: dict[str, Any] = {
    "photos_enabled": True,
    "photos_per_owner": 4,
    "photo_edit_window_hours": 24,
    "upload_allowed_types": ["image/jpeg", "image/png", "image/webp"],
    "upload_max_bytes": 10485760,
    "upload_url_ttl_seconds": 600,
    "pending_upload_ttl_hours": 24,
    "max_pending_uploads_per_user": 12,
    "max_image_pixels": 40000000,
    "min_image_side_px": 200,
    "variants": [
        {"name": "thumb", "max_side_px": 320},
        {"name": "display", "max_side_px": 1280},
    ],
    "webp_quality": 80,
    "blur_max_regions": 8,
    "blur_min_region": 0.02,
    "processing_max_attempts": 3,
    "processing_stuck_minutes": 10,
    "url_ttl_minutes": 60,
    "url_step_minutes": 30,
    "report_details_max": 500,
    "decision_note_max": 1000,
    "report_outcome_display_days": 30,
    "removed_photo_retention_days": 90,
    "rate_upload_user": "40/3600",
    "rate_upload_ip": "200/3600",
    "rate_photo_write_user": "60/3600",
    "rate_report_user": "10/3600",
    "rate_report_ip": "10/3600",
    "rate_report_ip_daily": "30/86400",
    "rate_admin_decision_user": "300/3600",
}


async def _v1(session: AsyncSession) -> dict[str, Any]:
    row = await session.get(ConfigVersion, ("media", 1))
    assert row is not None
    assert row.created_by is None
    return dict(row.values)


async def test_media_v1_is_the_design_seed(session: AsyncSession) -> None:
    values = await _v1(session)
    assert values == DESIGN_V1
    assert set(MediaLimits.model_fields) == set(values)  # all keys, no extras
    limits = MediaLimits.model_validate(values)
    assert limits.photos_enabled is True  # moved from deeds (D41)
    assert limits.variant("display").max_side_px == 1280
    assert limits.removed_photo_retention_days == 90  # Q32
    assert limits.report_outcome_display_days == 30  # Q39


async def test_load_limits_returns_the_active_version(session: AsyncSession) -> None:
    version, limits = await load_limits(session)
    assert version >= 1 and isinstance(limits, MediaLimits)


async def test_media_limits_have_no_defaults_and_no_extras(session: AsyncSession) -> None:
    values = await _v1(session)
    for key in values:
        partial = {k: v for k, v in values.items() if k != key}
        with pytest.raises(ValidationError):
            MediaLimits.model_validate(partial)
    with pytest.raises(ValidationError):
        MediaLimits.model_validate({**values, "unexpected": 1})


@pytest.mark.parametrize(
    "change",
    [
        {"upload_allowed_types": ["image/gif"]},
        {"upload_allowed_types": []},
        {"upload_allowed_types": ["image/png", "image/png"]},
        {"variants": [{"name": "thumb", "max_side_px": 320}]},
        {"variants": [{"name": "thumb", "max_side_px": 0}, {"name": "display", "max_side_px": 1}]},
        {
            "variants": [
                {"name": "thumb", "max_side_px": 1},
                {"name": "thumb", "max_side_px": 2},
                {"name": "display", "max_side_px": 3},
            ]
        },
        {"url_step_minutes": 60},
        {"photos_per_owner": 0},
        {"webp_quality": 101},
        {"blur_min_region": 0},
        {"report_details_max": 501},
        {"rate_upload_user": "forty per hour"},
        {"rate_report_ip": "0/3600"},
    ],
)
async def test_media_limits_reject_inconsistent_values(
    session: AsyncSession, change: dict[str, Any]
) -> None:
    values = await _v1(session)
    with pytest.raises(ValidationError):
        MediaLimits.model_validate({**values, **change})
