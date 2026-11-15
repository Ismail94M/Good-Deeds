"""Scan rate limits: account and IP keys (D1) plus the manual per-target key (D11), §3.1.

Limits come from the `deeds` config, so each test publishes small values. They are applied
before any lookup, so even requests with bad tokens count.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.modules.deeds.models import QrScan
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
