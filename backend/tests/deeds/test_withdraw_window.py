"""The 24-hour withdrawal window (docs/M4-DESIGN.md D64, §3.3 "Changed elsewhere", §8).

A verified deed can be withdrawn only before ``verified_at + withdraw_window_hours`` (exclusive),
checked under the deed row lock with server time; a pending invite can be cancelled at any time.
Edits stay allowed after the window.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict
from app.modules.deeds import service as deeds_service
from app.modules.deeds.models import Deed, DeedStatus, InviteStatus
from tests.deeds.helpers import make_member, members, outbox, publish_limits, scan_and_report
from tests.deeds.invite_helpers import create_invite, invite_row, withdraw

WINDOW = timedelta(hours=24)


async def _verified_at(session: AsyncSession, deed_id: uuid.UUID) -> datetime:
    deed = await session.get(Deed, deed_id, populate_existing=True)
    assert deed is not None and deed.verified_at is not None
    return deed.verified_at


async def _status(session: AsyncSession, deed_id: uuid.UUID) -> DeedStatus:
    deed = await session.get(Deed, deed_id, populate_existing=True)
    assert deed is not None
    return deed.status


async def test_withdraw_one_second_before_the_window_ends_is_allowed(
    session: AsyncSession,
) -> None:
    witness, doer = await members(session, 2)
    deed = await scan_and_report(session, witness, doer, at=datetime.now(UTC) - timedelta(days=2))
    verified_at = await _verified_at(session, deed.id)

    out = await withdraw(session, witness, deed.id, at=verified_at + WINDOW - timedelta(seconds=1))

    assert out.status == "withdrawn"
    assert out.can_withdraw is False and out.withdrawable_until is None
    assert len(await outbox(session, "deeds.deed_withdrawn", deed.id)) == 1


async def test_withdraw_at_exactly_24_hours_is_refused(session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    deed = await scan_and_report(session, witness, doer, at=datetime.now(UTC) - timedelta(days=2))
    verified_at = await _verified_at(session, deed.id)

    for late in (WINDOW, WINDOW + timedelta(seconds=1), timedelta(days=30)):
        with pytest.raises(Conflict) as caught:
            await withdraw(session, witness, deed.id, at=verified_at + late)
        assert caught.value.code == "withdraw_window_closed"
        assert "24 hours" in caught.value.detail
    assert await _status(session, deed.id) == DeedStatus.VERIFIED
    assert await outbox(session, "deeds.deed_withdrawn", deed.id) == []


async def test_window_comes_from_config(session: AsyncSession) -> None:
    await publish_limits(session, withdraw_window_hours=1)
    assert await deeds_service.withdraw_window_hours(session) == 1
    witness, doer = await members(session, 2)
    late = await scan_and_report(session, witness, doer, at=datetime.now(UTC) - timedelta(hours=3))
    other = await make_member(session)
    early = await scan_and_report(session, other, doer, at=datetime.now(UTC) - timedelta(hours=3))
    late_at, early_at = await _verified_at(session, late.id), await _verified_at(session, early.id)

    with pytest.raises(Conflict) as caught:
        await withdraw(session, witness, late.id, at=late_at + timedelta(hours=1))
    assert caught.value.code == "withdraw_window_closed"
    assert "1 hours" in caught.value.detail
    out = await withdraw(session, other, early.id, at=early_at + timedelta(minutes=59))
    assert out.status == "withdrawn"


async def test_api_shows_the_window_and_refuses_after_it(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await members(session, 2)
    fresh = await scan_and_report(session, witness, doer)
    other = await make_member(session)
    old = await scan_and_report(session, other, doer, at=datetime.now(UTC) - timedelta(hours=25))
    await session.commit()
    fresh_at, old_at = await _verified_at(session, fresh.id), await _verified_at(session, old.id)

    body = (await client.get(f"/api/deeds/{fresh.id}", headers=witness.headers)).json()
    assert body["can_withdraw"] is True and body["can_edit"] is True
    assert datetime.fromisoformat(body["withdrawable_until"]) == fresh_at + WINDOW
    # The doer sees the same end of the window, but can never withdraw.
    as_doer = (await client.get(f"/api/deeds/{fresh.id}", headers=doer.headers)).json()
    assert as_doer["can_withdraw"] is False
    assert datetime.fromisoformat(as_doer["withdrawable_until"]) == fresh_at + WINDOW

    body = (await client.get(f"/api/deeds/{old.id}", headers=other.headers)).json()
    assert body["can_withdraw"] is False and body["can_edit"] is True
    assert datetime.fromisoformat(body["withdrawable_until"]) == old_at + WINDOW
    listed = await client.get("/api/me/deeds?role=reporter", headers=other.headers)
    [item] = [d for d in listed.json()["items"] if d["id"] == str(old.id)]
    assert item["can_withdraw"] is False and item["withdrawable_until"] is not None

    response = await client.post(f"/api/deeds/{old.id}/withdraw", headers=other.headers)
    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": "You can no longer withdraw this deed. It was verified more than 24 hours ago.",
        "code": "withdraw_window_closed",
    }
    assert await _status(session, old.id) == DeedStatus.VERIFIED

    # Edits still update the text after the window.
    edited = await client.patch(
        f"/api/deeds/{old.id}",
        json={"description": "Carried shopping bags up the stairs for a neighbour"},
        headers=other.headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["description"] == "Carried shopping bags up the stairs for a neighbour"
    assert edited.json()["can_withdraw"] is False

    # Not the reporter: still 404, not the window error.
    stranger = await make_member(session)
    response = await client.post(f"/api/deeds/{old.id}/withdraw", headers=stranger.headers)
    assert response.status_code == 404


@pytest.mark.usefixtures("invites_on")
async def test_pending_invite_can_be_cancelled_days_later(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter = await make_member(session)
    created, _ = await create_invite(session, reporter, at=datetime.now(UTC) - timedelta(days=6))
    await session.commit()
    assert created.withdrawable_until is None and created.can_withdraw is True

    body = (await client.get(f"/api/deeds/{created.id}", headers=reporter.headers)).json()
    assert body["status"] == "pending"
    assert body["withdrawable_until"] is None and body["can_withdraw"] is True

    response = await client.post(f"/api/deeds/{created.id}/withdraw", headers=reporter.headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "withdrawn"
    assert (await invite_row(session, created.id)).status == InviteStatus.CANCELLED
