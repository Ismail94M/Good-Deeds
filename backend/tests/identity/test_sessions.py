"""Login, refresh-token rotation, logout, access-token checks and device recording."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token, keyed_hash, sha256_hex
from app.modules.identity.models import RefreshToken, User, UserDevice, UserStatus
from tests.identity.helpers import (
    PASSWORD,
    bearer,
    login,
    refresh,
    session_from,
    set_cookie,
    signup,
)

# --------------------------------------------------------------------------- login


async def test_login_success(client: AsyncClient) -> None:
    s = await signup(client, email="login.ok@example.com")
    response = await login(client, "LOGIN.OK@example.com")
    assert response.status_code == 200, response.text
    new = session_from(response)
    assert new.user_id == s.user_id
    assert new.refresh != s.refresh


async def test_wrong_password_and_unknown_email_look_the_same(client: AsyncClient) -> None:
    await signup(client, email="victim@example.com")
    wrong = await login(client, "victim@example.com", "not-the-password")
    unknown = await login(client, "nobody-here@example.com", PASSWORD)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert set_cookie(wrong) is None and set_cookie(unknown) is None


async def test_oauth_only_user_cannot_password_login(
    client: AsyncClient, session: AsyncSession
) -> None:
    session.add(User(email="oauthonly@example.com", display_name="O", handle="oauthonly"))
    await session.commit()
    response = await login(client, "oauthonly@example.com", PASSWORD)
    assert response.status_code == 401
    assert response.json()["code"] == "bad_credentials"


async def test_suspended_user_cannot_log_in(client: AsyncClient, session: AsyncSession) -> None:
    s = await signup(client)
    await session.execute(
        update(User).where(User.id == s.user_id).values(status=UserStatus.SUSPENDED)
    )
    await session.commit()
    assert s.email is not None
    response = await login(client, s.email)
    assert response.status_code == 403
    assert response.json()["code"] == "account_inactive"
    # An existing access token stops working too.
    assert (await client.get("/api/users/me", headers=s.headers)).status_code == 403


# --------------------------------------------------------------------------- refresh


async def test_refresh_rotates_and_old_cookie_stops_working(client: AsyncClient) -> None:
    s = await signup(client)
    first = await refresh(client, s.refresh)
    assert first.status_code == 200, first.text
    rotated = session_from(first)
    assert rotated.user_id == s.user_id
    assert rotated.refresh != s.refresh

    replay = await refresh(client, s.refresh)
    assert replay.status_code == 401
    assert replay.json()["code"] == "session_expired"


async def test_replayed_refresh_token_revokes_all_user_sessions(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)
    assert s.email is not None
    other_device = session_from(await login(client, s.email))
    rotated = session_from(await refresh(client, s.refresh))

    # Someone replays the stolen, already-rotated token.
    assert (await refresh(client, s.refresh)).status_code == 401

    active = await session.scalar(
        select(RefreshToken.id).where(
            RefreshToken.user_id == s.user_id, RefreshToken.revoked_at.is_(None)
        )
    )
    assert active is None
    assert (await refresh(client, rotated.refresh)).status_code == 401
    assert (await refresh(client, other_device.refresh)).status_code == 401


async def test_replay_does_not_touch_other_users(client: AsyncClient) -> None:
    attacker_target = await signup(client)
    bystander = await signup(client)
    await refresh(client, attacker_target.refresh)
    await refresh(client, attacker_target.refresh)
    assert (await refresh(client, bystander.refresh)).status_code == 200


async def test_expired_refresh_token_rejected(client: AsyncClient, session: AsyncSession) -> None:
    s = await signup(client)
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == sha256_hex(s.refresh))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    response = await refresh(client, s.refresh)
    assert response.status_code == 401
    assert response.json()["code"] == "session_expired"


async def test_refresh_without_cookie_or_with_garbage(client: AsyncClient) -> None:
    client.cookies.clear()
    assert (await client.post("/api/auth/refresh")).status_code == 401
    assert (await refresh(client, "not-a-real-token")).status_code == 401


async def test_refresh_for_suspended_user_rejected(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)
    await session.execute(
        update(User).where(User.id == s.user_id).values(status=UserStatus.SUSPENDED)
    )
    await session.commit()
    assert (await refresh(client, s.refresh)).status_code == 403


async def test_refresh_token_stored_only_as_hash(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)
    hashes = set(
        await session.scalars(
            select(RefreshToken.token_hash).where(RefreshToken.user_id == s.user_id)
        )
    )
    assert hashes == {sha256_hex(s.refresh)}
    assert s.refresh not in hashes


async def test_failed_refresh_clears_cookie(client: AsyncClient) -> None:
    s = await signup(client)
    await refresh(client, s.refresh)
    response = await refresh(client, s.refresh)
    assert response.status_code == 401
    cookie = set_cookie(response)
    assert cookie is not None
    assert cookie["path"] == "/api/auth"
    assert cookie.value == "" or cookie["max-age"] == "0"


async def test_logout_revokes_refresh_token_and_clears_cookie(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)
    client.cookies.clear()
    response = await client.post("/api/auth/logout", headers={"Cookie": f"gd_refresh={s.refresh}"})
    assert response.status_code == 204
    cookie = set_cookie(response)
    assert cookie is not None and cookie["path"] == "/api/auth"

    token = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(s.refresh))
    )
    assert token is not None and token.revoked_at is not None
    assert (await refresh(client, s.refresh)).status_code == 401


async def test_logout_without_cookie_is_noop(client: AsyncClient) -> None:
    client.cookies.clear()
    assert (await client.post("/api/auth/logout")).status_code == 204


# --------------------------------------------------------------------------- access tokens


def _forge(claims: dict[str, object], secret: str | None = None) -> str:
    return jwt.encode(claims, secret or get_settings().secret_key, algorithm="HS256")


async def test_access_token_missing_garbage_expired_wrong_type(client: AsyncClient) -> None:
    s = await signup(client)
    now = datetime.now(UTC)
    sub = str(s.user_id)
    bad_headers: list[dict[str, str]] = [
        {},
        bearer("garbage"),
        {"Authorization": "Basic dXNlcjpwYXNz"},
        bearer(_forge({"sub": sub, "type": "access", "exp": now - timedelta(seconds=1)})),
        bearer(_forge({"sub": sub, "type": "refresh", "exp": now + timedelta(minutes=5)})),
        bearer(_forge({"sub": sub, "exp": now + timedelta(minutes=5)})),
        bearer(_forge({"sub": sub, "type": "access"})),  # no exp
        bearer(
            _forge(
                {"sub": sub, "type": "access", "exp": now + timedelta(minutes=5)},
                secret="some-other-secret-of-sufficient-length-xx",
            )
        ),
        bearer(
            jwt.encode(
                {"sub": sub, "type": "access", "exp": now + timedelta(minutes=5)},
                key="",
                algorithm="none",
            )
        ),
        bearer(_forge({"sub": "not-a-uuid", "type": "access", "exp": now + timedelta(minutes=5)})),
        bearer(_forge({"type": "access", "exp": now + timedelta(minutes=5)})),  # no sub
    ]
    for headers in bad_headers:
        response = await client.get("/api/users/me", headers=headers)
        assert response.status_code == 401, (headers, response.text)
    assert (await client.get("/api/users/me", headers=s.headers)).status_code == 200


async def test_access_token_for_unknown_user_rejected(client: AsyncClient) -> None:
    token, _ = create_access_token(uuid.uuid4(), "member", 0)
    assert (await client.get("/api/users/me", headers=bearer(token))).status_code == 401


# --------------------------------------------------------------------------- devices


async def test_device_id_stored_only_as_keyed_hash(
    client: AsyncClient, session: AsyncSession
) -> None:
    raw = f"device-{uuid.uuid4()}"
    s = await signup(client, device_id=raw)
    devices = (
        await session.scalars(select(UserDevice).where(UserDevice.user_id == s.user_id))
    ).all()
    assert [d.device_hash for d in devices] == [keyed_hash("device", raw)]
    assert all(raw not in d.device_hash for d in devices)
    token_devices = set(
        await session.scalars(
            select(RefreshToken.device_hash).where(RefreshToken.user_id == s.user_id)
        )
    )
    assert token_devices == {keyed_hash("device", raw)}


async def test_same_device_login_updates_last_seen(
    client: AsyncClient, session: AsyncSession
) -> None:
    raw = f"device-{uuid.uuid4()}"
    s = await signup(client, device_id=raw)
    assert s.email is not None
    old = datetime(2026, 1, 1, tzinfo=UTC)
    await session.execute(
        update(UserDevice)
        .where(UserDevice.user_id == s.user_id)
        .values(first_seen_at=old, last_seen_at=old)
    )
    await session.commit()
    assert (await login(client, s.email, device_id=raw)).status_code == 200
    session.expire_all()
    devices = (
        await session.scalars(select(UserDevice).where(UserDevice.user_id == s.user_id))
    ).all()
    assert len(devices) == 1
    assert devices[0].first_seen_at == old
    assert devices[0].last_seen_at > old


async def test_no_device_header_records_nothing(client: AsyncClient, session: AsyncSession) -> None:
    s = await signup(client)
    assert await session.scalar(select(UserDevice).where(UserDevice.user_id == s.user_id)) is None


async def test_oversized_device_header_rejected(client: AsyncClient) -> None:
    s = await signup(client)
    assert s.email is not None
    response = await login(client, s.email, device_id="x" * 101)
    assert response.status_code == 422
