"""The notifications API, N1 to N4 (docs/M6-DESIGN.md §3.2, D106, D109, D114, PO Q93)."""

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ratelimit import InMemoryRateLimiter
from app.modules.identity.models import UserStatus
from app.modules.notifications._limits import load_notifications
from app.modules.notifications._purge import purge
from app.modules.notifications.models import (
    NotificationCategory,
    NotificationDailyCount,
    NotificationDedupe,
    NotificationKind,
)
from tests.deeds.helpers import Member, make_member, members
from tests.feed.helpers import set_post, verified_deed, withdraw_directly
from tests.notifications.helpers import (
    publish_notifications,
    rename,
    rows_for,
    send,
    set_status,
)

K = NotificationKind
UNAVAILABLE_DEED = "This deed is no longer available"


async def _items(client: AsyncClient, member: Member, **params: Any) -> list[dict[str, Any]]:
    response = await client.get("/api/notifications", headers=member.headers, params=params)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def test_signed_out_is_401(client: AsyncClient) -> None:
    for method, path in [
        ("GET", "/api/notifications"),
        ("GET", "/api/notifications/unread-count"),
        ("POST", "/api/notifications/seen"),
        ("POST", "/api/notifications/read-all"),
        ("GET", "/api/notifications/preferences"),
    ]:
        assert (await client.request(method, path)).status_code == 401, path


async def test_recognised_and_like_texts_are_built_on_read(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer = await rename(session, await make_member(session), "Ahmed Khan")
    reporter = await rename(session, await make_member(session), "Priya Rao")
    likers = [await rename(session, await make_member(session), n) for n in ("Ravi", "Sneha")]
    deed = await verified_deed(session, reporter, doer)
    now = datetime.now(UTC)
    await send(session, K.RECOGNISED, doer, actor=reporter, deed_id=deed, now=now)
    for i, liker in enumerate(likers):
        await send(
            session, K.LIKE, doer, actor=liker, deed_id=deed, now=now + timedelta(seconds=1 + i)
        )
        await send(session, K.LIKE, reporter, actor=liker, deed_id=deed, now=now)

    like, recognised = await _items(client, doer)
    assert like["kind"] == "like" and like["category"] == "activity"
    assert like["text"] == "Sneha and Ravi liked the deed about you."
    assert [a["display_name"] for a in like["actors"]] == ["Sneha", "Ravi"]
    assert set(like["actors"][0]) == {"handle", "display_name", "avatar"}
    assert like["actors"][0]["handle"] == likers[1].handle
    assert like["actor_count"] == 2 and like["read"] is False
    assert like["link"] == {"kind": "deed", "target": str(deed)}
    assert like["can_mute"] == {"person": likers[1].handle, "deed": str(deed)}

    assert recognised["text"] == "Priya Rao recognised you for a good deed: Animals."
    assert recognised["category"] == "about_you"
    assert recognised["link"] == {"kind": "deed", "target": str(deed)}
    # Recognitions of you can't be muted (D113).
    assert recognised["can_mute"] == {}

    [reporter_like] = await _items(client, reporter)
    assert reporter_like["text"] == "Sneha and Ravi liked the deed you recognised."

    # A name change shows in old rows (D106).
    await rename(session, reporter, "Priya R.")
    _, recognised = await _items(client, doer)
    assert recognised["text"].startswith("Priya R. recognised you")


async def test_names_for_bigger_groups(client: AsyncClient, session: AsyncSession) -> None:
    doer, reporter = await members(session, 2)
    people = [await rename(session, await make_member(session), f"P{i}") for i in range(6)]
    deed = await verified_deed(session, reporter, doer)
    now = datetime.now(UTC)
    for i, person in enumerate(people[:3]):
        await send(
            session, K.COMMENT, doer, actor=person, deed_id=deed, now=now + timedelta(seconds=i)
        )
    [item] = await _items(client, doer)
    assert item["text"] == "P2, P1 and P0 commented on the deed about you."
    for i, person in enumerate(people[3:]):
        await send(
            session, K.COMMENT, doer, actor=person, deed_id=deed, now=now + timedelta(seconds=9 + i)
        )
    [item] = await _items(client, doer)
    assert item["text"] == "P5, P4 and 4 others commented on the deed about you."
    assert len(item["actors"]) == 3 and item["actor_count"] == 6


async def test_withdrawn_or_hidden_deed_reads_no_longer_available(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer, reporter, liker, viewer = await members(session, 4)
    withdrawn = await verified_deed(session, reporter, doer)
    hidden = await verified_deed(session, reporter, doer)
    await send(session, K.RECOGNISED, doer, actor=reporter, deed_id=withdrawn)
    await send(session, K.LIKE, reporter, actor=liker, deed_id=hidden)
    await send(session, K.NETWORK, viewer, actor=reporter, deed_id=hidden)
    await withdraw_directly(session, withdrawn)
    await set_post(session, hidden, hidden_by_doer_at=datetime.now(UTC))

    [item] = await _items(client, doer)
    # Never says who withdrew it (PO Q93): no names, no link, nothing to mute.
    assert item["text"] == UNAVAILABLE_DEED
    assert item["actors"] == [] and item["actor_count"] == 0
    assert item["link"] is None and item["can_mute"] == {}
    # A deed hidden by its doer: the reporter can still see it (published), others can't.
    [like] = await _items(client, reporter)
    assert like["text"] == "Deed Tester liked the deed you recognised."
    [network] = await _items(client, viewer)
    assert network["text"] == UNAVAILABLE_DEED


async def test_inactive_actor_reads_unavailable_account(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer, follower = await members(session, 2)
    await send(session, K.FOLLOW, doer, actor=follower)
    await set_status(session, follower, UserStatus.SUSPENDED)
    [item] = await _items(client, doer)
    assert item["text"] == "Unavailable account started following you."
    assert item["actors"] == [
        {"handle": None, "display_name": "Unavailable account", "avatar": None}
    ]
    assert item["link"] == {"kind": "connections", "target": "followers"}
    assert item["can_mute"] == {}


async def test_system_rows(client: AsyncClient, session: AsyncSession) -> None:
    owner, doer = await members(session, 2)
    deed = await verified_deed(session, owner, doer)
    city = await session.execute(text("SELECT id, name FROM regions WHERE code = 'IN-KA-BLR'"))
    city_id, city_name = city.one()
    now = datetime.now(UTC)
    rows: list[tuple[K, dict[str, Any], uuid.UUID | None]] = [
        (K.BADGE_AWARDED, {"badge_slug": "kind_5"}, None),
        (
            K.TITLE_AWARDED,
            {"level_kind": "city", "region_id": city_id, "period_month": "2026-10-01", "place": 1},
            None,
        ),
        (K.PHOTO_REMOVED, {}, deed),
        (K.PHOTO_KEPT, {}, deed),
        (K.COMMENT_REMOVED, {}, deed),
        (K.AVATAR_REMOVED, {}, None),
        (K.PASSWORD_CHANGED, {}, None),
    ]
    for i, (kind, data, deed_id) in enumerate(rows):
        await send(session, kind, owner, deed_id=deed_id, data=data, now=now + timedelta(seconds=i))
    items = {item["kind"]: item for item in await _items(client, owner)}
    assert items["badge_awarded"]["text"] == "You earned a new badge."
    assert items["badge_awarded"]["link"] == {"kind": "badges", "target": owner.handle}
    assert items["title_awarded"]["text"] == f"You made the Top 3 in {city_name} for October 2026!"
    assert items["photo_removed"]["link"] == {"kind": "my_deed", "target": str(deed)}
    assert items["photo_kept"]["category"] == "account"
    assert items["comment_removed"]["link"] == {"kind": "guidelines", "target": "community"}
    assert items["avatar_removed"]["link"] == {"kind": "guidelines", "target": "photo"}
    assert items["password_changed"]["link"] is None
    assert all(item["can_mute"] == {} for item in items.values())


FORBIDDEN = re.compile(r"fraud|flag|case|ring|detector|reciprocal|concentration|admin", re.I)


async def test_review_updates_are_neutral(client: AsyncClient, session: AsyncSession) -> None:
    member = await make_member(session)
    due = datetime(2026, 12, 5, 12, 0, tzinfo=UTC)
    kinds = [
        "explanation_requested",
        "explanation_reminder",
        "decision_notice",
        "appeal_decided",
        "review_closed",
    ]
    now = datetime.now(UTC)
    for i, kind in enumerate(kinds):
        data: dict[str, Any] = {"review_kind": kind}
        if kind.startswith("explanation"):
            data["reply_due_at"] = due.isoformat()
        await send(
            session,
            K.REVIEW_UPDATE,
            member,
            ref_id=uuid.uuid4(),
            data=data,
            now=now + timedelta(seconds=i),
        )
    items = await _items(client, member)
    texts = {item["text"] for item in items}
    assert (
        "We'd like to hear from you about some of your recent deeds. Please reply by 5 December."
        in texts
    )
    assert "There's an update for you in Reviews." in texts
    for item in items:
        assert item["category"] == "reviews"
        assert item["link"] == {"kind": "reviews", "target": None}
        assert item["actors"] == []
        assert not FORBIDDEN.search(item["text"]), item["text"]


async def test_paging_unread_tab_and_invalid_cursor(
    client: AsyncClient, session: AsyncSession
) -> None:
    await publish_notifications(session, page_size=2)
    member = await make_member(session)
    now = datetime.now(UTC)
    for i in range(5):
        await send(session, K.BADGE_AWARDED, member, now=now + timedelta(seconds=i))
    rows = await rows_for(session, member)
    rows[1].read_at = now + timedelta(seconds=10)
    await session.flush()

    seen: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, str] = {"cursor": cursor} if cursor else {}
        body = (
            await client.get("/api/notifications", headers=member.headers, params=params)
        ).json()
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == [str(r.id) for r in rows]

    first = (
        await client.get("/api/notifications", headers=member.headers, params={"unread": "true"})
    ).json()
    assert [i["id"] for i in first["items"]] == [str(rows[0].id), str(rows[2].id)]
    unread_cursor = first["next_cursor"]
    rest = await _items(client, member, unread="true", cursor=unread_cursor)
    assert [i["id"] for i in rest] == [str(rows[3].id), str(rows[4].id)]

    # A cursor from the other tab, or garbage, is invalid.
    response = await client.get(
        "/api/notifications", headers=member.headers, params={"cursor": unread_cursor}
    )
    assert response.status_code == 400 and response.json()["code"] == "invalid_cursor"
    response = await client.get(
        "/api/notifications", headers=member.headers, params={"cursor": "%%%"}
    )
    assert response.status_code == 400 and response.json()["code"] == "invalid_cursor"


async def test_unread_count_seen_and_read_state(client: AsyncClient, session: AsyncSession) -> None:
    member, other = await members(session, 2)
    now = datetime.now(UTC) - timedelta(minutes=5)
    for i in range(3):
        await send(session, K.BADGE_AWARDED, member, now=now + timedelta(seconds=i))
    count = await client.get("/api/notifications/unread-count", headers=member.headers)
    assert count.json() == {"unread": 3, "new_since_seen": 3, "unread_poll_seconds": 60}

    assert (await client.post("/api/notifications/seen", headers=member.headers)).status_code == 204
    await send(session, K.BADGE_AWARDED, member)
    count = await client.get("/api/notifications/unread-count", headers=member.headers)
    assert count.json() == {"unread": 4, "new_since_seen": 1, "unread_poll_seconds": 60}

    rows = await rows_for(session, member)
    target = rows[0].id
    assert (
        await client.post(f"/api/notifications/{target}/read", headers=member.headers)
    ).status_code == 204
    assert (
        await client.post(f"/api/notifications/{target}/read", headers=member.headers)
    ).status_code == 204
    count = await client.get("/api/notifications/unread-count", headers=member.headers)
    assert count.json()["unread"] == 3
    assert (
        await client.post(f"/api/notifications/{target}/unread", headers=member.headers)
    ).status_code == 204
    count = await client.get("/api/notifications/unread-count", headers=member.headers)
    assert count.json()["unread"] == 4

    # Another person's row, or an unknown id, is 404 (a refused request rolls back uncommitted
    # work, so commit first).
    await session.commit()
    for path in (
        f"/api/notifications/{target}/read",
        f"/api/notifications/{target}/unread",
        f"/api/notifications/{uuid.uuid4()}/read",
    ):
        response = await client.post(path, headers=other.headers)
        assert response.status_code == 404 and response.json()["code"] == "notification_not_found"

    assert (
        await client.post("/api/notifications/read-all", headers=member.headers)
    ).status_code == 204
    count = await client.get("/api/notifications/unread-count", headers=member.headers)
    assert count.json() == {"unread": 0, "new_since_seen": 0, "unread_poll_seconds": 60}
    items = await _items(client, member)
    assert len(items) == 4 and all(item["read"] for item in items)


async def test_unread_again_refused_while_a_newer_group_row_is_unread(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer, reporter, a, b = await members(session, 4)
    deed = await verified_deed(session, reporter, doer)
    await send(session, K.LIKE, doer, actor=a, deed_id=deed)
    [old] = await rows_for(session, doer)
    assert (
        await client.post(f"/api/notifications/{old.id}/read", headers=doer.headers)
    ).status_code == 204
    await send(
        session, K.LIKE, doer, actor=b, deed_id=deed, now=datetime.now(UTC) + timedelta(seconds=1)
    )
    response = await client.post(f"/api/notifications/{old.id}/unread", headers=doer.headers)
    assert response.status_code == 409 and response.json()["code"] == "newer_unread_exists"


async def test_reads_and_writes_are_rate_limited_separately(
    client: AsyncClient, session: AsyncSession, rate_limiter: InMemoryRateLimiter
) -> None:
    await publish_notifications(session, rate_read="2/60", rate_write="1/60")
    member = await make_member(session)
    for _ in range(2):
        assert (
            await client.get("/api/notifications/unread-count", headers=member.headers)
        ).status_code == 200
    response = await client.get("/api/notifications", headers=member.headers)
    assert response.status_code == 429
    assert (
        await client.post("/api/notifications/read-all", headers=member.headers)
    ).status_code == 204
    assert (
        await client.post("/api/notifications/read-all", headers=member.headers)
    ).status_code == 429


async def test_purge_removes_old_rows(session: AsyncSession) -> None:
    doer, reporter, a = await members(session, 3)
    deed = await verified_deed(session, reporter, doer)
    now = datetime.now(UTC)
    old = now - timedelta(days=91)
    await send(session, K.BADGE_AWARDED, doer, now=old)
    await send(session, K.LIKE, doer, actor=a, deed_id=deed, now=old)
    await send(session, K.BADGE_AWARDED, doer, now=now - timedelta(days=89))
    session.add(
        NotificationDailyCount(
            user_id=doer.user_id,
            day=date(2000, 1, 1),
            category=NotificationCategory.ACTIVITY,
            count=1,
        )
    )
    await session.flush()
    _, config = await load_notifications(session)
    result = await purge(session, config, now)
    assert result.notifications == 2
    assert result.dedupe >= 1 and result.daily_counts >= 1
    assert len(await rows_for(session, doer)) == 1
    left = await session.scalars(
        select(NotificationDedupe).where(NotificationDedupe.user_id == doer.user_id)
    )
    assert list(left) == []


async def test_actors_carry_their_avatar_ref(client: AsyncClient, session: AsyncSession) -> None:
    from app.modules.identity.models import User

    doer, follower = await members(session, 2)
    user = await session.get(User, follower.user_id)
    assert user is not None
    user.avatar_ref = f"{uuid.uuid4()}-1"
    user.avatar_changed_at = datetime.now(UTC)
    await session.flush()
    await send(session, K.FOLLOW, doer, actor=follower)
    [item] = await _items(client, doer)
    assert item["actors"][0]["avatar"] == user.avatar_ref
    # A suspended actor never shows a picture.
    await set_status(session, follower, UserStatus.SUSPENDED)
    [item] = await _items(client, doer)
    assert item["actors"][0]["avatar"] is None
