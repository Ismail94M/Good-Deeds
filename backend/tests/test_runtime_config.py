import uuid

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import advisory_xact_lock
from app.core.errors import AppError, Gone
from app.core.runtime_config import ConfigVersion, RuntimeConfig, load, publish
from app.modules.identity._qr import QrConfig


class _ServiceArea(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lat: float
    lng: float
    radius_km: float


class _Category(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    slug: str
    label: str


class DeedsTestConfig(RuntimeConfig):
    """Mirrors the seeded ``deeds`` keys (docs/M2-DESIGN.md §2.1). The real model is
    ``deeds._limits.DeedLimits`` (task B3)."""

    namespace = "deeds"
    invites_per_day: int
    invite_expiry_hours: int
    same_act_radius_m: int
    same_act_window_minutes: int
    same_act_cap: int
    scan_submit_minutes: int
    qr_occurred_max_age_minutes: int
    invite_occurred_max_age_days: int
    shared_device_pair_monthly_limit: int
    max_location_accuracy_m: int
    max_travel_speed_kmh: int
    travel_slack_m: int
    service_area: _ServiceArea
    description_min: int
    description_max: int
    beneficiaries_max: int
    categories: tuple[_Category, ...]
    rate_scan_witness: str
    rate_scan_ip: str
    rate_manual_witness: str
    rate_manual_ip: str
    rate_manual_target: str
    rate_invite_create_ip: str
    rate_invite_preview_ip: str
    rate_invite_accept_user: str
    rate_invite_accept_ip: str
    rate_invite_reissue_user: str
    rate_invite_reissue_ip: str
    rate_deed_edit_user: str
    rate_deed_edit_ip: str
    rate_self_log_user: str
    rate_self_log_ip: str
    rate_profile_counts_ip: str
    unconsumed_scan_retention_days: int


class MissingConfig(RuntimeConfig):
    namespace = "no_such_namespace"
    anything: int


async def test_seeded_qr_row_validates(session: AsyncSession) -> None:
    row = await session.get(ConfigVersion, ("qr", 1))
    assert row is not None
    assert row.created_by is None
    assert QrConfig.model_validate(row.values) == QrConfig(window_seconds=30, grace_windows=1)


async def test_seeded_deeds_row_validates(session: AsyncSession) -> None:
    row = await session.get(ConfigVersion, ("deeds", 1))
    assert row is not None
    assert row.created_by is None
    deeds = DeedsTestConfig.model_validate(row.values)
    assert deeds.same_act_cap == 5
    assert deeds.invites_per_day == 5
    assert deeds.same_act_radius_m == 200
    assert deeds.invite_expiry_hours == 168
    assert deeds.shared_device_pair_monthly_limit == 15
    assert [c.slug for c in deeds.categories][-1] == "other"


def test_models_reject_missing_and_extra_keys() -> None:
    with pytest.raises(ValidationError):
        QrConfig.model_validate({"window_seconds": 30})
    with pytest.raises(ValidationError):
        QrConfig.model_validate({"window_seconds": 30, "grace_windows": 1, "extra": 1})


async def test_load_returns_highest_version(session: AsyncSession) -> None:
    before, _ = await load(session, QrConfig)
    v1 = await publish(
        session, QrConfig(window_seconds=45, grace_windows=2), actor_id=None, reason="a"
    )
    v2 = await publish(
        session, QrConfig(window_seconds=20, grace_windows=0), actor_id=None, reason="b"
    )
    assert (v1, v2) == (before + 1, before + 2)

    version, qr = await load(session, QrConfig)
    assert version == v2
    assert qr == QrConfig(window_seconds=20, grace_windows=0)


async def test_publish_writes_audit_row(session: AsyncSession) -> None:
    _, old = await load(session, QrConfig)
    actor = uuid.uuid4()
    new = QrConfig(window_seconds=old.window_seconds, grace_windows=old.grace_windows + 1)
    version = await publish(session, new, actor_id=actor, reason="Allow one more window")

    row = await session.get(ConfigVersion, ("qr", version))
    assert row is not None
    assert row.values == new.model_dump(mode="json")
    assert row.created_by == actor
    assert row.reason == "Allow one more window"

    audits = (
        await session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == "config",
                AuditLog.entity_id == "qr",
                AuditLog.action == "config.published",
                AuditLog.actor_id == actor,
            )
        )
    ).all()
    assert len(audits) == 1
    assert audits[0].old_value == old.model_dump(mode="json")
    assert audits[0].new_value == new.model_dump(mode="json")
    assert audits[0].reason == "Allow one more window"


async def test_publish_first_version_of_new_namespace(session: AsyncSession) -> None:
    class FreshConfig(RuntimeConfig):
        namespace = f"test_{uuid.uuid4().hex[:8]}"
        limit: int

    version = await publish(session, FreshConfig(limit=3), actor_id=None, reason="first")
    assert version == 1
    assert await load(session, FreshConfig) == (1, FreshConfig(limit=3))

    audit = (
        await session.scalars(select(AuditLog).where(AuditLog.entity_id == FreshConfig.namespace))
    ).one()
    assert audit.old_value is None


async def test_missing_namespace_fails_closed(session: AsyncSession) -> None:
    with pytest.raises(RuntimeError, match="no_such_namespace"):
        await load(session, MissingConfig)


async def test_invalid_stored_values_fail_closed(session: AsyncSession) -> None:
    session.add(
        ConfigVersion(
            namespace=MissingConfig.namespace,
            version=1,
            values={"anything": 1, "unexpected": True},
            reason="bad row",
        )
    )
    await session.flush()
    with pytest.raises(ValidationError):
        await load(session, MissingConfig)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE config_versions SET reason = 'changed' WHERE namespace = 'qr' AND version = 1",
        "DELETE FROM config_versions WHERE namespace = 'qr' AND version = 1",
    ],
)
async def test_config_versions_are_append_only(session: AsyncSession, statement: str) -> None:
    with pytest.raises(DBAPIError, match="append-only"):
        async with session.begin_nested():
            await session.execute(text(statement))


async def test_version_must_be_positive(session: AsyncSession) -> None:
    with pytest.raises(DBAPIError, match="ck_config_versions_version_positive"):
        async with session.begin_nested():
            session.add(ConfigVersion(namespace="qr", version=0, values={}, reason="x"))
            await session.flush()


async def test_advisory_xact_lock_is_held_in_transaction(session: AsyncSession) -> None:
    async def advisory_locks() -> int:
        result = await session.execute(
            text(
                "SELECT count(*) FROM pg_locks "
                "WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
            )
        )
        return int(result.scalar_one())

    before = await advisory_locks()
    await advisory_xact_lock(session, "test", str(uuid.uuid4()))
    assert await advisory_locks() == before + 1
    # Re-entrant within the same transaction: taking the same key again doesn't block.
    await advisory_xact_lock(session, "test", "same")
    await advisory_xact_lock(session, "test", "same")


def test_gone_is_http_410() -> None:
    err = Gone("This invite has expired.", code="invite_expired")
    assert isinstance(err, AppError)
    assert err.status_code == 410
    assert err.code == "invite_expired"
