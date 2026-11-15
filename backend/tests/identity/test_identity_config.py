"""The ``identity`` config namespace (task B10): M1's OTP and auth rate limits come from
versioned config rows, not env settings."""

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.runtime_config import ConfigVersion
from app.core.sms import ConsoleSmsProvider
from app.modules.identity._limits import IdentityLimits, load_limits
from tests.identity.helpers import (
    otp_from,
    publish_limits,
    signup,
    start_phone,
    valid_phone,
    verify_phone,
)

# The old Settings defaults; the seed must keep behaviour unchanged.
EXPECTED_V1 = {
    "otp_ttl_minutes": 10,
    "otp_max_attempts": 5,
    "otp_resend_seconds": 30,
    "otp_max_per_hour": 5,
    "rate_login_ip": "20/900",
    "rate_login_email": "10/900",
    "rate_signup_ip": "10/3600",
    "rate_phone_start_ip": "10/3600",
    "rate_sms_global": "500/3600",
    "rate_phone_verify_user": "20/3600",
    "rate_refresh_ip": "120/900",
    "rate_oauth_ip": "30/900",
}


async def test_seeded_identity_row_matches_model(session: AsyncSession) -> None:
    row = await session.get(ConfigVersion, ("identity", 1))
    assert row is not None
    assert row.created_by is None
    assert row.values == EXPECTED_V1
    assert IdentityLimits.model_validate(row.values).model_dump() == EXPECTED_V1


def test_model_has_no_defaults_and_forbids_extra_keys() -> None:
    assert all(field.is_required() for field in IdentityLimits.model_fields.values())
    assert set(IdentityLimits.model_fields) == set(EXPECTED_V1)
    for missing in EXPECTED_V1:
        with pytest.raises(ValidationError):
            IdentityLimits.model_validate({k: v for k, v in EXPECTED_V1.items() if k != missing})
    with pytest.raises(ValidationError):
        IdentityLimits.model_validate({**EXPECTED_V1, "unexpected": 1})


def test_limits_are_no_longer_env_settings() -> None:
    names = set(Settings.model_fields)
    assert not {n for n in names if n.startswith(("rate_", "otp_"))}


async def test_published_version_becomes_active(session: AsyncSession) -> None:
    published = await publish_limits(session, otp_max_attempts=2, rate_login_ip="1/60")
    assert await load_limits(session) == published
    assert published.otp_max_attempts == 2
    assert published.rate_login_ip == "1/60"
    assert published.otp_ttl_minutes == EXPECTED_V1["otp_ttl_minutes"]


async def test_published_otp_attempts_take_effect(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    await publish_limits(session, otp_max_attempts=2)
    s = await signup(client)
    phone = valid_phone(90)
    await start_phone(client, s, phone)
    code = otp_from(sms, phone)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    for _ in range(2):
        assert (await verify_phone(client, s, phone, wrong)).json()["code"] == "otp_incorrect"
    locked = await verify_phone(client, s, phone, code)
    assert locked.status_code == 429
    assert locked.json()["code"] == "otp_locked"


async def test_published_otp_resend_and_ttl_take_effect(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    await publish_limits(session, otp_resend_seconds=0, otp_ttl_minutes=3)
    s = await signup(client)
    first = await start_phone(client, s, valid_phone(91))
    assert first.status_code == 200
    assert first.json()["resend_after_seconds"] == 0
    assert "expires in 3 minutes" in sms.sent[valid_phone(91)]
    # With no resend wait, an immediate second request is allowed.
    assert (await start_phone(client, s, valid_phone(91))).status_code == 200
