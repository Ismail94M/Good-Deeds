from datetime import UTC, date, datetime, timedelta, tzinfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent
from app.core.sms import ConsoleSmsProvider
from app.modules.identity import service
from app.modules.identity.models import Consent, DoerCode, User
from tests.identity.helpers import (
    REFRESH_COOKIE,
    ist_today,
    onboard_phone,
    set_cookie,
    signup,
    signup_payload,
    valid_phone,
    years_ago,
)


async def test_signup_happy_path_returns_token_and_refresh_cookie(client: AsyncClient) -> None:
    payload = signup_payload()
    response = await client.post("/api/auth/signup", json=payload)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    cookie = set_cookie(response)
    assert cookie is not None
    assert cookie.key == REFRESH_COOKIE
    assert cookie["httponly"] is True
    assert cookie["path"] == "/api/auth"
    assert cookie["samesite"].lower() == "lax"
    assert cookie.value

    me = await client.get(
        "/api/users/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["handle"] == payload["handle"]


async def test_signup_stores_lowercased_email_and_handle(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client, email="Mixed.Case@Example.COM", handle="MixedCase_1")
    user = await session.get(User, s.user_id)
    assert user is not None
    assert user.email == "mixed.case@example.com"
    assert user.handle == "mixedcase_1"
    assert user.password_hash is not None and "correct-horse" not in user.password_hash


async def test_duplicate_email_is_case_insensitive(client: AsyncClient) -> None:
    await signup(client, email="dup@example.com")
    response = await client.post("/api/auth/signup", json=signup_payload(email="DUP@Example.com"))
    assert response.status_code == 409
    assert response.json()["code"] == "email_taken"


async def test_duplicate_handle_is_case_insensitive(client: AsyncClient) -> None:
    await signup(client, handle="kindsoul")
    response = await client.post("/api/auth/signup", json=signup_payload(handle="KindSoul"))
    assert response.status_code == 409
    assert response.json()["code"] == "handle_taken"


@pytest.mark.parametrize("handle", ["admin", "Admin", "me", "support", "gooddeeds"])
async def test_reserved_handle_rejected(client: AsyncClient, handle: str) -> None:
    response = await client.post("/api/auth/signup", json=signup_payload(handle=handle))
    assert response.status_code == 422


@pytest.mark.parametrize(
    "handle",
    ["ab", "x" * 31, "has space", "dash-ed", "emoji😀x", "at@sign", ".lead", "trail.", "a..b"],
)
async def test_invalid_handle_rejected(client: AsyncClient, handle: str) -> None:
    response = await client.post("/api/auth/signup", json=signup_payload(handle=handle))
    assert response.status_code == 422


async def test_password_under_10_characters_rejected(client: AsyncClient) -> None:
    response = await client.post("/api/auth/signup", json=signup_payload(password="123456789"))
    assert response.status_code == 422
    ok = await client.post("/api/auth/signup", json=signup_payload(password="1234567890"))
    assert ok.status_code == 201


@pytest.mark.parametrize("field", ["accept_terms", "accept_privacy"])
async def test_consent_flags_must_be_true(client: AsyncClient, field: str) -> None:
    response = await client.post("/api/auth/signup", json=signup_payload(**{field: False}))
    assert response.status_code == 422
    missing = signup_payload()
    del missing[field]
    assert (await client.post("/api/auth/signup", json=missing)).status_code == 422


async def test_exact_18th_birthday_passes(client: AsyncClient) -> None:
    dob = years_ago(18)
    response = await client.post(
        "/api/auth/signup", json=signup_payload(date_of_birth=dob.isoformat())
    )
    assert response.status_code == 201, response.text


async def test_day_before_18th_birthday_fails(client: AsyncClient, session: AsyncSession) -> None:
    dob = years_ago(18) + timedelta(days=1)
    payload = signup_payload(date_of_birth=dob.isoformat())
    response = await client.post("/api/auth/signup", json=payload)
    assert response.status_code == 400
    assert response.json()["code"] == "underage"
    assert await session.scalar(select(User.id).where(User.email == payload["email"])) is None


def test_is_adult_boundaries() -> None:
    today = ist_today()
    assert service.is_adult(years_ago(18, today), today)
    assert not service.is_adult(years_ago(18, today) + timedelta(days=1), today)
    # Leap-day birthday: 18 on 1 March in a non-leap year, not on 28 February.
    assert not service.is_adult(date(2008, 2, 29), date(2026, 2, 28))
    assert service.is_adult(date(2008, 2, 29), date(2026, 3, 1))


class _FrozenDatetime(datetime):
    """20:30 UTC on 11 Nov 2026 is already 02:00 on 12 Nov in Bangalore."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> "_FrozenDatetime":
        frozen = cls(2026, 11, 11, 20, 30, tzinfo=UTC)
        return frozen.astimezone(tz) if tz else frozen


def test_age_uses_bangalore_date_not_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "datetime", _FrozenDatetime)
    assert service.is_adult(date(2008, 11, 12))  # 18th birthday today in IST
    assert not service.is_adult(date(2008, 11, 13))


async def test_signup_writes_user_registered_event(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)
    rows = (
        await session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == "identity.user_registered")
        )
    ).all()
    mine = [r for r in rows if r.payload.get("user_id") == str(s.user_id)]
    assert len(mine) == 1
    assert mine[0].payload["method"] == "password"


async def test_signup_records_current_consent_versions(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)
    rows = (await session.execute(select(Consent).where(Consent.user_id == s.user_id))).scalars()
    granted = {(c.consent_type, c.version) for c in rows if c.withdrawn_at is None}
    assert granted == set(service.CURRENT_CONSENTS.items())
    user = await session.get(User, s.user_id)
    assert user is not None and user.adult_declared_at is not None


async def test_signup_creates_doer_short_id_shown_only_after_onboarding(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await signup(client)
    code = await session.get(DoerCode, s.user_id)
    assert code is not None
    assert len(code.short_id) == 7
    me = (await client.get("/api/users/me", headers=s.headers)).json()
    assert me["short_id"] is None
    await onboard_phone(client, sms, s, valid_phone(5))
    me = (await client.get("/api/users/me", headers=s.headers)).json()
    assert me["short_id"] == f"GD-{code.short_id}"
