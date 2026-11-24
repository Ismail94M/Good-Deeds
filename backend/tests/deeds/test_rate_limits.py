"""Scan rate limits: account and IP keys (D1) plus the manual per-target key (D11), §3.1.

Limits come from the `deeds` config, so each test publishes small values. They are applied
before any lookup, so even requests with bad tokens count.
"""

import itertools
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TooManyRequests
from app.core.ratelimit import InMemoryRateLimiter
from app.main import app
from app.modules.deeds import _scans
from app.modules.deeds._limits import load_limits
from app.modules.deeds.models import QrScan
from app.modules.deeds.schemas import Location, ScanCreate
from tests.deeds.helpers import (
    Member,
    code_for,
    count,
    location,
    make_member,
    members,
    publish_limits,
    token_for,
)


@asynccontextmanager
async def client_at(ip: str) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, client=(ip, 1234))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _limited(response: Response) -> bool:
    return response.status_code == 429 and response.json().get("code") == "rate_limited"


async def _qr(c: AsyncClient, who: Member, token: str) -> Response:
    return await c.post(
        "/api/scans", json={"token": token, "location": location()}, headers=who.headers
    )


async def _manual(c: AsyncClient, who: Member, short_id: str, code: str) -> Response:
    body: dict[str, Any] = {"short_id": short_id, "code": code, "location": location()}
    return await c.post("/api/scans", json=body, headers=who.headers)


async def test_scan_account_limit_holds_across_ips(
    client: AsyncClient, other_ip: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await members(session, 2)
    await publish_limits(session, rate_scan_witness="2/3600")
    for _ in range(2):
        assert (await _qr(client, witness, "bad-token")).status_code == 404
    # Over the limit on a fresh IP and with a valid token: nothing is looked up or saved.
    assert _limited(await _qr(other_ip, witness, await token_for(session, doer)))
    assert await count(session, QrScan, QrScan.witness_user_id == witness.user_id) == 0
    # The manual-entry counters are separate.
    manual = await _manual(other_ip, witness, doer.short_id, await code_for(session, doer))
    assert manual.status_code == 201, manual.text


async def test_scan_ip_limit_holds_across_accounts(
    client: AsyncClient, other_ip: AsyncClient, session: AsyncSession
) -> None:
    a, b, c, doer = await members(session, 4)
    await publish_limits(session, rate_scan_ip="2/3600")
    assert (await _qr(client, a, "bad-token")).status_code == 404
    assert (await _qr(client, b, "bad-token")).status_code == 404
    assert _limited(await _qr(client, c, await token_for(session, doer)))
    # The same account from another IP is fine.
    assert (await _qr(other_ip, c, await token_for(session, doer))).status_code == 201


async def test_manual_account_limit_holds_across_ips(
    client: AsyncClient, other_ip: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await members(session, 2)
    await publish_limits(session, rate_manual_witness="2/900")
    for _ in range(2):
        assert (await _manual(client, witness, doer.short_id, "000000")).status_code == 404
    response = await _manual(other_ip, witness, doer.short_id, await code_for(session, doer))
    assert _limited(response)
    # QR scanning has its own counters.
    assert (await _qr(other_ip, witness, await token_for(session, doer))).status_code == 201


async def test_manual_ip_limit_holds_across_accounts(
    client: AsyncClient, other_ip: AsyncClient, session: AsyncSession
) -> None:
    a, b, c, doer = await members(session, 4)
    await publish_limits(session, rate_manual_ip="2/900")
    code = await code_for(session, doer)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    assert (await _manual(client, a, "GD-ZZZZZZZ", wrong)).status_code == 404
    assert (await _manual(client, b, "GD-YYYYYYY", wrong)).status_code == 404
    assert _limited(await _manual(client, c, doer.short_id, code))
    assert (await _manual(other_ip, c, doer.short_id, code)).status_code == 201


async def test_manual_target_limit_holds_across_witnesses_and_ips(
    session: AsyncSession, client: AsyncClient
) -> None:
    doer = await make_member(session)
    w1, w2, w3, w4 = await members(session, 4)
    await publish_limits(session, rate_manual_target="2/900")
    code = await code_for(session, doer)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    # Different spellings of the same ID share one counter.
    loose = f"gd-{doer.short_id.lower()}"
    async with client_at("198.51.100.10") as c1, client_at("198.51.100.11") as c2:
        assert (await _manual(c1, w1, doer.short_id, wrong)).status_code == 404
        assert (await _manual(c2, w2, loose, wrong)).status_code == 404
    async with client_at("198.51.100.12") as c3:
        # A third guess at this ID is refused even with the right code.
        assert _limited(await _manual(c3, w3, doer.short_id, code))
        # Another target is unaffected.
        other = await make_member(session)
        ok = await _manual(c3, w4, other.short_id, await code_for(session, other))
        assert ok.status_code == 201, ok.text


async def test_manual_target_has_a_15_minute_and_a_daily_limit(session: AsyncSession) -> None:
    """Security L4: typed codes for one ID are limited per 15 minutes (``rate_manual_target``,
    20/900) and per day (``rate_manual_target_daily``, 60/86400), across witnesses and IPs.
    Seeded values, with a clock the test moves."""
    _, limits = await load_limits(session)
    assert (limits.rate_manual_target, limits.rate_manual_target_daily) == ("20/900", "60/86400")
    day_start = 1_800_000_000 - (1_800_000_000 % 86_400)  # a UTC midnight
    now = float(day_start + 1)
    limiter = InMemoryRateLimiter(clock=lambda: now)
    ips = itertools.count()
    data = ScanCreate(
        short_id="GD-ABCDEFG", code="123456", location=Location.model_validate(location())
    )

    async def guess() -> bool:
        """One typed code from a new witness on a new IP; True if it got past the limits."""
        n = next(ips)
        ip = f"10.0.{n // 250}.{n % 250}"
        try:
            await _scans.enforce_rate_limits(limiter, limits, data, uuid.uuid4(), ip)
        except TooManyRequests:
            return False
        return True

    for window in range(3):  # three 15-minute windows: 20 allowed in each, the 21st refused
        now = float(day_start + 1 + window * 900)
        assert [await guess() for _ in range(20)] == [True] * 20
        assert await guess() is False
    # 60 counted today: a fresh 15-minute window is still refused by the daily limit ...
    now = float(day_start + 1 + 3 * 900)
    assert await guess() is False
    # ... until the next day.
    now = float(day_start + 86_400 + 1)
    assert await guess() is True
