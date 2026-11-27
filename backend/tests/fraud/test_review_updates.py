"""``MemberReviewUpdate`` at the five points, the reminder job and ``ReviewsSeen``
(docs/M6-DESIGN.md D112, PO Q91; member-safe payloads only, M5 D101)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent
from app.modules.fraud import _review
from app.modules.fraud._limits import load_fraud
from app.modules.fraud._reminders import remind_explanations
from app.modules.fraud.models import ExplanationReminder, ExplanationRequest
from app.modules.fraud.schemas import DecisionIn
from tests.deeds.helpers import Member
from tests.fraud.appeal_helpers import (
    appeal,
    appeal_of,
    confirm,
    decide_appeal,
    notice_of,
    pair,
    points_overridden,
)
from tests.fraud.review_helpers import NOTE, admin, ask, reciprocal_case, reply, ring_case

UPDATE = "fraud.member_review_update"
SAFE_KEYS = {"user_id", "kind", "item_id", "reply_due_at"}


# The order updates happen in a test (one outer transaction gives every outbox row the same
# ``occurred_at``, so sort by kind instead).
ORDER = [
    "explanation_requested",
    "explanation_reminder",
    "review_closed",
    "decision_notice",
    "appeal_decided",
]


async def updates(session: AsyncSession, person: Member | None = None) -> list[dict[str, Any]]:
    stmt = select(OutboxEvent.payload).where(OutboxEvent.event_type == UPDATE)
    if person is not None:
        stmt = stmt.where(OutboxEvent.payload["user_id"].astext == str(person.user_id))
    rows = list(await session.scalars(stmt))
    return sorted(rows, key=lambda u: (ORDER.index(u["kind"]), u["item_id"]))


async def request_of(
    session: AsyncSession, case_id: uuid.UUID, person: Member
) -> ExplanationRequest:
    row = await session.scalar(
        select(ExplanationRequest)
        .where(ExplanationRequest.case_id == case_id, ExplanationRequest.user_id == person.user_id)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def dismiss(session: AsyncSession, who: Member, case_id: uuid.UUID) -> None:
    """Dismiss through the service, as the decision endpoint does."""
    _, config = await load_fraud(session)
    await _review.decide(
        session,
        who.user_id,
        case_id,
        DecisionIn(outcome="dismissed", note=NOTE),
        config=config,
        now=datetime.now(UTC),
    )
    await session.commit()


def assert_member_safe(payloads: list[dict[str, Any]], case_id: uuid.UUID) -> None:
    for payload in payloads:
        assert set(payload) <= SAFE_KEYS, payload
        assert str(case_id) not in {str(v) for v in payload.values()}


async def test_nothing_when_a_case_opens_or_is_dismissed_without_asking(
    session: AsyncSession,
) -> None:
    who = await admin(session)
    p = await reciprocal_case(session)
    assert await updates(session) == []
    await dismiss(session, who, p.case_id)
    assert await updates(session) == []


async def test_explanation_request_carries_its_own_id_and_due_date(session: AsyncSession) -> None:
    who = await admin(session)
    p = await reciprocal_case(session)
    await ask(session, who, p.case_id, p.a)
    request = await request_of(session, p.case_id, p.a)
    [payload] = await updates(session)
    assert payload == {
        "user_id": str(p.a.user_id),
        "kind": "explanation_requested",
        "item_id": str(request.id),
        "reply_due_at": request.reply_due_at.isoformat().replace("+00:00", "Z"),
    }
    assert await updates(session, p.b) == []
    assert_member_safe([payload], p.case_id)


async def test_dismissal_after_asking_says_finished_looking(session: AsyncSession) -> None:
    who = await admin(session)
    p = await reciprocal_case(session)
    await ask(session, who, p.case_id)
    await dismiss(session, who, p.case_id)
    for person in (p.a, p.b):
        kinds = [u["kind"] for u in await updates(session, person)]
        assert kinds == ["explanation_requested", "review_closed"]
        request = await request_of(session, p.case_id, person)
        assert (await updates(session, person))[-1]["item_id"] == str(request.id)
    assert_member_safe(await updates(session), p.case_id)


async def test_confirm_gives_notices_and_closes_for_people_without_one(
    session: AsyncSession,
) -> None:
    """Ring 0 -> 1 -> 2 -> 0: only deed 0 -> 1 is adverse, so people 0 and 1 get a decision
    notice and person 2 (asked, nothing adverse) hears "We've finished looking"."""
    who = await admin(session)
    ring = await ring_case(session)
    first, second, third = ring.deeds
    await confirm(
        session, who, ring.case_id, {first: "exclude", second: "release", third: "release"}
    )
    for person in ring.people[:2]:
        notice = await notice_of(session, ring.case_id, person)
        kinds = [(u["kind"], u["item_id"]) for u in await updates(session, person)]
        assert kinds == [
            ("explanation_requested", kinds[0][1]),
            ("decision_notice", str(notice.id)),
        ]
    request = await request_of(session, ring.case_id, ring.people[2])
    assert [(u["kind"], u["item_id"]) for u in await updates(session, ring.people[2])] == [
        ("explanation_requested", str(request.id)),
        ("review_closed", str(request.id)),
    ]
    assert_member_safe(await updates(session), ring.case_id)


async def test_appeal_decision_and_override_notice(
    client: AsyncClient, session: AsyncSession
) -> None:
    who, second = await admin(session), await admin(session)
    p = await pair(session)
    await confirm(session, who, p.case_id, {p.a_to_b: "reject", p.b_to_a: "release"})
    notice = await notice_of(session, p.case_id, p.a)
    assert (await appeal(client, p.a, notice.id)).status_code == 200
    filed = await appeal_of(session, notice.id)
    assert (await decide_appeal(client, second, filed.id, "upheld")).status_code == 200
    last = (await updates(session, p.a))[-1]
    assert last == {
        "user_id": str(p.a.user_id),
        "kind": "appeal_decided",
        "item_id": str(notice.id),
        "reply_due_at": None,
    }

    await points_overridden(session, deed_id=p.b_to_a, user=p.b, actor=who)
    last = (await updates(session, p.b))[-1]
    assert last["kind"] == "decision_notice" and last["reply_due_at"] is None
    # A rise (or an existing notice) emits nothing more.
    before = len(await updates(session, p.b))
    await points_overridden(session, deed_id=p.b_to_a, user=p.b, actor=who, old_points=4)
    assert len(await updates(session, p.b)) == before
    assert {u["kind"] for u in await updates(session, p.b)} == {
        "explanation_requested",
        "decision_notice",
    }


async def test_reminder_once_before_due_and_only_without_a_reply(session: AsyncSession) -> None:
    who = await admin(session)
    p = await reciprocal_case(session)
    _, config = await load_fraud(session)
    days = config.explanation.reply_days
    lead = config.explanation.reminder_days_before_due
    asked_at = datetime.now(UTC)
    await ask(session, who, p.case_id, now=asked_at)
    await reply(session, p.case_id, p.b)
    due = asked_at + timedelta(days=days)

    # Too early: nothing.
    assert (
        await remind_explanations(session, config=config, now=due - timedelta(days=lead + 1)) == 0
    )
    # Inside the window: a only (b replied), once.
    at = due - timedelta(days=lead) + timedelta(minutes=1)
    assert await remind_explanations(session, config=config, now=at) == 1
    assert await remind_explanations(session, config=config, now=at + timedelta(hours=1)) == 0
    await session.commit()
    reminders = [u for u in await updates(session) if u["kind"] == "explanation_reminder"]
    request = await request_of(session, p.case_id, p.a)
    assert reminders == [
        {
            "user_id": str(p.a.user_id),
            "kind": "explanation_reminder",
            "item_id": str(request.id),
            "reply_due_at": request.reply_due_at.isoformat().replace("+00:00", "Z"),
        }
    ]
    rows = list(await session.scalars(select(ExplanationReminder.request_id)))
    assert request.id in rows


async def test_no_reminder_after_due_or_once_the_case_is_resolved(session: AsyncSession) -> None:
    who = await admin(session)
    p = await reciprocal_case(session)
    _, config = await load_fraud(session)
    asked_at = datetime.now(UTC)
    await ask(session, who, p.case_id, now=asked_at)
    due = asked_at + timedelta(days=config.explanation.reply_days)
    assert await remind_explanations(session, config=config, now=due + timedelta(minutes=1)) == 0
    await dismiss(session, who, p.case_id)
    lead = config.explanation.reminder_days_before_due
    assert await remind_explanations(session, config=config, now=due - timedelta(days=lead)) == 0


async def test_opening_reviews_emits_reviews_seen(
    client: AsyncClient, session: AsyncSession
) -> None:
    p = await reciprocal_case(session)
    res = await client.post("/api/reviews/seen", headers=p.a.headers)
    assert res.status_code == 204
    rows = list(
        await session.scalars(
            select(OutboxEvent.payload).where(OutboxEvent.event_type == "fraud.reviews_seen")
        )
    )
    assert len(rows) == 1 and set(rows[0]) == {"user_id", "seen_at"}
    assert rows[0]["user_id"] == str(p.a.user_id)
