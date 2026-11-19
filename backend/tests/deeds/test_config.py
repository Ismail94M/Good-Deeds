"""The `deeds` config namespace (docs/M2-DESIGN.md §2.1, §7 Config)."""

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import ConfigVersion
from app.modules.deeds._limits import DeedLimits, load_limits


async def test_seeded_deeds_row_validates_against_deed_limits(session: AsyncSession) -> None:
    row = await session.get(ConfigVersion, ("deeds", 5))
    assert row is not None
    limits = DeedLimits.model_validate(row.values)
    # Every seeded key is modelled, and nothing else.
    assert set(DeedLimits.model_fields) == set(row.values)
    assert limits.same_act_cap == 5
    assert limits.same_act_radius_m == 200
    assert limits.same_act_window_minutes == 120
    assert limits.scan_submit_minutes == 15
    assert limits.qr_occurred_max_age_minutes == 120
    assert limits.max_location_accuracy_m == 500
    assert limits.rate_manual_target == "20/900"
    assert limits.reporter_monthly_limit == 150  # D19 raised it from 15
    assert limits.reporter_doer_monthly_limit == 15
    assert limits.area_fallback_m == 1500
    assert limits.invites_enabled is False
    assert limits.has_category("animals")
    assert not limits.has_category("crypto")


async def test_load_limits_returns_active_version(session: AsyncSession) -> None:
    version, limits = await load_limits(session)
    assert version >= 1
    assert isinstance(limits, DeedLimits)


async def test_deeds_v2_is_v1_plus_the_d16_d17_keys(session: AsyncSession) -> None:
    """Migration 0007 publishes v2 = v1 + ``reporter_monthly_limit`` + ``invites_enabled``."""
    v1 = await session.get(ConfigVersion, ("deeds", 1))
    v2 = await session.get(ConfigVersion, ("deeds", 2))
    assert v1 is not None and v2 is not None
    assert v2.values == {**v1.values, "reporter_monthly_limit": 15, "invites_enabled": False}
    assert v2.created_by is None
    assert "D16" in v2.reason and "D17" in v2.reason
    # v1 lacks the new keys, so the current model can't read it: v2 must stay the active base.
    with pytest.raises(ValidationError):
        DeedLimits.model_validate(v1.values)


async def test_deeds_v3_is_v2_plus_the_d19_d23_values(session: AsyncSession) -> None:
    """Migration 0008 publishes v3 = v2 merged with the pair limit (15), the raised total (150)
    and ``photos_enabled`` (false). v3 is the active version on a fresh database."""
    v2 = await session.get(ConfigVersion, ("deeds", 2))
    v3 = await session.get(ConfigVersion, ("deeds", 3))
    assert v2 is not None and v3 is not None
    assert v3.values == {
        **v2.values,
        "reporter_doer_monthly_limit": 15,
        "reporter_monthly_limit": 150,
        "photos_enabled": False,
    }
    assert v3.created_by is None
    assert "D19" in v3.reason and "D23" in v3.reason
    version, limits = await load_limits(session)
    assert version >= 3 and limits.reporter_monthly_limit == 150
    # v2 lacks the v3 keys, so the current model can't read it.
    with pytest.raises(ValidationError):
        DeedLimits.model_validate(v2.values)


async def test_deeds_v4_adds_the_daily_manual_target_limit(session: AsyncSession) -> None:
    """Migration 0009 (security L4) publishes v4 = v3 + ``rate_manual_target_daily``."""
    v3 = await session.get(ConfigVersion, ("deeds", 3))
    v4 = await session.get(ConfigVersion, ("deeds", 4))
    assert v3 is not None and v4 is not None
    assert v4.values == {**v3.values, "rate_manual_target_daily": "60/86400"}
    assert v4.created_by is None and "L4" in v4.reason
    _, limits = await load_limits(session)
    assert (limits.rate_manual_target, limits.rate_manual_target_daily) == ("20/900", "60/86400")
    with pytest.raises(ValidationError):
        DeedLimits.model_validate(v3.values)


async def test_deeds_v5_moves_photos_enabled_and_adds_area_fallback(session: AsyncSession) -> None:
    """Migration 0013 (M3 D39, D41) publishes v5 = v4 minus ``photos_enabled`` (now in the
    ``media`` namespace) plus ``area_fallback_m = 1500``; v5 is the active version on a fresh
    database."""
    v4 = await session.get(ConfigVersion, ("deeds", 4))
    v5 = await session.get(ConfigVersion, ("deeds", 5))
    assert v4 is not None and v5 is not None
    expected = {k: v for k, v in v4.values.items() if k != "photos_enabled"}
    assert v5.values == {**expected, "area_fallback_m": 1500}
    assert v5.created_by is None and "D39" in v5.reason and "D41" in v5.reason
    assert await session.get(ConfigVersion, ("deeds", 6)) is None
    version, limits = await load_limits(session)
    assert version == 5 and limits.area_fallback_m == 1500
    # v4 still has photos_enabled and lacks area_fallback_m: the current model can't read it.
    with pytest.raises(ValidationError):
        DeedLimits.model_validate(v4.values)
    # A snapshot that still carries photos_enabled is rejected (extra keys are forbidden).
    with pytest.raises(ValidationError):
        DeedLimits.model_validate({**v5.values, "photos_enabled": True})


async def test_deed_limits_has_no_defaults(session: AsyncSession) -> None:
    row = await session.get(ConfigVersion, ("deeds", 5))
    assert row is not None
    for key in (
        "same_act_cap",
        "reporter_monthly_limit",
        "reporter_doer_monthly_limit",
        "area_fallback_m",
        "invites_enabled",
        "rate_manual_target_daily",
    ):
        values = dict(row.values)
        values.pop(key)
        with pytest.raises(ValidationError):
            DeedLimits.model_validate(values)
    with pytest.raises(ValidationError):
        DeedLimits.model_validate({**row.values, "unexpected": 1})
