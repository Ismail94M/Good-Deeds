"""Per-IP / per-email / per-user / global request limits (429 `rate_limited`)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.config import get_settings, parse_rate
from app.core.errors import TooManyRequests
from app.core.ratelimit import InMemoryRateLimiter, RateLimitCounter
from app.core.sms import ConsoleSmsProvider
from app.main import app
from tests.identity.helpers import (
    PASSWORD,
    FakeOAuth,
    login,
    otp_from,
    refresh,
    signup,
    signup_payload,
    start_phone,
    valid_phone,
    verify_phone,
)


def _limit(monkeypatch: pytest.MonkeyPatch, name: str, spec: str) -> None:
    monkeypatch.setattr(get_settings(), name, spec)


def _limited(response: Response) -> bool:
    return response.status_code == 429 and response.json().get("code") == "rate_limited"


@pytest.fixture
async def other_ip(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """A second client on another IP, sharing the same overrides as `client`."""
    transport = ASGITransport(app=app, client=("203.0.113.9", 4321))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_login_limited_per_ip(
    client: AsyncClient, other_ip: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = await signup(client)
    assert s.email is not None
    _limit(monkeypatch, "rate_login_ip", "3/900")
    for i in range(3):
        response = await login(client, f"nobody{i}@example.com")
        assert response.status_code == 401
    # Over the limit even with the right password and a fresh email.
    assert _limited(await login(client, s.email))
    # Another IP is unaffected.
    assert (await login(other_ip, s.email)).status_code == 200


async def test_login_limited_per_email(
    client: AsyncClient,
    other_ip: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: InMemoryRateLimiter,
) -> None:
    s = await signup(client, email="target@example.com")
    _limit(monkeypatch, "rate_login_email", "2/900")
    assert (await login(client, "target@example.com", "wrong-password")).status_code == 401
    assert (await login(other_ip, "TARGET@example.com", "wrong-password")).status_code == 401
    # Third try on the same address (any case, any IP) is refused, even with the password.
    assert _limited(await login(other_ip, "Target@Example.com", PASSWORD))
    assert any(key.startswith("login:email:target@example.com") for key, _ in rate_limiter.counts)
    # Other accounts can still log in.
    other = await signup(client)
    assert other.email is not None
    assert (await login(client, other.email)).status_code == 200
    assert s.email == "target@example.com"


async def test_signup_limited_per_ip(
    client: AsyncClient, other_ip: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limit(monkeypatch, "rate_signup_ip", "2/3600")
    for _ in range(2):
        assert (await client.post("/api/auth/signup", json=signup_payload())).status_code == 201
    assert _limited(await client.post("/api/auth/signup", json=signup_payload()))
    # Another IP still works.
    assert (await other_ip.post("/api/auth/signup", json=signup_payload())).status_code == 201


async def test_oauth_limited_per_ip(
    client: AsyncClient, oauth: FakeOAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limit(monkeypatch, "rate_oauth_ip", "2/900")
    assert (
        await client.post("/api/auth/oauth/google", json={"token": "bad-token-1"})
    ).status_code == 401
    assert (
        await client.post("/api/auth/oauth/twitter", json={"token": "x" * 20})
    ).status_code == 400
    good = oauth.google.add("g-limited")
    assert _limited(await client.post("/api/auth/oauth/google", json={"token": good}))


async def test_refresh_limited_per_ip(
    client: AsyncClient, other_ip: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = await signup(client)
    _limit(monkeypatch, "rate_refresh_ip", "2/900")
    assert (await refresh(client, "garbage-1")).status_code == 401
    assert (await refresh(client, "garbage-2")).status_code == 401
    assert _limited(await refresh(client, s.refresh))
    # The limited request did not consume or revoke the real token.
    assert (await refresh(other_ip, s.refresh)).status_code == 200


async def test_phone_start_limited_per_ip(
    client: AsyncClient, other_ip: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limit(monkeypatch, "rate_phone_start_ip", "2/3600")
    users = [await signup(client) for _ in range(3)]
    assert (await start_phone(client, users[0], valid_phone(80))).status_code == 200
    assert (await start_phone(client, users[1], valid_phone(81))).status_code == 200
    assert _limited(await start_phone(client, users[2], valid_phone(82)))
    assert (await start_phone(other_ip, users[2], valid_phone(82))).status_code == 200


async def test_global_sms_budget(
    client: AsyncClient,
    other_ip: AsyncClient,
    sms: ConsoleSmsProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit(monkeypatch, "rate_sms_global", "2/3600")
    users = [await signup(client) for _ in range(3)]
    assert (await start_phone(client, users[0], valid_phone(83))).status_code == 200
    assert (await start_phone(other_ip, users[1], valid_phone(84))).status_code == 200
    blocked = await start_phone(other_ip, users[2], valid_phone(85))
    assert _limited(blocked)
    assert valid_phone(85) not in sms.sent


async def test_phone_verify_limited_per_user(
    client: AsyncClient, sms: ConsoleSmsProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limit(monkeypatch, "rate_phone_verify_user", "2/3600")
    s = await signup(client)
    other = await signup(client)
    phone = valid_phone(86)
    await start_phone(client, s, phone)
    code = otp_from(sms, phone)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    assert (await verify_phone(client, s, phone, wrong)).status_code == 400
    assert (await verify_phone(client, s, phone, wrong)).status_code == 400
    assert _limited(await verify_phone(client, s, phone, code))

    # Another user is unaffected.
    await start_phone(client, other, valid_phone(87))
    ok = await verify_phone(client, other, valid_phone(87), otp_from(sms, valid_phone(87)))
    assert ok.status_code == 200


def test_parse_rate() -> None:
    assert parse_rate("20/900") == (20, 900)
    settings = get_settings()
    for name in (
        "rate_login_ip",
        "rate_login_email",
        "rate_signup_ip",
        "rate_phone_start_ip",
        "rate_sms_global",
        "rate_phone_verify_user",
        "rate_refresh_ip",
        "rate_oauth_ip",
    ):
        count, seconds = parse_rate(getattr(settings, name))
        assert count > 0 and seconds > 0


# --------------------------------------------------------------------------- Postgres counter


async def test_increment_upserts_and_counts(session: AsyncSession) -> None:
    now = 1_800_000_030.0  # window of 60 s starts at 1_800_000_000
    assert await ratelimit.increment(session, "test:k1", 60, now) == 1
    assert await ratelimit.increment(session, "test:k1", 60, now + 29) == 2
    assert await ratelimit.increment(session, "test:k1", 60, now + 29.9) == 3
    # Another key has its own counter.
    assert await ratelimit.increment(session, "test:k2", 60, now) == 1
    # The next window starts again at 1.
    assert await ratelimit.increment(session, "test:k1", 60, now + 30) == 1

    rows = (
        await session.scalars(
            select(RateLimitCounter)
            .where(RateLimitCounter.key == "test:k1")
            .order_by(RateLimitCounter.window_start)
        )
    ).all()
    assert [(r.window_start, r.count) for r in rows] == [
        (datetime.fromtimestamp(1_800_000_000, UTC), 3),
        (datetime.fromtimestamp(1_800_000_060, UTC), 1),
    ]


async def test_purge_old_counters(session: AsyncSession) -> None:
    now = datetime.now(UTC).timestamp()
    await ratelimit.increment(session, "test:old", 60, now - 2 * 86400)
    await ratelimit.increment(session, "test:new", 60, now)
    await ratelimit.purge_old(session, older_than=timedelta(days=1))
    keys = set(
        await session.scalars(
            select(RateLimitCounter.key).where(RateLimitCounter.key.like("test:%"))
        )
    )
    assert keys == {"test:new"}


async def test_in_memory_limiter_matches_semantics() -> None:
    limiter = InMemoryRateLimiter()
    await limiter.hit("k", "2/86400")
    await limiter.hit("k", "2/86400")
    with pytest.raises(TooManyRequests) as exc:
        await limiter.hit("k", "2/86400")
    assert exc.value.code == "rate_limited"
    await limiter.hit("other", "2/86400")
