"""The `deeds` config namespace (docs/M2-DESIGN.md §2.1, §7 Config)."""

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import ConfigVersion
from app.modules.deeds._limits import DeedLimits, load_limits


async def test_seeded_deeds_row_validates_against_deed_limits(session: AsyncSession) -> None:
    row = await session.get(ConfigVersion, ("deeds", 1))
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
    assert limits.has_category("animals")
    assert not limits.has_category("crypto")


async def test_load_limits_returns_active_version(session: AsyncSession) -> None:
    version, limits = await load_limits(session)
    assert version >= 1
    assert isinstance(limits, DeedLimits)


async def test_deed_limits_has_no_defaults(session: AsyncSession) -> None:
    row = await session.get(ConfigVersion, ("deeds", 1))
    assert row is not None
    values = dict(row.values)
    values.pop("same_act_cap")
    with pytest.raises(ValidationError):
        DeedLimits.model_validate(values)
    with pytest.raises(ValidationError):
        DeedLimits.model_validate({**row.values, "unexpected": 1})
