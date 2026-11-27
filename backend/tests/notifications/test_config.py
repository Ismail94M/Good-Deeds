"""The ``notifications`` config namespace (docs/M6-DESIGN.md §2.5, D125)."""

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications._limits import NotificationsConfig, load_notifications
from app.modules.notifications.models import NotificationCategory


async def test_seeded_v1_validates(session: AsyncSession) -> None:
    version, config = await load_notifications(session)
    assert version >= 1
    assert config.categories[NotificationCategory.ACCOUNT].locked
    assert config.categories[NotificationCategory.REVIEWS].locked
    assert not config.categories[NotificationCategory.ACTIVITY].locked
    assert config.daily_caps.activity == 30
    assert config.daily_cap(NotificationCategory.NETWORK) == 1
    assert config.daily_cap(NotificationCategory.ABOUT_YOU) is None
    assert config.retention_days == 90
    assert config.actors_kept == 3
    assert len(config.review_kinds) == 5


async def _values(session: AsyncSession) -> dict[str, Any]:
    _, config = await load_notifications(session)
    return config.model_dump(mode="json")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda v: v["categories"].pop("network"), "categories missing"),
        (
            lambda v: v["categories"].update(account={"default": True, "locked": False}),
            "must be locked",
        ),
        (
            lambda v: v["categories"].update(network={"default": False, "locked": True}),
            "locked category must be on",
        ),
        (lambda v: v.update(review_kinds=["decision_notice", "decision_notice"]), "repeat"),
        (lambda v: v.update(review_kinds=["case_opened"]), "review_kinds"),
        (lambda v: v.update(timezone="Mars/Olympus"), "unknown timezone"),
        (lambda v: v["daily_caps"].update(activity=0), "daily_caps"),
        (lambda v: v["network"].update(audience="everyone"), "audience"),
        (lambda v: v["network"].update(roles=["doer", "doer"]), "repeat"),
        (lambda v: v.update(rate_read="many"), "rate_read"),
        (lambda v: v.update(extra_key=1), "extra"),
    ],
)
async def test_invalid_values_are_refused(session: AsyncSession, change: Any, message: str) -> None:
    values = await _values(session)
    change(values)
    with pytest.raises(ValidationError, match=message):
        NotificationsConfig.model_validate(values)
