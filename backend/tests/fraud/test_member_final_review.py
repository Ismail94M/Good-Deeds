"""Member texts and case summaries fixed after the final M5 review (D100, D101):

- an upheld appeal whose deeds were restored by another person's overturned appeal says so;
- an explanation request is ``closed`` once the case is decided, replied or not;
- the queue summary uses "1 day" / "once" and a neutral line when no subject has a name.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.fraud import _review
from app.modules.fraud.models import FlagCase, FlagCaseType
from tests.deeds.helpers import Member, members
from tests.fraud.appeal_helpers import appeal, appeal_of, confirm, decide_appeal, notice_of, pair
from tests.fraud.helpers import DAY, days_ago, deed
from tests.fraud.review_helpers import admin, ask, dismiss_body, open_case, reply

ALL_RESTORED = "We looked again. Your deeds were restored after another review."
SOME_RESTORED = "We looked again. Some of your deeds were restored; the rest of the decision stays."
STAYS = "We looked again and the decision stays"
NO_CHANGES = "We've finished looking. No changes."


async def _reviews(client: AsyncClient, person: Member) -> dict[str, Any]:
    res = await client.get("/api/reviews", headers=person.headers)
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


async def _decide(
    client: AsyncClient,
    session: AsyncSession,
    reviewer: Member,
    case_id: uuid.UUID,
    person: Member,
    outcome: str,
) -> None:
    notice = await notice_of(session, case_id, person)
    assert (await appeal(client, person, notice.id)).status_code == 200
    appeal_id = (await appeal_of(session, notice.id)).id
    assert (await decide_appeal(client, reviewer, appeal_id, outcome)).status_code == 200


async def test_upheld_appeal_with_all_deeds_restored_by_another_says_so(
    client: AsyncClient, session: AsyncSession
) -> None:
    decider, reviewer = await admin(session), await admin(session)
    p = await pair(session)
    await confirm(session, decider, p.case_id, {p.a_to_b: "exclude", p.b_to_a: "exclude"})
    await _decide(client, session, reviewer, p.case_id, p.a, "upheld")
    (notice,) = (await _reviews(client, p.a))["notices"]
    assert notice["appeal"]["outcome_text"] == STAYS
    await _decide(client, session, reviewer, p.case_id, p.b, "overturned")

    (notice,) = (await _reviews(client, p.a))["notices"]
    assert {i["effect_text"] for i in notice["items"]} == {"Restored after another review"}
    assert notice["appeal"]["outcome"] == "upheld"
    assert notice["appeal"]["outcome_text"] == ALL_RESTORED


async def test_upheld_appeal_with_some_deeds_restored_by_another_says_so(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A 3-ring: c's overturn restores b->c and c->a; a's own a->b stays excluded."""
    decider, reviewer = await admin(session), await admin(session)
    a, b, c = await members(session, 3)
    t0 = days_ago(6)
    ab = await deed(session, a, b, t0)
    bc = await deed(session, b, c, t0 + DAY)
    ca = await deed(session, c, a, t0 + 2 * DAY)
    case_id = await open_case(session, FlagCaseType.RING, [a, b, c], [ab, bc, ca])
    await confirm(session, decider, case_id, {ab: "exclude", bc: "exclude", ca: "exclude"})
    await _decide(client, session, reviewer, case_id, a, "upheld")
    await _decide(client, session, reviewer, case_id, c, "overturned")

    (notice,) = (await _reviews(client, a))["notices"]
    effects = {i["deed"]["deed_id"]: i["effect_text"] for i in notice["items"]}
    assert effects == {
        str(ab): "Not counted for leaderboards",
        str(ca): "Restored after another review",
    }
    assert notice["appeal"]["outcome_text"] == SOME_RESTORED


async def test_a_replied_request_is_closed_once_the_case_is_decided(
    client: AsyncClient, session: AsyncSession
) -> None:
    who = await admin(session)
    p = await pair(session)
    await ask(session, who, p.case_id)
    await reply(session, p.case_id, p.a)
    (request,) = (await _reviews(client, p.a))["explanations"]
    assert request["status"] == "replied" and request["outcome_text"] is None
    res = await client.post(
        f"/api/admin/fraud/cases/{p.case_id}/decision", json=dismiss_body(), headers=who.headers
    )
    assert res.status_code == 200
    (request,) = (await _reviews(client, p.a))["explanations"]
    assert request["status"] == "closed" and request["outcome_text"] == NO_CHANGES
    assert request["reply"] is not None


# --------------------------------------------------- queue summaries


@dataclass(frozen=True)
class _Person:
    handle: str
    display_name: str
    avatar_ref: str | None = None


def _case(case_type: FlagCaseType, deed_count: int, signal: dict[str, Any] | None = None) -> Any:
    return FlagCase(id=uuid.uuid4(), type=case_type, deed_count=deed_count, signal=signal or {})


def _view(at: datetime) -> Any:
    @dataclass(frozen=True)
    class View:
        verified_at: datetime
        doer_id: uuid.UUID | None = None

    return View(verified_at=at)


def test_summary_plurals_and_neutral_fallback() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    people = {a: _Person("priya", "Priya"), b: _Person("ahmed", "Ahmed")}
    now = datetime.now(UTC)
    one_day = [_view(now), _view(now + timedelta(hours=3))]
    summary = _review._summary(_case(FlagCaseType.RECIPROCAL, 1), {a, b}, one_day, people)
    assert summary == "Ahmed and Priya recognised each other once in 1 day"
    three = [_view(now), _view(now + timedelta(days=3))]
    summary = _review._summary(_case(FlagCaseType.RECIPROCAL, 4), {a, b}, three, people)
    assert summary == "Ahmed and Priya recognised each other 4 times in 3 days"
    ring = _review._summary(_case(FlagCaseType.RING, 1), {a, b}, one_day, people)
    assert ring == "Ahmed and Priya recognised each other in a ring (1 deed)"

    # No names (no subjects, or none of them with a name): neutral, capitalised.
    for subjects in (set(), {uuid.uuid4()}):
        for case_type in (FlagCaseType.RECIPROCAL, FlagCaseType.RING):
            text = _review._summary(_case(case_type, 3), subjects, one_day, people)
            assert text == "Unusual pattern: 3 deeds"
    assert _review._summary(_case(FlagCaseType.RING, 1), set(), [], {}) == "Unusual pattern: 1 deed"
