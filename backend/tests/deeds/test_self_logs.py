"""Self-logs and the public profile counts (docs/M2-DESIGN.md D3, D4, D15, §3.2 #11, #13, §7)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent
from app.modules.deeds.models import Deed, DeedStatus, QrScan, SameActGroup, SelfLog
from app.modules.identity.models import User, UserStatus
from tests.deeds.helpers import (
    Member,
    count,
    location,
    make_member,
    members,
    publish_limits,
    scan,
    scan_and_report,
)


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "category": "animals",
        "description": "Fed the street dogs near the park",
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    body.update(overrides)
    return body


async def _create(client: AsyncClient, owner: Member, **overrides: Any) -> dict[str, Any]:
    response = await client.post(
        "/api/me/self-logs", json=_body(**overrides), headers=owner.headers
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


async def _counts(client: AsyncClient, handle: str) -> dict[str, Any]:
    response = await client.get(f"/api/users/{handle}/deed-counts")
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


# --------------------------------------------------- create, list, edit, delete


async def test_create_list_edit_delete(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_member(session)
    created = await _create(client, owner, location=location())
    assert set(created) == {
        "id",
        "category",
        "description",
        "occurred_at",
        "has_location",
        "created_at",
        "updated_at",
    }
    assert created["has_location"] is True
    assert "12.97" not in str(created) and "77.59" not in str(created)

    listed = (await client.get("/api/me/self-logs", headers=owner.headers)).json()
    assert [i["id"] for i in listed["items"]] == [created["id"]]
    assert listed["next_cursor"] is None

    response = await client.patch(
        f"/api/me/self-logs/{created['id']}",
        json={"description": "Fed and watered the street dogs", "category": "environment"},
        headers=owner.headers,
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["description"] == "Fed and watered the street dogs"
    assert edited["category"] == "environment"
    # Fields that weren't sent are kept, including the location.
    assert edited["has_location"] is True
    assert edited["occurred_at"] == created["occurred_at"]

    response = await client.delete(f"/api/me/self-logs/{created['id']}", headers=owner.headers)
    assert response.status_code == 204
    assert (await client.get("/api/me/self-logs", headers=owner.headers)).json()["items"] == []
    assert await count(session, SelfLog, SelfLog.user_id == owner.user_id) == 0


async def test_location_is_optional_and_can_be_removed(
    client: AsyncClient, session: AsyncSession
) -> None:
    owner = await make_member(session)
    without = await _create(client, owner)
    assert without["has_location"] is False

    with_loc = await _create(client, owner, location=location(north_m=500))
    response = await client.patch(
        f"/api/me/self-logs/{with_loc['id']}", json={"location": None}, headers=owner.headers
    )
    assert response.status_code == 200
    assert response.json()["has_location"] is False
    row = await session.get(SelfLog, uuid.UUID(with_loc["id"]))
    assert row is not None
    await session.refresh(row)
    assert row.location is None


async def test_occurred_at_any_past_date_but_not_far_future(
    client: AsyncClient, session: AsyncSession
) -> None:
    owner = await make_member(session)
    now = datetime.now(UTC)
    await _create(client, owner, occurred_at=(now - timedelta(days=200)).isoformat())
    await _create(client, owner, occurred_at=(now + timedelta(minutes=4)).isoformat())

    response = await client.post(
        "/api/me/self-logs",
        json=_body(occurred_at=(now + timedelta(minutes=6)).isoformat()),
        headers=owner.headers,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "implausible_time"

    log = await _create(client, owner)
    response = await client.patch(
        f"/api/me/self-logs/{log['id']}",
        json={"occurred_at": (now + timedelta(minutes=6)).isoformat()},
        headers=owner.headers,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "implausible_time"


async def test_content_is_validated(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_member(session)
    cases: list[tuple[dict[str, Any], int, str | None]] = [
        (_body(category="not_a_category"), 400, "invalid_category"),
        (_body(description="short"), 400, "invalid_description"),
        (_body(occurred_at="2026-11-01T10:00:00"), 422, None),  # naive timestamp
        (_body(doer="someone"), 422, None),  # unknown fields are rejected
        ({"category": "animals", "description": "Fed the street dogs"}, 422, None),
    ]
    for body, status, code in cases:
        response = await client.post("/api/me/self-logs", json=body, headers=owner.headers)
        assert response.status_code == status, (body, response.text)
        if code:
            assert response.json()["code"] == code

    log = await _create(client, owner)
    for patch in ({"category": None}, {"description": None}, {"occurred_at": None}):
        response = await client.patch(
            f"/api/me/self-logs/{log['id']}", json=patch, headers=owner.headers
        )
        assert response.status_code == 422, patch
    response = await client.patch(
        f"/api/me/self-logs/{log['id']}", json={"category": "nope"}, headers=owner.headers
    )
    assert response.json()["code"] == "invalid_category"


async def test_owner_only_everywhere(client: AsyncClient, session: AsyncSession) -> None:
    owner, other = await members(session, 2)
    log = await _create(client, owner)

    listed = (await client.get("/api/me/self-logs", headers=other.headers)).json()
    assert listed["items"] == [] and listed["total"] == 0
    for method, body in (
        ("PATCH", {"description": "Taken over by someone else"}),
        ("DELETE", None),
    ):
        response = await client.request(
            method, f"/api/me/self-logs/{log['id']}", json=body, headers=other.headers
        )
        assert response.status_code == 404, method
        assert response.json()["code"] == "self_log_not_found"
    # Unknown IDs look the same as other people's.
    response = await client.delete(f"/api/me/self-logs/{uuid.uuid4()}", headers=owner.headers)
    assert response.status_code == 404
    assert response.json()["code"] == "self_log_not_found"

    row = await session.get(SelfLog, uuid.UUID(log["id"]))
    assert row is not None and row.description == "Fed the street dogs near the park"


async def test_requires_onboarded_member(client: AsyncClient, session: AsyncSession) -> None:
    newcomer = await make_member(session, onboarded=False)
    assert (await client.get("/api/me/self-logs")).status_code == 401
    response = await client.post("/api/me/self-logs", json=_body(), headers=newcomer.headers)
    assert response.status_code == 403
    assert response.json()["code"] == "onboarding_required"


async def test_list_pages_newest_first(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_member(session)
    base = datetime(2026, 11, 1, tzinfo=UTC)
    for i in range(5):
        session.add(
            SelfLog(
                user_id=owner.user_id,
                category="animals",
                description=f"Log number {i}",
                occurred_at=base,
                created_at=base + timedelta(minutes=i),
            )
        )
    await session.commit()

    other = await make_member(session)
    session.add(
        SelfLog(user_id=other.user_id, category="animals", description="Not mine", occurred_at=base)
    )
    await session.commit()

    seen: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = (await client.get("/api/me/self-logs", params=params, headers=owner.headers)).json()
        seen.extend(i["description"] for i in page["items"])
        # Every page carries the owner's whole count (My deeds, M4 D69), never anyone else's.
        assert page["total"] == 5
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [f"Log number {i}" for i in reversed(range(5))]

    response = await client.get(
        "/api/me/self-logs", params={"cursor": "garbage!"}, headers=owner.headers
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_cursor"


async def test_self_logs_touch_nothing_else(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_member(session)
    tables: list[Any] = [Deed, SameActGroup, QrScan, OutboxEvent]
    before = [await count(session, t) for t in tables]
    log = await _create(client, owner, location=location())
    await client.patch(
        f"/api/me/self-logs/{log['id']}",
        json={"description": "Fed the dogs again today"},
        headers=owner.headers,
    )
    await client.delete(f"/api/me/self-logs/{log['id']}", headers=owner.headers)
    assert [await count(session, t) for t in tables] == before


async def test_writes_are_rate_limited_per_account_and_per_ip(
    client: AsyncClient,
    other_ip: AsyncClient,
    session: AsyncSession,
) -> None:
    await publish_limits(session, rate_self_log_user="2/3600", rate_self_log_ip="3/3600")
    a, b, c = await members(session, 3)
    # Account key: a third write from the same account on another IP is refused.
    await _create(client, a)
    await _create(client, a)
    response = await other_ip.post("/api/me/self-logs", json=_body(), headers=a.headers)
    assert response.status_code == 429
    # IP key: the first IP already has 2 hits from a; b's first hit is the 3rd, c's is the 4th.
    await _create(client, b)
    response = await client.post("/api/me/self-logs", json=_body(), headers=c.headers)
    assert response.status_code == 429
    # Reads are not limited.
    assert (await client.get("/api/me/self-logs", headers=a.headers)).status_code == 200


# --------------------------------------------------- profile counts (§3.2 #13)


async def test_counts_are_public_and_never_show_self_logs(
    client: AsyncClient, session: AsyncSession
) -> None:
    """M4 D69 (PO Q55): the public counts are ``{verified}`` only; the Self-log Deeds count is
    no longer public (the owner sees their self-logs in their own list)."""
    owner = await make_member(session)
    assert await _counts(client, owner.handle) == {"verified": 0}
    await _create(client, owner)
    await _create(client, owner)
    body = await _counts(client, owner.handle)
    assert body == {"verified": 0}
    # Handles are case-insensitive, like the public profile.
    assert await _counts(client, owner.handle.upper()) == body
    # The owner still has them in their own list.
    own = await client.get("/api/me/self-logs", headers=owner.headers)
    assert len(own.json()["items"]) == 2


async def test_verified_counts_counted_reports(client: AsyncClient, session: AsyncSession) -> None:
    doer = await make_member(session)
    witnesses = await members(session, 6)
    # Six witnesses of one act (D15): only the cap of 5 counts.
    for i, w in enumerate(witnesses):
        out = await scan_and_report(session, w, doer, east_m=i * 10)
        assert out.same_act is not None
    await session.commit()
    assert await _counts(client, doer.handle) == {"verified": 5}
    # The witnesses' own counts are unaffected: reporting isn't doing.
    assert (await _counts(client, witnesses[0].handle))["verified"] == 0


async def test_only_verified_deeds_count(client: AsyncClient, session: AsyncSession) -> None:
    doer = await make_member(session)
    w1, w2, w3 = await members(session, 3)
    kept = await scan_and_report(session, w1, doer)
    withdrawn = await scan_and_report(session, w2, doer, east_m=20)
    unverified = await scan_and_report(session, w3, doer, east_m=40)
    now = datetime.now(UTC)
    await session.execute(
        update(Deed)
        .where(Deed.id == withdrawn.id)
        .values(status=DeedStatus.WITHDRAWN, withdrawn_at=now)
    )
    await session.execute(
        update(Deed).where(Deed.id == unverified.id).values(status=DeedStatus.REMOVED)
    )
    # An unconsumed scan is not a deed.
    w4 = await make_member(session)
    await scan(session, w4, doer, east_m=60)
    await session.commit()

    body = await _counts(client, doer.handle)
    assert body["verified"] == 1, kept
    # Self-logs never change the verified count.
    await _create(client, doer)
    assert await _counts(client, doer.handle) == {"verified": 1}


async def test_counts_404_for_unknown_or_inactive_handle(
    client: AsyncClient, session: AsyncSession
) -> None:
    response = await client.get("/api/users/nobody-here-123/deed-counts")
    assert response.status_code == 404
    assert response.json()["code"] == "user_not_found"

    suspended = await make_member(session)
    await session.execute(
        update(User).where(User.id == suspended.user_id).values(status=UserStatus.SUSPENDED)
    )
    await session.commit()
    response = await client.get(f"/api/users/{suspended.handle}/deed-counts")
    assert response.status_code == 404
    assert response.json()["code"] == "user_not_found"


async def test_counts_are_ip_rate_limited(
    client: AsyncClient, other_ip: AsyncClient, session: AsyncSession
) -> None:
    await publish_limits(session, rate_profile_counts_ip="2/60")
    owner = await make_member(session)
    await _counts(client, owner.handle)
    await _counts(client, owner.handle)
    # Applied before the lookup, so unknown handles count too.
    response = await client.get("/api/users/nobody-here-123/deed-counts")
    assert response.status_code == 429
    assert (await other_ip.get(f"/api/users/{owner.handle}/deed-counts")).status_code == 200
