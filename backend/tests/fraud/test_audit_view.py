"""The audit-log view (docs/M5-DESIGN.md D96, §3.3 #8): admin-only, filters, keyset cursor,
range limit."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.modules.identity.models import Role
from tests.deeds.helpers import make_member
from tests.fraud.helpers import publish_fraud
from tests.fraud.review_helpers import NOTE, admin, reciprocal_case, with_role

PATH = "/api/admin/audit"


async def _get(client: AsyncClient, who: Any, **params: str) -> Any:
    return await client.get(PATH, params=params, headers=who.headers)


async def test_admin_only(client: AsyncClient, session: AsyncSession) -> None:
    member = await make_member(session)
    moderator = await with_role(session, await make_member(session), Role.MODERATOR)
    assert (await client.get(PATH)).status_code == 401
    assert (await _get(client, member)).status_code == 403
    assert (await _get(client, moderator)).status_code == 403


async def test_case_actions_show_up_with_their_actor_and_note(
    client: AsyncClient, session: AsyncSession
) -> None:
    who = await admin(session)
    pair = await reciprocal_case(session)
    res = await client.post(
        f"/api/admin/fraud/cases/{pair.case_id}/park", json={"note": NOTE}, headers=who.headers
    )
    assert res.status_code == 200
    body = (await _get(client, who, entity_type="flag_case", entity_id=str(pair.case_id))).json()
    (item,) = body["items"]
    assert item["action"] == "flag_case.parked"
    assert item["actor"] == {"handle": who.handle, "display_name": who.display_name}
    assert item["reason"] == NOTE
    assert item["old_value"] == {"status": "open"}
    assert item["new_value"] == {"type": "reciprocal", "status": "parked"}
    # The admin's own actions are included (§3.5), and filters combine.
    mine = (await _get(client, who, actor=who.handle, action="flag_case.parked")).json()
    assert [i["id"] for i in mine["items"]] == [item["id"]]
    assert (await _get(client, who, actor="nobody-here-xyz")).status_code == 404


async def test_filters_cursor_and_range(client: AsyncClient, session: AsyncSession) -> None:
    who = await admin(session)
    entity = f"test-{uuid.uuid4().hex[:8]}"
    base = datetime.now(UTC) - timedelta(days=3)
    for i in range(5):
        entry = record_audit(
            session,
            entity_type="test_entity",
            entity_id=entity,
            action="test.done" if i % 2 == 0 else "test.other",
            actor_id=who.user_id if i < 3 else None,
            new_value={"i": i},
            reason="testing",
        )
        entry.created_at = base + timedelta(hours=i)
    await publish_fraud(session, page_size=2)
    await session.commit()

    seen: list[int] = []
    cursor: str | None = None
    pages = 0
    while True:
        params = {"entity_id": entity, **({"cursor": cursor} if cursor else {})}
        body = (await _get(client, who, **params)).json()
        seen += [i["new_value"]["i"] for i in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == [4, 3, 2, 1, 0]  # newest first, no repeats
    assert pages == 3

    done = (await _get(client, who, entity_id=entity, action="test.done")).json()
    assert [i["new_value"]["i"] for i in done["items"]] == [4, 2]
    assert all(i["actor"] is None for i in done["items"][:1])
    by_actor = (await _get(client, who, entity_id=entity, actor=who.handle)).json()
    assert [i["new_value"]["i"] for i in by_actor["items"]] == [2, 1]

    since = (base + timedelta(hours=1)).isoformat()
    until = (base + timedelta(hours=3)).isoformat()
    ranged = (await _get(client, who, entity_id=entity, **{"from": since, "to": until})).json()
    assert [i["new_value"]["i"] for i in ranged["items"]] == [3, 2]
    assert ranged["next_cursor"] is not None

    reversed_range = await _get(client, who, **{"from": until, "to": since})
    assert reversed_range.status_code == 400
    assert reversed_range.json()["code"] == "invalid_range"
    too_long = await _get(
        client,
        who,
        **{"from": "2025-01-01T00:00:00+00:00", "to": "2026-01-03T00:00:00+00:00"},
    )
    assert too_long.status_code == 400 and too_long.json()["code"] == "invalid_range"
    from_only = await _get(client, who, **{"from": "2020-01-01T00:00:00+00:00"})
    assert from_only.status_code == 400 and from_only.json()["code"] == "invalid_range"
    ok = await _get(
        client, who, **{"from": "2025-01-01T00:00:00+00:00", "to": "2026-01-02T00:00:00+00:00"}
    )
    assert ok.status_code == 200

    bad = await _get(client, who, cursor="garbage")
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_cursor"
    # A queue cursor is not an audit cursor.
    pair = await reciprocal_case(session)
    await reciprocal_case(session)
    await reciprocal_case(session)
    queue = (await client.get("/api/admin/fraud/cases", headers=who.headers)).json()
    assert queue["next_cursor"] is not None, pair
    wrong = await _get(client, who, cursor=queue["next_cursor"])
    assert wrong.status_code == 400 and wrong.json()["code"] == "invalid_cursor"
