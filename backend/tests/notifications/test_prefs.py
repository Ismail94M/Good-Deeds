"""Preferences and mutes, N5 to N7 (docs/M6-DESIGN.md §3.2, D107, D113)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import UserStatus
from app.modules.notifications._write import Outcome
from app.modules.notifications.models import NotificationKind
from tests.deeds.helpers import Member, make_member, members
from tests.feed.helpers import set_post, verified_deed, withdraw_directly
from tests.notifications.helpers import publish_notifications, rename, send, set_status

K = NotificationKind
PREFS = "/api/notifications/preferences"
MUTES = "/api/notifications/mutes"


async def _prefs(client: AsyncClient, member: Member) -> dict[str, Any]:
    response = await client.get(PREFS, headers=member.headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _mute(client: AsyncClient, member: Member, kind: str, target: str) -> int:
    response = await client.put(
        MUTES, headers=member.headers, json={"type": kind, "target": target}
    )
    return response.status_code


async def _unmute(client: AsyncClient, member: Member, kind: str, target: str) -> int:
    response = await client.request(
        "DELETE", MUTES, headers=member.headers, json={"type": kind, "target": target}
    )
    return response.status_code


async def test_defaults_and_locked_categories(client: AsyncClient, session: AsyncSession) -> None:
    member = await make_member(session)
    body = await _prefs(client, member)
    assert [c["id"] for c in body["categories"]] == [
        "about_you",
        "network",
        "activity",
        "account",
        "reviews",
    ]
    assert all(c["on"] for c in body["categories"])
    locked = {c["id"] for c in body["categories"] if c["locked"]}
    assert locked == {"account", "reviews"}
    assert all(c["label"] and c["description"] for c in body["categories"])
    assert body["mutes"] == {"people": [], "deeds": []}


async def test_switching_a_category_off_and_on(client: AsyncClient, session: AsyncSession) -> None:
    doer, reporter, liker = await members(session, 3)
    deed = await verified_deed(session, reporter, doer)
    response = await client.put(
        PREFS, headers=doer.headers, json={"category": "activity", "on": False}
    )
    assert response.status_code == 200
    activity = next(c for c in response.json()["categories"] if c["id"] == "activity")
    assert activity["on"] is False
    assert await send(session, K.LIKE, doer, actor=liker, deed_id=deed) == Outcome.CATEGORY_OFF
    response = await client.put(
        PREFS, headers=doer.headers, json={"category": "activity", "on": True}
    )
    assert response.status_code == 200
    assert await send(session, K.LIKE, doer, actor=liker, deed_id=deed) == Outcome.CREATED


async def test_locked_and_unknown_categories(client: AsyncClient, session: AsyncSession) -> None:
    member = await make_member(session)
    for category in ("account", "reviews"):
        response = await client.put(
            PREFS, headers=member.headers, json={"category": category, "on": False}
        )
        assert response.status_code == 409 and response.json()["code"] == "category_locked"
        # Switching a locked category on is a no-op, not an error.
        response = await client.put(
            PREFS, headers=member.headers, json={"category": category, "on": True}
        )
        assert response.status_code == 200
    response = await client.put(
        PREFS, headers=member.headers, json={"category": "fraud", "on": False}
    )
    assert response.status_code == 422 and response.json()["code"] == "unknown_category"


async def test_mute_and_unmute_a_person(client: AsyncClient, session: AsyncSession) -> None:
    me = await make_member(session)
    other = await rename(session, await make_member(session), "Kavya")
    assert await _mute(client, me, "person", other.handle) == 204
    assert await _mute(client, me, "person", other.handle.upper()) == 204  # idempotent
    body = await _prefs(client, me)
    assert body["mutes"]["people"] == [
        {"handle": other.handle, "display_name": "Kavya", "avatar": None}
    ]
    assert await send(session, K.FOLLOW, me, actor=other) == Outcome.MUTED
    assert await _unmute(client, me, "person", other.handle) == 204
    assert await _unmute(client, me, "person", other.handle) == 204
    assert (await _prefs(client, me))["mutes"]["people"] == []
    assert await send(session, K.FOLLOW, me, actor=other) == Outcome.CREATED


async def test_unknown_or_inactive_person_is_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    me, gone = await members(session, 2)
    assert await _mute(client, me, "person", "no-such-handle") == 404
    await set_status(session, gone, UserStatus.SUSPENDED)
    assert await _mute(client, me, "person", gone.handle) == 404


async def test_unmute_answers_the_same_for_unknown_and_suspended_handles(
    client: AsyncClient, session: AsyncSession
) -> None:
    """SEC6-L4: no account-state oracle. Unmuting someone you muted works whatever their state
    is now; otherwise an unknown and a suspended handle both get the same 404."""
    me, gone, muted_then_gone = await members(session, 3)
    assert await _mute(client, me, "person", muted_then_gone.handle) == 204
    await set_status(session, gone, UserStatus.SUSPENDED)
    await set_status(session, muted_then_gone, UserStatus.SUSPENDED)
    await session.commit()  # a refused request rolls back uncommitted work
    unknown = await client.request(
        "DELETE", MUTES, headers=me.headers, json={"type": "person", "target": "no-such-handle"}
    )
    suspended = await client.request(
        "DELETE", MUTES, headers=me.headers, json={"type": "person", "target": gone.handle}
    )
    assert unknown.status_code == suspended.status_code == 404
    assert unknown.json() == suspended.json()
    assert await _unmute(client, me, "person", muted_then_gone.handle) == 204
    assert await _unmute(client, me, "person", muted_then_gone.handle) == 404


async def test_mute_a_deed(client: AsyncClient, session: AsyncSession) -> None:
    doer = await rename(session, await make_member(session), "Ahmed")
    reporter, viewer, liker = await members(session, 3)
    deed = await verified_deed(session, reporter, doer)
    assert await _mute(client, viewer, "deed", str(deed)) == 204
    [muted] = (await _prefs(client, viewer))["mutes"]["deeds"]
    assert muted == {"deed_id": str(deed), "title_text": "Animals: the deed about Ahmed"}
    assert await _mute(client, doer, "deed", str(deed)) == 204
    assert (await _prefs(client, doer))["mutes"]["deeds"][0]["title_text"] == (
        "Animals: the deed about you"
    )
    assert await send(session, K.LIKE, doer, actor=liker, deed_id=deed) == Outcome.MUTED
    # Once the deed is gone it reads "no longer available" and can still be unmuted.
    await withdraw_directly(session, deed)
    [muted] = (await _prefs(client, viewer))["mutes"]["deeds"]
    assert muted["title_text"] == "This deed is no longer available"
    assert await _unmute(client, viewer, "deed", str(deed)) == 204
    assert (await _prefs(client, viewer))["mutes"]["deeds"] == []


async def test_a_deed_you_cant_see_is_404(client: AsyncClient, session: AsyncSession) -> None:
    doer, reporter, viewer = await members(session, 3)
    hidden = await verified_deed(session, reporter, doer)
    await set_post(session, hidden, hidden_by_reporter_at=datetime.now(UTC))
    await session.commit()  # a refused request rolls back uncommitted work
    assert await _mute(client, viewer, "deed", str(hidden)) == 404
    assert await _mute(client, viewer, "deed", str(uuid.uuid4())) == 404
    assert await _mute(client, viewer, "deed", "not-a-uuid") == 404
    assert await _unmute(client, viewer, "deed", str(uuid.uuid4())) == 404
    # Its doer can still see it (published, hidden by the reporter), so may mute it.
    assert await _mute(client, doer, "deed", str(hidden)) == 204


async def test_mute_cap(client: AsyncClient, session: AsyncSession) -> None:
    await publish_notifications(session, max_mutes=2)
    me, a, b, c = await members(session, 4)
    assert await _mute(client, me, "person", a.handle) == 204
    assert await _mute(client, me, "person", b.handle) == 204
    assert await _mute(client, me, "person", b.handle) == 204  # already muted: fine
    response = await client.put(
        MUTES, headers=me.headers, json={"type": "person", "target": c.handle}
    )
    assert response.status_code == 409 and response.json()["code"] == "too_many_mutes"


async def test_mutes_are_own_data(client: AsyncClient, session: AsyncSession) -> None:
    me, other, target = await members(session, 3)
    assert await _mute(client, other, "person", target.handle) == 204
    assert (await _prefs(client, me))["mutes"]["people"] == []
    response = await client.put(MUTES, headers=me.headers, json={"type": "group", "target": "x"})
    assert response.status_code == 422
