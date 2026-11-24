"""D17: invites are switched off by config (``deeds.invites_enabled``, seeded false in v2).

While off, invite create, preview, accept and reissue return 403 ``invites_disabled`` after their
rate limits and before any lookup. ``/api/me/invites`` still lists existing invites and the
expiry job keeps running. Publishing ``invites_enabled=True`` turns everything back on.
"""

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import _invites
from app.modules.deeds._limits import load_limits
from app.modules.deeds.models import Deed, DeedStatus, InviteStatus
from tests.deeds.helpers import make_member, publish_limits
from tests.deeds.invite_helpers import create_invite, invite_body, invite_row

_UNKNOWN_TOKEN = "x" * 43


def _assert_disabled(response: Response) -> None:
    body = response.json()
    assert response.status_code == 403, body
    assert body["code"] == "invites_disabled"
    assert "switched off" in body["detail"]


async def test_invites_are_off_in_the_seeded_config(session: AsyncSession) -> None:
    _, limits = await load_limits(session)
    assert limits.invites_enabled is False


async def test_every_invite_endpoint_is_disabled_by_default(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer = await make_member(session), await make_member(session)

    _assert_disabled(await client.post("/api/deeds", json=invite_body(), headers=reporter.headers))
    preview = await client.get(f"/api/invites/by-token/{_UNKNOWN_TOKEN}")
    _assert_disabled(preview)
    # The token-in-URL headers are kept on the error too.
    assert preview.headers["cache-control"] == "no-store"
    assert preview.headers["referrer-policy"] == "no-referrer"
    _assert_disabled(
        await client.post(f"/api/invites/by-token/{_UNKNOWN_TOKEN}/accept", headers=doer.headers)
    )
    _assert_disabled(
        await client.post(f"/api/invites/{uuid.uuid4()}/reissue", headers=reporter.headers)
    )


async def test_existing_invite_is_unreachable_while_disabled_but_still_listed(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The check runs before any lookup: a real token gets the same 403 as an unknown one."""
    reporter, doer = await make_member(session), await make_member(session)
    created, token = await create_invite(session, reporter)  # service level: no config check
    await session.commit()
    assert created.invite is not None

    _assert_disabled(await client.get(f"/api/invites/by-token/{token}"))
    _assert_disabled(
        await client.post(f"/api/invites/by-token/{token}/accept", headers=doer.headers)
    )
    _assert_disabled(
        await client.post(f"/api/invites/{created.invite.id}/reissue", headers=reporter.headers)
    )
    row = await invite_row(session, created.id)
    assert row.status == InviteStatus.PENDING and row.accepted_by is None

    listed = await client.get("/api/me/invites", headers=reporter.headers)
    assert listed.status_code == 200, listed.text
    assert [i["id"] for i in listed.json()["items"]] == [str(created.invite.id)]


async def test_expiry_job_still_expires_invites_while_disabled(session: AsyncSession) -> None:
    reporter = await make_member(session)
    created, _ = await create_invite(session, reporter, at=datetime.now(UTC) - timedelta(days=8))
    assert await _invites.expire_invites(session) >= 1
    deed = await session.get(Deed, created.id, populate_existing=True)
    assert deed is not None and deed.status == DeedStatus.UNVERIFIED
    assert (await invite_row(session, created.id)).status == InviteStatus.EXPIRED


async def test_rate_limits_still_run_before_the_disabled_check(
    client: AsyncClient, session: AsyncSession
) -> None:
    await publish_limits(session, rate_invite_preview_ip="1/900")
    _assert_disabled(await client.get(f"/api/invites/by-token/{_UNKNOWN_TOKEN}"))
    second = await client.get(f"/api/invites/by-token/{_UNKNOWN_TOKEN}")
    assert second.status_code == 429 and second.json()["code"] == "rate_limited"


async def test_qr_path_is_unaffected(client: AsyncClient, session: AsyncSession) -> None:
    """Only the invite method of ``POST /api/deeds`` is switched off."""
    reporter = await make_member(session)
    body = {**invite_body(), "method": "qr_scan", "scan_id": str(uuid.uuid4())}
    for key in ("occurred_at", "location", "sent_via"):
        body.pop(key)
    response = await client.post("/api/deeds", json=body, headers=reporter.headers)
    assert response.status_code == 404 and response.json()["code"] == "scan_not_found"


async def test_publishing_invites_enabled_turns_them_back_on(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer = await make_member(session), await make_member(session)
    await publish_limits(session, invites_enabled=True)

    created = await client.post("/api/deeds", json=invite_body(), headers=reporter.headers)
    assert created.status_code == 201, created.text
    invite = created.json()["invite"]
    reissued = await client.post(f"/api/invites/{invite['id']}/reissue", headers=reporter.headers)
    assert reissued.status_code == 200, reissued.text
    token = reissued.json()["url"].rsplit("/", 1)[1]
    assert (await client.get(f"/api/invites/by-token/{token}")).status_code == 200
    accepted = await client.post(f"/api/invites/by-token/{token}/accept", headers=doer.headers)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "verified"

    # And off again with the next version.
    await publish_limits(session, invites_enabled=False)
    _assert_disabled(await client.post("/api/deeds", json=invite_body(), headers=reporter.headers))
