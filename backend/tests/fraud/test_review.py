"""Admin fraud review: roles, queue, detail, park/unpark, decisions and their effects, D99
gating, notices, conflict of interest and privacy (docs/M5-DESIGN.md §3.3 #1 to #7, #10, D88,
D90, D95, D99, D100, §8 "Review")."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent
from app.modules.deeds.models import Deed, DeedStatus
from app.modules.fraud import _review
from app.modules.fraud._limits import load_fraud
from app.modules.fraud.models import (
    DecisionNotice,
    FlagCaseDeedOutcome,
    FlagCaseStatus,
    FlagCaseType,
    PrizeHold,
)
from app.modules.fraud.schemas import DecisionIn, DeedAction
from app.modules.identity.models import Role
from tests.deeds.helpers import count, make_member, members, outbox
from tests.fraud.helpers import days_ago, deed, publish_fraud
from tests.fraud.review_helpers import (
    NOTE,
    admin,
    ask,
    audit_actions,
    case_row,
    confirm_body,
    decisions,
    dismiss_body,
    open_case,
    reciprocal_case,
    reply,
    ring_case,
    with_role,
)

HOLD_EVENT = "fraud.deed_hold_changed"
RESOLVED_EVENT = "fraud.flag_case_resolved"


def _case_paths(case_id: uuid.UUID) -> list[tuple[str, str, dict[str, Any] | None]]:
    base = f"/api/admin/fraud/cases/{case_id}"
    return [
        ("GET", "/api/admin/fraud/options", None),
        ("GET", "/api/admin/fraud/status", None),
        ("GET", "/api/admin/fraud/cases", None),
        ("GET", base, None),
        ("POST", f"{base}/park", {"note": NOTE}),
        ("POST", f"{base}/unpark", {"note": NOTE}),
        ("POST", f"{base}/decision", dismiss_body()),
        ("POST", f"{base}/explanations", {"note": NOTE}),
        ("GET", "/api/admin/audit", None),
    ]


async def _call(
    client: AsyncClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    headers: dict[str, str] | None,
) -> int:
    res = await client.request(method, path, json=body, headers=headers or {})
    return res.status_code


# --------------------------------------------------- roles (D90)


async def test_every_endpoint_is_admin_only(client: AsyncClient, session: AsyncSession) -> None:
    pair = await reciprocal_case(session)
    member = await make_member(session)
    moderator = await with_role(session, await make_member(session), Role.MODERATOR)
    for method, path, body in _case_paths(pair.case_id):
        assert await _call(client, method, path, body, None) == 401, path
        assert await _call(client, method, path, body, member.headers) == 403, path
        assert await _call(client, method, path, body, moderator.headers) == 403, path
    # Nothing changed.
    assert (await case_row(session, pair.case_id)).status == FlagCaseStatus.OPEN
    assert await decisions(session, pair.case_id) == []


async def test_options_and_status(client: AsyncClient, session: AsyncSession) -> None:
    who = await admin(session)
    await reciprocal_case(session)
    res = await client.get("/api/admin/fraud/options", headers=who.headers)
    assert res.status_code == 200
    body = res.json()
    assert {t["id"] for t in body["types"]} >= {"reciprocal", "ring", "content"}
    assert all(t["holds_on_open"] is False for t in body["types"])
    assert body["deed_outcomes"] == ["exclude", "reject", "release"]
    assert body["note_min_chars"] == 10
    assert [c["id"] for c in body["override_reason_codes"]] == [
        "fake_photo",
        "rule_misfire",
        "fraud_confirmed",
        "other",
    ]
    status = await client.get("/api/admin/fraud/status", headers=who.headers)
    assert status.status_code == 200
    data = status.json()
    assert {d["detector"] for d in data["detectors"]} >= {"reciprocal", "ring"}
    assert data["open_cases_by_type"]["reciprocal"] >= 1


async def test_reads_are_rate_limited_per_account(
    client: AsyncClient, session: AsyncSession
) -> None:
    who = await admin(session)
    await publish_fraud(session, rate_admin_read="2/60")
    await session.commit()
    for _ in range(2):
        assert (
            await client.get("/api/admin/fraud/options", headers=who.headers)
        ).status_code == 200
    res = await client.get("/api/admin/fraud/options", headers=who.headers)
    assert res.status_code == 429


# --------------------------------------------------- queue (#2) and detail (#3)


async def test_queue_tabs_counts_filters_and_cursor(
    client: AsyncClient, session: AsyncSession
) -> None:
    who = await admin(session)
    first, second = await reciprocal_case(session), await reciprocal_case(session)
    ring = await ring_case(session)
    await publish_fraud(session, page_size=1)
    await session.commit()

    async def ids(**params: str) -> tuple[list[str], dict[str, Any]]:
        seen: list[str] = []
        body: dict[str, Any] = {}
        cursor: str | None = None
        while True:
            query = {**params, **({"cursor": cursor} if cursor else {})}
            res = await client.get("/api/admin/fraud/cases", params=query, headers=who.headers)
            assert res.status_code == 200, res.text
            body = res.json()
            seen += [item["case_id"] for item in body["items"]]
            cursor = body["next_cursor"]
            if cursor is None:
                return seen, body

    mine = {str(first.case_id), str(second.case_id), str(ring.case_id)}
    listed, body = await ids()
    assert mine <= set(listed)
    assert len(listed) == len(set(listed))  # no repeats across pages
    assert body["counts"]["open"] == len(listed)

    rings, _ = await ids(type="ring")
    assert str(ring.case_id) in rings and str(first.case_id) not in rings
    by_user, body = await ids(user=first.a.handle)
    assert by_user == [str(first.case_id)]
    assert body["counts"] == {"open": 1, "parked": 0, "resolved": 0}

    park = await client.post(
        f"/api/admin/fraud/cases/{first.case_id}/park", json={"note": NOTE}, headers=who.headers
    )
    assert park.status_code == 200
    parked, body = await ids(status="parked", user=first.a.handle)
    assert parked == [str(first.case_id)]
    assert body["counts"] == {"open": 0, "parked": 1, "resolved": 0}

    row = (
        await client.get(
            "/api/admin/fraud/cases", params={"user": ring.people[0].handle}, headers=who.headers
        )
    ).json()["items"][0]
    assert row["user_count"] == 3 and row["deed_count"] == 3 and row["held_count"] == 0
    assert "ring" in row["summary"]

    unknown = await client.get(
        "/api/admin/fraud/cases", params={"user": "nobody-here-xyz"}, headers=who.headers
    )
    assert unknown.status_code == 404
    bad = await client.get(
        "/api/admin/fraud/cases", params={"cursor": "not-a-cursor"}, headers=who.headers
    )
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_cursor"
    # A cursor of another tab is invalid too.
    page = (
        await client.get("/api/admin/fraud/cases", params={"status": "open"}, headers=who.headers)
    ).json()
    if page["next_cursor"]:
        other = await client.get(
            "/api/admin/fraud/cases",
            params={"status": "parked", "cursor": page["next_cursor"]},
            headers=who.headers,
        )
        assert other.status_code == 400


async def test_resolved_tab_is_newest_first(client: AsyncClient, session: AsyncSession) -> None:
    who = await admin(session)
    first, second = await reciprocal_case(session), await reciprocal_case(session)
    for pair in (first, second):
        res = await client.post(
            f"/api/admin/fraud/cases/{pair.case_id}/decision",
            json=dismiss_body(),
            headers=who.headers,
        )
        assert res.status_code == 200
    items = (
        await client.get(
            "/api/admin/fraud/cases", params={"status": "resolved"}, headers=who.headers
        )
    ).json()["items"]
    ours = [
        i["case_id"] for i in items if i["case_id"] in {str(first.case_id), str(second.case_id)}
    ]
    assert ours == [str(second.case_id), str(first.case_id)]


async def test_case_detail_shape_and_privacy(client: AsyncClient, session: AsyncSession) -> None:
    who = await admin(session)
    pair = await reciprocal_case(session)
    res = await client.get(f"/api/admin/fraud/cases/{pair.case_id}", headers=who.headers)
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {
        "case",
        "users",
        "deeds",
        "edges",
        "decisions",
        "explanations",
        "finalization",
        "window",
    }
    assert body["case"]["signal"] == {"a_to_b": 1, "b_to_a": 1, "first_gap_hours": 24}
    assert "recognised each other 2 times" in body["case"]["summary"]
    assert {d["deed_id"] for d in body["deeds"]} == {str(pair.a_to_b), str(pair.b_to_a)}
    deed_out = body["deeds"][0]
    assert deed_out["held"] is False and deed_out["outcome"] is None
    assert deed_out["status"] == "verified" and deed_out["area_name"]
    assert {(e["from"]["handle"], e["to"]["handle"]) for e in body["edges"]} == {
        (pair.a.handle, pair.b.handle),
        (pair.b.handle, pair.a.handle),
    }
    assert all(
        set(e) == {"from", "to", "deeds", "shared_device", "shared_network"} for e in body["edges"]
    )
    users = {u["user"]["handle"]: u for u in body["users"]}
    assert users[pair.a.handle]["verified_as_doer_in_window"] == 1
    assert users[pair.a.handle]["verified_as_reporter_in_window"] == 1
    assert body["finalization"] and all("final" in m for m in body["finalization"])

    # No coordinates, hashes, phone numbers or points anywhere (D95, D78).
    raw = json.dumps(body)
    for member in (pair.a, pair.b):
        assert member.phone_hash not in raw
    assert "+91" not in raw

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {k for v in value.values() for k in keys(v)}
        if isinstance(value, list):
            return {k for v in value for k in keys(v)}
        return set()

    forbidden = {
        "lat",
        "lng",
        "latitude",
        "longitude",
        "location",
        "coordinates",
        "phone",
        "phone_e164",
        "device_hash",
        "ip_hash",
        "points",
        "doer_points",
    }
    assert not keys(body) & forbidden


async def test_unknown_case_is_404(client: AsyncClient, session: AsyncSession) -> None:
    who = await admin(session)
    res = await client.get(f"/api/admin/fraud/cases/{uuid.uuid4()}", headers=who.headers)
    assert res.status_code == 404 and res.json()["code"] == "case_not_found"


# --------------------------------------------------- park / unpark (#4, #5) and notes


async def test_park_unpark_transitions_and_409s(client: AsyncClient, session: AsyncSession) -> None:
    who = await admin(session)
    pair = await reciprocal_case(session)
    base = f"/api/admin/fraud/cases/{pair.case_id}"

    res = await client.post(f"{base}/unpark", json={"note": NOTE}, headers=who.headers)
    assert res.status_code == 409 and res.json()["code"] == "case_not_parked"
    res = await client.post(f"{base}/park", json={"note": NOTE}, headers=who.headers)
    assert res.status_code == 200 and res.json()["case"]["status"] == "parked"
    res = await client.post(f"{base}/park", json={"note": NOTE}, headers=who.headers)
    assert res.status_code == 409 and res.json()["code"] == "case_not_open"
    res = await client.post(f"{base}/unpark", json={"note": NOTE}, headers=who.headers)
    assert res.status_code == 200 and res.json()["case"]["status"] == "open"
    # A parked case can be decided.
    await client.post(f"{base}/park", json={"note": NOTE}, headers=who.headers)
    res = await client.post(f"{base}/decision", json=dismiss_body(), headers=who.headers)
    assert res.status_code == 200
    assert res.json()["case"]["status"] == "resolved"
    assert res.json()["case"]["outcome"] == "dismissed"

    # A resolved case is never changed again.
    for path, body, code in (
        (f"{base}/park", {"note": NOTE}, "case_not_open"),
        (f"{base}/unpark", {"note": NOTE}, "case_not_parked"),
        (f"{base}/decision", dismiss_body(), "case_resolved"),
        (f"{base}/explanations", {"note": NOTE}, "case_resolved"),
    ):
        res = await client.post(path, json=body, headers=who.headers)
        assert res.status_code == 409 and res.json()["code"] == code, path

    assert await decisions(session, pair.case_id) == ["park", "unpark", "park", "dismiss"]
    assert await audit_actions(session, pair.case_id) == [
        "flag_case.parked",
        "flag_case.unparked",
        "flag_case.parked",
        "flag_case.dismissed",
    ]
    detail = (await client.get(base, headers=who.headers)).json()
    assert [d["action"] for d in detail["decisions"]] == ["park", "unpark", "park", "dismiss"]
    assert all(
        d["actor"]["handle"] == who.handle and d["note"] == NOTE for d in detail["decisions"]
    )


async def test_note_bounds_come_from_config(client: AsyncClient, session: AsyncSession) -> None:
    who = await admin(session)
    pair = await reciprocal_case(session)
    park = f"/api/admin/fraud/cases/{pair.case_id}/park"

    res = await client.post(park, json={"note": "   short  "}, headers=who.headers)
    assert res.status_code == 422 and res.json()["code"] == "note_required"
    await publish_fraud(session, note_min_chars=20, note_max_chars=40)
    await session.commit()
    res = await client.post(park, json={"note": "fifteen chars!!"}, headers=who.headers)
    assert res.status_code == 422 and res.json()["code"] == "note_required"
    res = await client.post(park, json={"note": "x" * 41}, headers=who.headers)
    assert res.status_code == 422 and res.json()["code"] == "note_too_long"
    for path, body in (
        (f"/api/admin/fraud/cases/{pair.case_id}/decision", dismiss_body("too short")),
        (f"/api/admin/fraud/cases/{pair.case_id}/explanations", {"note": "too short"}),
    ):
        res = await client.post(path, json=body, headers=who.headers)
        assert res.status_code == 422 and res.json()["code"] == "note_required"
    assert await decisions(session, pair.case_id) == []
    res = await client.post(park, json={"note": "  " + "y" * 25 + "  "}, headers=who.headers)
    assert res.status_code == 200
    assert res.json()["decisions"][0]["note"] == "y" * 25


async def test_decisions_and_outcomes_are_append_only(session: AsyncSession) -> None:
    who = await admin(session)
    pair = await reciprocal_case(session)
    await ask(session, who, pair.case_id)
    _, config = await load_fraud(session)
    await _review.decide(
        session,
        who.user_id,
        pair.case_id,
        DecisionIn(
            outcome="confirmed",
            note=NOTE,
            deeds=[
                DeedAction(deed_id=pair.a_to_b, action="exclude"),
                DeedAction(deed_id=pair.b_to_a, action="release"),
            ],
        ),
        config=config,
        now=datetime.now(UTC) + timedelta(days=30),  # both replies are past due
    )
    await session.flush()
    for statement in (
        "UPDATE flag_case_decisions SET note = 'changed note here' WHERE case_id = :c",
        "DELETE FROM flag_case_decisions WHERE case_id = :c",
        "UPDATE flag_case_deed_outcomes SET outcome = 'reject' WHERE case_id = :c",
        "DELETE FROM flag_case_deed_outcomes WHERE case_id = :c",
        "UPDATE flag_cases SET held_count = 0 WHERE id = :c",
    ):
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(text(statement), {"c": pair.case_id})


# --------------------------------------------------- decision (#6)


async def test_deed_actions_must_cover_every_case_deed(
    client: AsyncClient, session: AsyncSession
) -> None:
    who = await admin(session)
    pair = await reciprocal_case(session)
    path = f"/api/admin/fraud/cases/{pair.case_id}/decision"
    bad_bodies: list[dict[str, Any]] = [
        {"outcome": "confirmed", "note": NOTE},
        confirm_body({pair.a_to_b: "exclude"}),
        confirm_body({pair.a_to_b: "exclude", pair.b_to_a: "release", uuid.uuid4(): "reject"}),
        {
            "outcome": "confirmed",
            "note": NOTE,
            "deeds": [
                {"deed_id": str(pair.a_to_b), "action": "exclude"},
                {"deed_id": str(pair.a_to_b), "action": "release"},
                {"deed_id": str(pair.b_to_a), "action": "release"},
            ],
        },
        {**dismiss_body(), "deeds": [{"deed_id": str(pair.a_to_b), "action": "release"}]},
    ]
    for body in bad_bodies:
        res = await client.post(path, json=body, headers=who.headers)
        assert res.status_code == 422 and res.json()["code"] == "deed_actions_invalid", body
    res = await client.post(
        path,
        json=confirm_body({pair.a_to_b: "release", pair.b_to_a: "release"}),
        headers=who.headers,
    )
    assert res.status_code == 422 and res.json()["code"] == "use_dismiss"
    assert (await case_row(session, pair.case_id)).status == FlagCaseStatus.OPEN
    assert await decisions(session, pair.case_id) == []


async def test_confirm_needs_explanations_first(client: AsyncClient, session: AsyncSession) -> None:
    """D99: never asked -> explanation_required; asked, not replied, not due -> pending;
    replied or past due -> allowed. Only people with an adverse deed count."""
    who = await admin(session)
    ring = await ring_case(session)
    a, b, c = ring.people
    path = f"/api/admin/fraud/cases/{ring.case_id}/decision"
    # a -> b excluded: a and b need asking, c (only released deeds) doesn't.
    body = confirm_body(
        {ring.deeds[0]: "exclude", ring.deeds[1]: "release", ring.deeds[2]: "release"}
    )

    res = await client.post(path, json=body, headers=who.headers)
    assert res.status_code == 409 and res.json()["code"] == "explanation_required"
    await ask(session, who, ring.case_id, a)
    res = await client.post(path, json=body, headers=who.headers)
    assert res.status_code == 409 and res.json()["code"] == "explanation_required"
    # b asked 20 days ago: due date passed with no reply. a asked now: pending.
    await ask(session, who, ring.case_id, b, now=datetime.now(UTC) - timedelta(days=20))
    res = await client.post(path, json=body, headers=who.headers)
    assert res.status_code == 409 and res.json()["code"] == "explanation_pending"
    await reply(session, ring.case_id, a)
    res = await client.post(path, json=body, headers=who.headers)
    assert res.status_code == 200, res.text
    assert res.json()["case"]["outcome"] == "confirmed"

    # Notices only for people with an adverse item, with codes only.
    notices = list(
        await session.scalars(select(DecisionNotice).where(DecisionNotice.case_id == ring.case_id))
    )
    assert {n.user_id for n in notices} == {a.user_id, b.user_id}
    _, config = await load_fraud(session)
    for notice in notices:
        assert notice.kind.value == "case" and notice.reason_key == "case"
        assert notice.items == [{"deed_id": str(ring.deeds[0]), "effect": "excluded"}]
        assert notice.final_at is None
        window = notice.appeal_until - notice.created_at
        assert window == timedelta(days=config.appeal.window_days)
    assert c.user_id not in {n.user_id for n in notices}


async def test_dismiss_never_needs_an_explanation(
    client: AsyncClient, session: AsyncSession
) -> None:
    who = await admin(session)
    pair = await reciprocal_case(session)
    res = await client.post(
        f"/api/admin/fraud/cases/{pair.case_id}/decision", json=dismiss_body(), headers=who.headers
    )
    assert res.status_code == 200
    row = await case_row(session, pair.case_id)
    assert row.status == FlagCaseStatus.RESOLVED and row.resolved_by == who.user_id
    assert row.held_count == 0
    assert await count(session, PrizeHold, PrizeHold.case_id == pair.case_id) == 0
    assert await count(session, DecisionNotice, DecisionNotice.case_id == pair.case_id) == 0
    assert (
        await count(session, FlagCaseDeedOutcome, FlagCaseDeedOutcome.case_id == pair.case_id) == 0
    )
    for deed_id in (pair.a_to_b, pair.b_to_a):
        assert await outbox(session, HOLD_EVENT, deed_id) == []


async def test_confirm_effects_per_deed_outcome(client: AsyncClient, session: AsyncSession) -> None:
    """exclude and reject hold now (nothing rejected yet), release holds nothing; every effect
    is written before the case is resolved; one DeedHoldChanged per deed that became held."""
    who = await admin(session)
    ring = await ring_case(session)
    excluded, rejected, released = ring.deeds
    await ask(session, who, ring.case_id)
    for person in ring.people:
        await reply(session, ring.case_id, person)

    res = await client.post(
        f"/api/admin/fraud/cases/{ring.case_id}/decision",
        json=confirm_body({excluded: "exclude", rejected: "reject", released: "release"}),
        headers=who.headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["case"]["held_count"] == 2
    by_id = {d["deed_id"]: d for d in body["deeds"]}
    assert by_id[str(excluded)]["held"] and by_id[str(excluded)]["outcome"] == "exclude"
    assert by_id[str(rejected)]["held"] and by_id[str(rejected)]["outcome"] == "reject"
    assert not by_id[str(released)]["held"] and by_id[str(released)]["outcome"] == "release"

    holds = list(await session.scalars(select(PrizeHold).where(PrizeHold.case_id == ring.case_id)))
    assert {h.deed_id for h in holds} == {excluded, rejected}
    assert all(h.released_at is None for h in holds)
    for deed_id in (excluded, rejected):
        events = await outbox(session, HOLD_EVENT, deed_id)
        assert len(events) == 1
        assert events[0].payload["held"] is True
        assert events[0].payload["case_id"] == str(ring.case_id)
    assert await outbox(session, HOLD_EVENT, released) == []
    # Nothing is rejected until the decision is final (D89, D100).
    statuses = await session.scalars(
        select(Deed.status).where(Deed.id.in_(ring.deeds)).execution_options(populate_existing=True)
    )
    assert set(statuses) == {DeedStatus.VERIFIED}

    outcomes = {
        o.deed_id: o.outcome.value
        for o in await session.scalars(
            select(FlagCaseDeedOutcome).where(FlagCaseDeedOutcome.case_id == ring.case_id)
        )
    }
    assert outcomes == {excluded: "exclude", rejected: "reject", released: "release"}

    resolved = await session.scalars(
        select(OutboxEvent.payload).where(
            OutboxEvent.event_type == RESOLVED_EVENT,
            OutboxEvent.payload["case_id"].astext == str(ring.case_id),
        )
    )
    (payload,) = list(resolved)
    assert payload["outcome"] == "confirmed"
    assert payload["excluded_deed_ids"] == [str(excluded)]
    assert payload["pending_reject_deed_ids"] == [str(rejected)]
    assert payload["released_deed_ids"] == [str(released)]
    assert payload["actor_id"] == str(who.user_id)

    # Every person on an adverse deed gets one notice with their own adverse items.
    notices = {
        n.user_id: n.items
        for n in await session.scalars(
            select(DecisionNotice).where(DecisionNotice.case_id == ring.case_id)
        )
    }
    a, b, c = ring.people
    assert notices[a.user_id] == [{"deed_id": str(excluded), "effect": "excluded"}]
    assert notices[b.user_id] == [
        {"deed_id": str(excluded), "effect": "excluded"},
        {"deed_id": str(rejected), "effect": "pending_removal"},
    ]
    assert notices[c.user_id] == [{"deed_id": str(rejected), "effect": "pending_removal"}]
    assert await audit_actions(session, ring.case_id) == [
        "flag_case.explanation_requested",
        "flag_case.confirmed",
    ]


async def test_a_deed_held_by_another_case_emits_no_second_event(
    client: AsyncClient, session: AsyncSession
) -> None:
    who = await admin(session)
    ring = await ring_case(session)
    a, b, _ = ring.people
    # A reciprocal case for a and b over the ring's a -> b deed and a new b -> a deed.
    back = await deed(session, b, a, days_ago(2))
    other = await open_case(session, FlagCaseType.RECIPROCAL, [a, b], [ring.deeds[0], back])
    for case_id, actions in (
        (other, {ring.deeds[0]: "exclude", back: "release"}),
        (
            ring.case_id,
            {ring.deeds[0]: "exclude", ring.deeds[1]: "release", ring.deeds[2]: "release"},
        ),
    ):
        await ask(session, who, case_id, a, b)
        await reply(session, case_id, a)
        await reply(session, case_id, b)
        res = await client.post(
            f"/api/admin/fraud/cases/{case_id}/decision",
            json=confirm_body(actions),
            headers=who.headers,
        )
        assert res.status_code == 200, res.text
    assert await count(session, PrizeHold, PrizeHold.deed_id == ring.deeds[0]) == 2
    assert len(await outbox(session, HOLD_EVENT, ring.deeds[0])) == 1


async def test_release_and_dismiss_release_holds_placed_on_open(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Only with a PO-approved ``hold_on_open`` can an open case hold anything: then release
    and dismissal release this case's holds and emit held=false."""
    who = await admin(session)
    await publish_fraud(session, hold_on_open={"reciprocal": True})
    await session.commit()
    first, second = await reciprocal_case(session), await reciprocal_case(session)
    assert (await case_row(session, first.case_id)).held_count == 2

    await ask(session, who, first.case_id)
    await reply(session, first.case_id, first.a)
    await reply(session, first.case_id, first.b)
    res = await client.post(
        f"/api/admin/fraud/cases/{first.case_id}/decision",
        json=confirm_body({first.a_to_b: "exclude", first.b_to_a: "release"}),
        headers=who.headers,
    )
    assert res.status_code == 200
    assert (await case_row(session, first.case_id)).held_count == 1
    released = await outbox(session, HOLD_EVENT, first.b_to_a)
    # Events from one transaction can share occurred_at, so compare without order.
    assert sorted(e.payload["held"] for e in released) == [False, True]
    assert [e.payload["held"] for e in await outbox(session, HOLD_EVENT, first.a_to_b)] == [True]

    res = await client.post(
        f"/api/admin/fraud/cases/{second.case_id}/decision",
        json=dismiss_body(),
        headers=who.headers,
    )
    assert res.status_code == 200
    assert (await case_row(session, second.case_id)).held_count == 0
    for deed_id in (second.a_to_b, second.b_to_a):
        held = [e.payload["held"] for e in await outbox(session, HOLD_EVENT, deed_id)]
        assert sorted(held) == [False, True]


async def test_withdrawn_deed_may_still_be_marked_reject(
    client: AsyncClient, session: AsyncSession
) -> None:
    who = await admin(session)
    pair = await reciprocal_case(session)
    await session.execute(
        text("UPDATE deeds SET status = 'withdrawn', withdrawn_at = now() WHERE id = :d"),
        {"d": pair.b_to_a},
    )
    await session.commit()
    await ask(session, who, pair.case_id, now=datetime.now(UTC) - timedelta(days=20))
    res = await client.post(
        f"/api/admin/fraud/cases/{pair.case_id}/decision",
        json=confirm_body({pair.a_to_b: "exclude", pair.b_to_a: "reject"}),
        headers=who.headers,
    )
    assert res.status_code == 200, res.text
    by_id = {d["deed_id"]: d for d in res.json()["deeds"]}
    assert by_id[str(pair.b_to_a)]["status"] == "withdrawn"
    assert by_id[str(pair.b_to_a)]["outcome"] == "reject"


# --------------------------------------------------- explanations (#10)


async def test_explanation_requests(client: AsyncClient, session: AsyncSession) -> None:
    who = await admin(session)
    ring = await ring_case(session)
    a, b, c = ring.people
    path = f"/api/admin/fraud/cases/{ring.case_id}/explanations"

    res = await client.post(path, json={"note": NOTE, "handles": [a.handle]}, headers=who.headers)
    assert res.status_code == 200, res.text
    (only,) = res.json()["explanations"]
    assert only["user"]["handle"] == a.handle and only["reply"] is None
    asked = datetime.fromisoformat(only["requested_at"])
    due = datetime.fromisoformat(only["reply_due_at"])
    assert due - asked == timedelta(days=14)

    res = await client.post(path, json={"note": NOTE, "handles": [a.handle]}, headers=who.headers)
    assert res.status_code == 409 and res.json()["code"] == "already_requested"
    outsider = await make_member(session)
    res = await client.post(
        path, json={"note": NOTE, "handles": [outsider.handle]}, headers=who.headers
    )
    assert res.status_code == 422 and res.json()["code"] == "not_a_subject"

    # Default: everyone not asked yet.
    res = await client.post(path, json={"note": NOTE}, headers=who.headers)
    assert res.status_code == 200
    assert {e["user"]["handle"] for e in res.json()["explanations"]} == {
        a.handle,
        b.handle,
        c.handle,
    }
    res = await client.post(path, json={"note": NOTE}, headers=who.headers)
    assert res.status_code == 409 and res.json()["code"] == "already_requested"

    assert await decisions(session, ring.case_id) == ["request_explanation"] * 2
    assert await audit_actions(session, ring.case_id) == ["flag_case.explanation_requested"] * 2
    assert (await case_row(session, ring.case_id)).status == FlagCaseStatus.OPEN

    await publish_fraud(session, explanation={"reply_days": 3})
    await session.commit()
    other = await reciprocal_case(session)
    res = await client.post(
        f"/api/admin/fraud/cases/{other.case_id}/explanations",
        json={"note": NOTE},
        headers=who.headers,
    )
    first = res.json()["explanations"][0]
    gap = datetime.fromisoformat(first["reply_due_at"]) - datetime.fromisoformat(
        first["requested_at"]
    )
    assert gap == timedelta(days=3)


# --------------------------------------------------- conflict of interest (D90)


async def _hidden(client: AsyncClient, who: Any, case_id: uuid.UUID) -> None:
    listed = (await client.get("/api/admin/fraud/cases", headers=who.headers)).json()
    assert str(case_id) not in {i["case_id"] for i in listed["items"]}
    status = (await client.get("/api/admin/fraud/status", headers=who.headers)).json()
    base = f"/api/admin/fraud/cases/{case_id}"
    for method, path, body in (
        ("GET", base, None),
        ("POST", f"{base}/park", {"note": NOTE}),
        ("POST", f"{base}/unpark", {"note": NOTE}),
        ("POST", f"{base}/decision", dismiss_body()),
        ("POST", f"{base}/explanations", {"note": NOTE}),
    ):
        res = await client.request(method, path, json=body, headers=who.headers)
        assert res.status_code == 404 and res.json()["code"] == "case_not_found", path
    assert isinstance(status["open_cases_by_type"], dict)


async def _counts(client: AsyncClient, who: Any) -> int:
    body = (await client.get("/api/admin/fraud/cases", headers=who.headers)).json()
    return int(body["counts"]["open"])


async def test_a_subject_admin_cannot_see_their_case(
    client: AsyncClient, session: AsyncSession
) -> None:
    pair = await reciprocal_case(session)
    flagged = await with_role(session, pair.a, Role.ADMIN)
    other = await admin(session)
    assert await _counts(client, other) == await _counts(client, flagged) + 1
    await _hidden(client, flagged, pair.case_id)
    assert (await case_row(session, pair.case_id)).status == FlagCaseStatus.OPEN
    assert await decisions(session, pair.case_id) == []


async def test_an_admin_who_reported_a_case_deed_is_conflicted(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A content case's subject is the doer only; its reporter is still conflicted, even when
    the deed is older than the lookback."""
    reporter, doer = await members(session, 2)
    old = await deed(session, reporter, doer, days_ago(200))
    case_id = await open_case(
        session, FlagCaseType.CONTENT, [doer], [old], {"text_check": "spam_or_empty"}
    )
    who = await with_role(session, reporter, Role.ADMIN)
    await _hidden(client, who, case_id)
    other = await admin(session)
    res = await client.get(f"/api/admin/fraud/cases/{case_id}", headers=other.headers)
    assert res.status_code == 200
    assert "may be spam or empty" in res.json()["case"]["summary"]


@pytest.mark.parametrize(("age_days", "conflicted"), [(89, True), (91, False)])
async def test_lookback_for_deeds_with_a_subject(
    client: AsyncClient, session: AsyncSession, age_days: int, conflicted: bool
) -> None:
    pair = await reciprocal_case(session)
    who = await admin(session)
    await deed(session, who, pair.b, days_ago(age_days))
    await session.commit()
    if conflicted:
        await _hidden(client, who, pair.case_id)
    else:
        res = await client.get(f"/api/admin/fraud/cases/{pair.case_id}", headers=who.headers)
        assert res.status_code == 200
        listed = (await client.get("/api/admin/fraud/cases", headers=who.headers)).json()
        assert str(pair.case_id) in {i["case_id"] for i in listed["items"]}


async def test_lookback_comes_from_config(client: AsyncClient, session: AsyncSession) -> None:
    pair = await reciprocal_case(session)
    who = await admin(session)
    await deed(session, pair.a, who, days_ago(40))
    await session.commit()
    await _hidden(client, who, pair.case_id)
    await publish_fraud(session, coi_lookback_days=30)
    await session.commit()
    res = await client.get(f"/api/admin/fraud/cases/{pair.case_id}", headers=who.headers)
    assert res.status_code == 200
