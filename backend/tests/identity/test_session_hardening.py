"""token_version, the refresh reuse grace window, absolute session lifetime,
device recording only after consent, and data retention."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    hash_password,
    keyed_hash,
    sha256_hex,
    verify_password,
)
from app.modules.identity import service
from app.modules.identity.models import PhoneOtp, RefreshToken, User, UserDevice
from tests.identity.helpers import (
    FakeOAuth,
    adult_dob,
    bearer,
    login,
    refresh,
    session_from,
    set_cookie,
    signup,
)

DEVICE = "device-aaaa-1111"


async def _token(session: AsyncSession, raw: str) -> RefreshToken:
    session.expire_all()
    token = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(raw))
    )
    assert token is not None
    return token


async def _active_count(session: AsyncSession, user_id: uuid.UUID) -> int:
    session.expire_all()
    rows = await session.scalars(
        select(RefreshToken.id).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    )
    return len(rows.all())


# --------------------------------------------------------------------------- token_version


async def test_replay_bumps_token_version_and_kills_issued_access_tokens(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)
    assert (await client.get("/api/users/me", headers=s.headers)).status_code == 200
    rotated = session_from(await refresh(client, s.refresh))
    assert (await client.get("/api/users/me", headers=rotated.headers)).status_code == 200

    replay = await refresh(client, s.refresh)  # theft: no device, so no grace
    assert replay.status_code == 401

    session.expire_all()
    user = await session.get(User, s.user_id)
    assert user is not None and user.token_version == 1
    for headers in (s.headers, rotated.headers):
        response = await client.get("/api/users/me", headers=headers)
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_token"

    # Logging in again issues tokens with the new version.
    assert s.email is not None
    fresh = session_from(await login(client, s.email))
    assert (await client.get("/api/users/me", headers=fresh.headers)).status_code == 200


async def test_access_token_with_stale_version_rejected(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)
    await session.execute(update(User).where(User.id == s.user_id).values(token_version=5))
    await session.commit()
    assert (await client.get("/api/users/me", headers=s.headers)).status_code == 401
    token, _ = create_access_token(s.user_id, "member", 5)
    assert (await client.get("/api/users/me", headers=bearer(token))).status_code == 200


async def test_revoke_all_sessions_service(client: AsyncClient, session: AsyncSession) -> None:
    s = await signup(client)
    assert s.email is not None
    await login(client, s.email)
    user = await session.get(User, s.user_id)
    assert user is not None
    await service.revoke_all_sessions(session, user)
    assert user.token_version == 1
    assert await _active_count(session, s.user_id) == 0


# --------------------------------------------------------------------------- grace window


async def test_reuse_within_grace_on_same_device_starts_sibling_session(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client, device_id=DEVICE)
    first = await refresh(client, s.refresh, DEVICE)
    assert first.status_code == 200
    sibling_tab = await refresh(client, s.refresh, DEVICE)  # second tab, same old cookie
    assert sibling_tab.status_code == 200, sibling_tab.text

    a, b = session_from(first), session_from(sibling_tab)
    assert a.refresh != b.refresh
    assert await _active_count(session, s.user_id) == 2
    user = await session.get(User, s.user_id)
    assert user is not None and user.token_version == 0
    # Nothing was revoked: both new sessions and the original access token still work.
    assert (await client.get("/api/users/me", headers=s.headers)).status_code == 200
    assert (await refresh(client, a.refresh, DEVICE)).status_code == 200
    assert (await refresh(client, b.refresh, DEVICE)).status_code == 200


@pytest.mark.parametrize("replay_device", ["device-bbbb-2222", None])
async def test_reuse_from_other_or_missing_device_revokes_everything(
    client: AsyncClient, session: AsyncSession, replay_device: str | None
) -> None:
    s = await signup(client, device_id=DEVICE)
    rotated = session_from(await refresh(client, s.refresh, DEVICE))
    replay = await refresh(client, s.refresh, replay_device)
    assert replay.status_code == 401
    assert await _active_count(session, s.user_id) == 0
    assert (await client.get("/api/users/me", headers=rotated.headers)).status_code == 401
    assert (await refresh(client, rotated.refresh)).status_code == 401


async def test_reuse_after_grace_window_revokes_everything(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client, device_id=DEVICE)
    rotated = session_from(await refresh(client, s.refresh, DEVICE))
    grace = get_settings().refresh_reuse_grace_seconds
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == sha256_hex(s.refresh))
        .values(revoked_at=datetime.now(UTC) - timedelta(seconds=grace + 1))
    )
    await session.commit()
    assert (await refresh(client, s.refresh, DEVICE)).status_code == 401
    assert await _active_count(session, s.user_id) == 0
    assert (await refresh(client, rotated.refresh)).status_code == 401


async def test_revoke_all_cannot_be_undone_within_grace(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client, device_id=DEVICE)
    rotated = session_from(await refresh(client, s.refresh, DEVICE))
    assert (await refresh(client, s.refresh, "attacker-device")).status_code == 401
    # The session revoked a moment ago by theft detection must stay dead.
    assert (await refresh(client, rotated.refresh, DEVICE)).status_code == 401
    assert await _active_count(session, s.user_id) == 0


async def test_grace_needs_the_device_recorded_on_the_token(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)  # no device header: token has no device hash
    await refresh(client, s.refresh, DEVICE)
    assert (await refresh(client, s.refresh, DEVICE)).status_code == 401
    assert await _active_count(session, s.user_id) == 0


async def test_logged_out_token_cannot_be_revived_within_grace(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client, device_id=DEVICE)
    client.cookies.clear()
    out = await client.post("/api/auth/logout", headers={"Cookie": f"gd_refresh={s.refresh}"})
    assert out.status_code == 204
    revived = await refresh(client, s.refresh, DEVICE)
    assert revived.status_code == 401
    assert await _active_count(session, s.user_id) == 0


# --------------------------------------------------------------------------- absolute lifetime


async def _age_session(session: AsyncSession, raw: str, age: timedelta) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == sha256_hex(raw))
        .values(session_started_at=datetime.now(UTC) - age)
    )
    await session.commit()


async def test_session_older_than_max_days_rejected(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)
    max_days = get_settings().session_max_days
    await _age_session(session, s.refresh, timedelta(days=max_days, minutes=1))
    token = await _token(session, s.refresh)
    assert token.expires_at > datetime.now(UTC)  # not expired, only too old
    response = await refresh(client, s.refresh)
    assert response.status_code == 401
    assert response.json()["code"] == "session_expired"
    cookie = set_cookie(response)
    assert cookie is not None and cookie.value == ""


async def test_session_just_under_max_days_still_refreshes(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)
    max_days = get_settings().session_max_days
    await _age_session(session, s.refresh, timedelta(days=max_days, minutes=-1))
    assert (await refresh(client, s.refresh)).status_code == 200


async def test_rotation_preserves_session_started_at(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client, device_id=DEVICE)
    await _age_session(session, s.refresh, timedelta(days=10))
    started = (await _token(session, s.refresh)).session_started_at

    rotated = session_from(await refresh(client, s.refresh, DEVICE))
    assert (await _token(session, rotated.refresh)).session_started_at == started
    sibling = session_from(await refresh(client, s.refresh, DEVICE))  # grace path
    assert (await _token(session, sibling.refresh)).session_started_at == started
    again = session_from(await refresh(client, rotated.refresh, DEVICE))
    assert (await _token(session, again.refresh)).session_started_at == started

    # A fresh login starts a new chain.
    assert s.email is not None
    fresh = session_from(await login(client, s.email))
    assert (await _token(session, fresh.refresh)).session_started_at > started


# --------------------------------------------------------------------------- device after consent


async def test_device_recorded_only_after_consent(
    client: AsyncClient, session: AsyncSession, oauth: FakeOAuth
) -> None:
    token = oauth.google.add("g-device")
    response = await client.post(
        "/api/auth/oauth/google", json={"token": token}, headers={"X-Device-Id": DEVICE}
    )
    assert response.status_code == 200
    s = session_from(response)
    assert await session.scalar(select(UserDevice).where(UserDevice.user_id == s.user_id)) is None
    assert (await _token(session, s.refresh)).device_hash is None

    consent = await client.post(
        "/api/users/me/age-consent",
        json={"date_of_birth": adult_dob(), "accept_terms": True, "accept_privacy": True},
        headers={**s.headers, "X-Device-Id": DEVICE},
    )
    assert consent.status_code == 200
    hashes = set(
        await session.scalars(select(UserDevice.device_hash).where(UserDevice.user_id == s.user_id))
    )
    assert hashes == {keyed_hash("device", DEVICE)}

    # Later sessions record the device on the refresh token too.
    again = session_from(
        await client.post(
            "/api/auth/oauth/google", json={"token": token}, headers={"X-Device-Id": DEVICE}
        )
    )
    assert (await _token(session, again.refresh)).device_hash == keyed_hash("device", DEVICE)


async def test_failed_age_consent_records_no_device(
    client: AsyncClient, session: AsyncSession, oauth: FakeOAuth
) -> None:
    s = session_from(
        await client.post("/api/auth/oauth/google", json={"token": oauth.google.add("g-dev2")})
    )
    response = await client.post(
        "/api/users/me/age-consent",
        json={"date_of_birth": "2015-01-01", "accept_terms": True, "accept_privacy": True},
        headers={**s.headers, "X-Device-Id": DEVICE},
    )
    assert response.status_code == 400
    assert await session.scalar(select(UserDevice).where(UserDevice.user_id == s.user_id)) is None


# --------------------------------------------------------------------------- retention


async def test_purge_expired_records(client: AsyncClient, session: AsyncSession) -> None:
    s = await signup(client)
    now = datetime.now(UTC)

    def otp(age: timedelta) -> PhoneOtp:
        return PhoneOtp(
            user_id=s.user_id,
            phone_e164="+919876543299",
            code_hash="x",
            attempts=0,
            expires_at=now - age + timedelta(minutes=10),
            created_at=now - age,
        )

    def token(
        *, expired_ago: timedelta | None = None, revoked_ago: timedelta | None = None
    ) -> RefreshToken:
        return RefreshToken(
            user_id=s.user_id,
            token_hash=uuid.uuid4().hex,
            session_started_at=now - timedelta(days=60),
            expires_at=now - expired_ago if expired_ago else now + timedelta(days=1),
            revoked_at=now - revoked_ago if revoked_ago else None,
        )

    old_otp, recent_otp = otp(timedelta(days=1, minutes=1)), otp(timedelta(hours=23))
    old_expired = token(expired_ago=timedelta(days=31))
    old_revoked = token(revoked_ago=timedelta(days=31))
    recent_expired = token(expired_ago=timedelta(days=29))
    recent_revoked = token(revoked_ago=timedelta(days=29))
    live = token()
    session.add_all(
        [old_otp, recent_otp, old_expired, old_revoked, recent_expired, recent_revoked, live]
    )
    await session.flush()

    keep_otp = recent_otp.id
    gone = {old_expired.id, old_revoked.id}
    kept = {recent_expired.id, recent_revoked.id, live.id}
    await service.purge_expired_records(session)
    session.expire_all()

    otp_ids = set(await session.scalars(select(PhoneOtp.id).where(PhoneOtp.user_id == s.user_id)))
    assert otp_ids == {keep_otp}
    token_ids = set(
        await session.scalars(select(RefreshToken.id).where(RefreshToken.user_id == s.user_id))
    )
    assert not gone & token_ids
    assert kept <= token_ids
    # The signup session is untouched.
    assert (await refresh(client, s.refresh)).status_code == 200


# --------------------------------------------------------------------------- password hashing


async def test_password_hashing_is_async_and_unknown_accounts_fail() -> None:
    hashed = await hash_password("a-long-password")
    assert await verify_password(hashed, "a-long-password")
    assert not await verify_password(hashed, "another-password")
    assert not await verify_password(None, "a-long-password")
    assert not await verify_password("not-an-argon2-hash", "a-long-password")
