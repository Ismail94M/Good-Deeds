"""The member Reviews page (docs/M5-DESIGN.md §3.3b M1 to M4, D99 to D101, §6.2, §8 "Member
copy"): own items only, reply rules, statuses and texts, unread/seen, and a text and schema scan
that no member response names fraud, a case type, a detector, other people or an admin."""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.fraud import schemas
from app.modules.fraud._limits import load_fraud
from app.modules.fraud._member import day_text
from app.modules.fraud.models import FlagCaseType
from tests.deeds.helpers import Member, members
from tests.fraud.appeal_helpers import (
    appeal,
    appeal_of,
    confirm,
    decide_appeal,
    drain,
    finalize,
    notice_of,
    pair,
)
from tests.fraud.helpers import DAY, days_ago, deed, publish_fraud
from tests.fraud.review_helpers import NOTE, admin, ask, dismiss_body, open_case, reply

FORBIDDEN = re.compile(
    r"\b(fraud\w*|flag\w*|cases?|ring|reciprocal|concentration|content|invite_anomaly|detector\w*"
    r"|admin\w*|moderator\w*|held|holds?|reviewer\w*|decider\w*|suspicio\w*|collu\w*)\b",
    re.IGNORECASE,
)
NO_CHANGES = "We've finished looking. No changes."


async def _reviews(client: AsyncClient, person: Member) -> dict[str, Any]:
    res = await client.get("/api/reviews", headers=person.headers)
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


async def _reply(client: AsyncClient, person: Member, request_id: str, text: str) -> Any:
    return await client.post(
        f"/api/reviews/explanations/{request_id}/reply",
        json={"reply": text},
        headers=person.headers,
    )


def _scan(body: Any, *, banned: list[str]) -> None:
    text = json.dumps(body)
    assert not FORBIDDEN.search(text), FORBIDDEN.search(text)
    for value in banned:
        assert value not in text, value


# --------------------------------------------------- signed in, own items only


async def test_signed_out_is_401(client: AsyncClient, session: AsyncSession) -> None:
    rid = uuid.uuid4()
    assert (await client.get("/api/reviews")).status_code == 401
    assert (await client.post("/api/reviews/seen")).status_code == 401
    assert (
        await client.post(f"/api/reviews/explanations/{rid}/reply", json={"reply": "hi"})
    ).status_code == 401
    assert (
        await client.post(f"/api/reviews/notices/{rid}/appeal", json={"reason": "x" * 20})
    ).status_code == 401


async def test_only_own_items(client: AsyncClient, session: AsyncSession) -> None:
    who = await admin(session)
    p = await pair(session)
    await ask(session, who, p.case_id, p.a)
    outsider = (await members(session, 1))[0]

    a_view = await _reviews(client, p.a)
    (request,) = a_view["explanations"]
    assert request["status"] == "waiting" and request["outcome_text"] is None
    # Both of a's deeds, each with the other person on it.
    refs = {d["deed_id"]: d for d in request["deeds"]}
    assert set(refs) == {str(p.a_to_b), str(p.b_to_a)}
    assert refs[str(p.a_to_b)]["role"] == "reporter"
    assert refs[str(p.b_to_a)]["role"] == "doer"
    assert {d["other_person"]["handle"] for d in request["deeds"]} == {p.b.handle}
    assert all(d["category_label"] and d["area_name"] for d in request["deeds"])

    # b was not asked; an outsider sees nothing and can't touch a's request.
    assert (await _reviews(client, p.b))["explanations"] == []
    assert (await _reviews(client, outsider))["explanations"] == []
    for person in (p.b, outsider):
        res = await _reply(client, person, request["id"], "Not mine to answer.")
        assert res.status_code == 404
    assert (await _reply(client, p.a, str(uuid.uuid4()), "Hello there.")).status_code == 404


# --------------------------------------------------- M2 reply rules


async def test_reply_rules(client: AsyncClient, session: AsyncSession) -> None:
    who = await admin(session)
    p = await pair(session)
    await ask(session, who, p.case_id, p.a)
    (request,) = (await _reviews(client, p.a))["explanations"]
    rid = request["id"]

    empty = await _reply(client, p.a, rid, "   ")
    assert empty.status_code == 422 and empty.json()["code"] == "reply_required"
    await publish_fraud(session, explanation={"reply_max_chars": 20})
    await session.commit()
    long = await _reply(client, p.a, rid, "x" * 21)
    assert long.status_code == 422 and long.json()["code"] == "reply_too_long"

    ok = await _reply(client, p.a, rid, "  We are neighbours.  ")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "replied" and body["reply"] == "We are neighbours."
    assert body["replied_at"] is not None
    again = await _reply(client, p.a, rid, "Another reply.")
    assert again.status_code == 409 and again.json()["code"] == "already_replied"
    # The admin sees the reply on the case page.
    detail = (await client.get(f"/api/admin/fraud/cases/{p.case_id}", headers=who.headers)).json()
    assert detail["explanations"][0]["reply"] == "We are neighbours."


async def test_reply_after_the_due_date_is_closed(
    client: AsyncClient, session: AsyncSession
) -> None:
    who = await admin(session)
    p = await pair(session)
    await ask(session, who, p.case_id, p.a, now=datetime.now(UTC) - timedelta(days=20))
    (request,) = (await _reviews(client, p.a))["explanations"]
    assert request["status"] == "closed" and request["outcome_text"] is None
    res = await _reply(client, p.a, request["id"], "Sorry I am late.")
    assert res.status_code == 409 and res.json()["code"] == "reply_closed"


# --------------------------------------------------- statuses and outcome texts


async def test_dismissed_look_reads_no_changes(client: AsyncClient, session: AsyncSession) -> None:
    who = await admin(session)
    p = await pair(session)
    await ask(session, who, p.case_id)
    res = await client.post(
        f"/api/admin/fraud/cases/{p.case_id}/decision", json=dismiss_body(), headers=who.headers
    )
    assert res.status_code == 200
    for person in (p.a, p.b):
        view = await _reviews(client, person)
        (request,) = view["explanations"]
        assert request["status"] == "closed" and request["outcome_text"] == NO_CHANGES
        assert view["notices"] == []
        res = await _reply(client, person, request["id"], "Late but here.")
        assert res.status_code == 409 and res.json()["code"] == "reply_closed"


async def test_confirmed_with_nothing_for_this_person_reads_no_changes(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A 3-ring where only a -> b is excluded: c was asked but had no adverse deed."""
    who = await admin(session)
    a, b, c = await members(session, 3)
    t0 = days_ago(6)
    ab = await deed(session, a, b, t0)
    bc = await deed(session, b, c, t0 + DAY)
    ca = await deed(session, c, a, t0 + 2 * DAY)
    case_id = await open_case(session, FlagCaseType.RING, [a, b, c], [ab, bc, ca])
    await confirm(session, who, case_id, {ab: "exclude", bc: "release", ca: "release"})

    c_view = await _reviews(client, c)
    assert c_view["explanations"][0]["outcome_text"] == NO_CHANGES
    assert c_view["notices"] == []
    a_view = await _reviews(client, a)
    assert a_view["explanations"][0]["outcome_text"] is None
    (notice,) = a_view["notices"]
    assert [i["deed"]["deed_id"] for i in notice["items"]] == [str(ab)]
    assert notice["items"][0]["effect_text"] == "Not counted for leaderboards"
    _, config = await load_fraud(session)
    assert notice["reason_text"] == config.member_reason_labels["case"]
    assert notice["can_appeal"] is True and notice["appeal"] is None and notice["final"] is False


async def test_effect_texts_follow_the_decision(client: AsyncClient, session: AsyncSession) -> None:
    decider = await admin(session)
    reviewer = await admin(session)
    p = await pair(session)
    await confirm(session, decider, p.case_id, {p.a_to_b: "reject", p.b_to_a: "exclude"})
    _, config = await load_fraud(session)
    notice = await notice_of(session, p.case_id, p.a)
    removal = (
        f"Will be removed on {day_text(notice.appeal_until, config)} "
        "unless you ask for another review"
    )

    def effects(view: dict[str, Any]) -> dict[str, str]:
        (n,) = view["notices"]
        return {i["deed"]["deed_id"]: i["effect_text"] for i in n["items"]}

    assert effects(await _reviews(client, p.a)) == {
        str(p.a_to_b): removal,
        str(p.b_to_a): "Not counted for leaderboards",
    }
    assert (await appeal(client, p.a, notice.id)).status_code == 200
    assert effects(await _reviews(client, p.a))[str(p.a_to_b)] == "Waiting for another review"
    appeal_id = (await appeal_of(session, notice.id)).id
    assert (await decide_appeal(client, reviewer, appeal_id, "upheld")).status_code == 200
    view = await _reviews(client, p.a)
    assert effects(view)[str(p.a_to_b)] == "Will be removed after review"
    assert view["notices"][0]["appeal"]["outcome_text"] == "We looked again and the decision stays"
    assert view["notices"][0]["final"] is True and view["notices"][0]["can_appeal"] is False

    await finalize(session, datetime.now(UTC) + timedelta(days=31))
    await drain(session, [p.a_to_b, p.b_to_a], [p.case_id])
    for person in (p.a, p.b):
        assert effects(await _reviews(client, person))[str(p.a_to_b)] == "Removed after review"


async def test_overturn_reads_restored_for_both_people(
    client: AsyncClient, session: AsyncSession
) -> None:
    decider = await admin(session)
    reviewer = await admin(session)
    p = await pair(session)
    await confirm(session, decider, p.case_id, {p.a_to_b: "reject", p.b_to_a: "release"})
    notice = await notice_of(session, p.case_id, p.b)
    assert (await appeal(client, p.b, notice.id)).status_code == 200
    appeal_id = (await appeal_of(session, notice.id)).id
    assert (await decide_appeal(client, reviewer, appeal_id, "overturned")).status_code == 200
    for person in (p.a, p.b):
        (n,) = (await _reviews(client, person))["notices"]
        assert n["items"][0]["effect_text"] == "Restored after another review"


# --------------------------------------------------- M4 unread and seen


async def test_unread_counts_new_and_changed_items(
    client: AsyncClient, session: AsyncSession
) -> None:
    decider = await admin(session)
    reviewer = await admin(session)
    p = await pair(session)
    assert (await _reviews(client, p.a))["unread"] == 0
    await ask(session, decider, p.case_id, p.a)
    assert (await _reviews(client, p.a))["unread"] == 1

    res = await client.post("/api/reviews/seen", headers=p.a.headers)
    assert res.status_code == 204 and res.content == b""
    assert (await _reviews(client, p.a))["unread"] == 0
    # The member's own reply is not news to them.
    (request,) = (await _reviews(client, p.a))["explanations"]
    assert (await _reply(client, p.a, request["id"], "All good here.")).status_code == 200
    assert (await _reviews(client, p.a))["unread"] == 0

    await confirm(session, decider, p.case_id, {p.a_to_b: "exclude", p.b_to_a: "release"})
    assert (await _reviews(client, p.a))["unread"] == 1
    assert (await client.post("/api/reviews/seen", headers=p.a.headers)).status_code == 204
    assert (await client.post("/api/reviews/seen", headers=p.a.headers)).status_code == 204
    assert (await _reviews(client, p.a))["unread"] == 0

    notice = await notice_of(session, p.case_id, p.a)
    assert (await appeal(client, p.a, notice.id)).status_code == 200
    assert (await _reviews(client, p.a))["unread"] == 0
    appeal_id = (await appeal_of(session, notice.id)).id
    assert (await decide_appeal(client, reviewer, appeal_id, "upheld")).status_code == 200
    assert (await _reviews(client, p.a))["unread"] == 1


# --------------------------------------------------- D101 wording


async def test_member_responses_never_name_fraud_cases_or_other_people(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A 4-ring a -> b -> c -> d -> a: a's deeds are with b and d only, so c and the admins must
    never show up for a, and no response says fraud, case, ring, flag, detector or admin."""
    decider = await admin(session)
    reviewer = await admin(session)
    a, b, c, d = await members(session, 4)
    t0 = days_ago(8)
    people = [a, b, c, d]
    made = [await deed(session, people[i], people[(i + 1) % 4], t0 + i * DAY) for i in range(4)]
    case_id = await open_case(
        session, FlagCaseType.RING, people, made, {"size": 4, "internal_share_pct": 100}
    )
    banned = [c.handle, decider.handle, reviewer.handle, str(case_id), str(made[1]), str(made[2])]
    bodies: list[Any] = []

    await ask(session, decider, case_id, a, b)
    view = await _reviews(client, a)
    bodies.append(view)
    answer = await _reply(client, a, view["explanations"][0]["id"], "We volunteer together.")
    assert answer.status_code == 200
    bodies.append(answer.json())
    await reply(session, case_id, b)

    # Only a's deeds with b (a -> b) and d (d -> a); then a decision about a -> b and d -> a.
    await confirm(
        session,
        decider,
        case_id,
        {made[0]: "reject", made[1]: "release", made[2]: "release", made[3]: "exclude"},
    )
    view = await _reviews(client, a)
    bodies.append(view)
    notice_id = view["notices"][0]["id"]
    res = await appeal(client, a, uuid.UUID(notice_id))
    assert res.status_code == 200
    bodies.append(res.json())
    appeal_id = (await appeal_of(session, uuid.UUID(notice_id))).id
    assert (await decide_appeal(client, reviewer, appeal_id, "upheld")).status_code == 200
    bodies.append(await _reviews(client, a))
    # Errors too.
    bodies.append((await appeal(client, a, uuid.UUID(notice_id))).json())
    bodies.append((await _reply(client, a, view["explanations"][0]["id"], "Again.")).json())
    bodies.append((await _reply(client, a, str(uuid.uuid4()), "Hello there.")).json())

    for body in bodies:
        _scan(body, banned=banned)
    final = bodies[4]
    others = {
        ref["other_person"]["handle"] for e in final["explanations"] for ref in e["deeds"]
    } | {i["deed"]["other_person"]["handle"] for n in final["notices"] for i in n["items"]}
    assert others == {b.handle, d.handle}
    assert NOTE not in json.dumps(final)  # the admin's note stays on the admin side


def test_member_schemas_have_no_case_fields() -> None:
    models: list[type[BaseModel]] = [
        schemas.MyReviews,
        schemas.MyExplanation,
        schemas.MyNotice,
        schemas.MyNoticeItem,
        schemas.MyAppeal,
        schemas.MyDeedRef,
        schemas.MyReviewLimits,
    ]
    for model in models:
        text = json.dumps(model.model_json_schema())
        assert not FORBIDDEN.search(text), (model.__name__, FORBIDDEN.search(text))
