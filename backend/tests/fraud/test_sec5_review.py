"""SEC5 review fixes (docs/M5-SEC5.md M1, M4, L5, L6; PO Q82): the audit view hides rows about
cases the admin is conflicted on, revocation waits for every co-subject on shared deeds, member
rate limits for /api/reviews, and finalize idempotency without the outbox."""

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.events import OutboxEvent
from app.modules.fraud import service as fraud
from app.modules.fraud.models import FlagRejectionSent
from app.modules.identity.models import Role
from tests.fraud.appeal_helpers import (
    FINAL_EVENT,
    appeal,
    appeal_of,
    confirm,
    decide_appeal,
    final_events,
    finalize,
    notice_of,
    pair,
)
from tests.fraud.helpers import days_ago, deed, publish_fraud
from tests.fraud.review_helpers import NOTE, admin, with_role

LATER = timedelta(days=31)
AUDIT = "/api/admin/audit"


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------- M1: audit view and conflicts (Q82)


async def _audit_for(client: AsyncClient, who: object, entity_type: str) -> list[dict[str, object]]:
    headers = who.headers  # type: ignore[attr-defined]
    res = await client.get(AUDIT, params={"entity_type": entity_type}, headers=headers)
    assert res.status_code == 200, res.text
    items: list[dict[str, object]] = res.json()["items"]
    return items


async def test_a_conflicted_admin_sees_no_audit_rows_about_the_case(
    client: AsyncClient, session: AsyncSession
) -> None:
    decider = await admin(session)
    reviewer = await admin(session)
    friend = await admin(session)
    p = await pair(session)
    # ``friend`` recognised a recently: conflicted for the case (D90).
    await deed(session, friend, p.a, days_ago(3))
    await session.commit()

    await confirm(session, decider, p.case_id, {p.a_to_b: "exclude", p.b_to_a: "release"})
    notice = await notice_of(session, p.case_id, p.a)
    assert (await appeal(client, p.a, notice.id)).status_code == 200
    appeal_id = (await appeal_of(session, notice.id)).id
    assert (await decide_appeal(client, reviewer, appeal_id, "upheld")).status_code == 200
    # Rows that name the case another way: a notice row, and a row with ``case_id``.
    for entity_type, entity_id, value in (
        ("decision_notice", str(notice.id), {"kind": "case"}),
        ("deed", str(p.a_to_b), {"case_id": str(p.case_id)}),
    ):
        record_audit(
            session,
            entity_type=entity_type,
            entity_id=entity_id,
            action=f"{entity_type}.test",
            actor_id=decider.user_id,
            new_value=value,
            reason=NOTE,
        )
    unrelated = record_audit(
        session,
        entity_type="deed",
        entity_id=str(uuid.uuid4()),
        action="deed.test",
        actor_id=decider.user_id,
        new_value={"case_id": None},
    )
    await session.commit()

    kinds = ("flag_case", "appeal", "decision_notice", "deed")
    seen = {k: await _audit_for(client, reviewer, k) for k in kinds}
    assert any(i["entity_id"] == str(p.case_id) for i in seen["flag_case"])
    assert any(i["entity_id"] == str(appeal_id) for i in seen["appeal"])
    assert any(i["entity_id"] == str(notice.id) for i in seen["decision_notice"])
    assert any(i["entity_id"] == str(p.a_to_b) for i in seen["deed"])

    hidden = {k: await _audit_for(client, friend, k) for k in kinds}
    assert not any(i["entity_id"] == str(p.case_id) for i in hidden["flag_case"])
    assert not any(i["entity_id"] == str(appeal_id) for i in hidden["appeal"])
    assert not any(i["entity_id"] == str(notice.id) for i in hidden["decision_notice"])
    assert not any(i["entity_id"] == str(p.a_to_b) for i in hidden["deed"])
    # Rows about no case (or a null case id) are still there; filters by id find nothing.
    assert any(i["id"] == str(unrelated.id) for i in hidden["deed"])
    by_id = await client.get(AUDIT, params={"entity_id": str(p.case_id)}, headers=friend.headers)
    assert by_id.json()["items"] == []
    # A subject who is also an admin sees nothing about their own case either.
    subject = p.a

    await with_role(session, subject, Role.ADMIN)
    own = await _audit_for(client, subject, "flag_case")
    assert not any(i["entity_id"] == str(p.case_id) for i in own)


# --------------------------------------------------- M4: revocation waits for co-subjects


async def test_a_co_subjects_open_appeal_blocks_revocation_and_an_overturn_ends_it(
    client: AsyncClient, session: AsyncSession
) -> None:
    decider = await admin(session)
    reviewer = await admin(session)
    p = await pair(session)
    # Both shared deeds excluded: a and b each get a notice.
    await confirm(session, decider, p.case_id, {p.a_to_b: "exclude", p.b_to_a: "exclude"})
    notice_b = await notice_of(session, p.case_id, p.b)
    assert (await appeal(client, p.b, notice_b.id)).status_code == 200
    # a's window closes with no appeal: a's notice is final, b's waits for the appeal.
    await finalize(session, _now() + LATER)
    assert (await notice_of(session, p.case_id, p.a)).final_at is not None
    assert (await notice_of(session, p.case_id, p.b)).final_at is None

    assert not await fraud.confirmed_case_for_user(session, p.case_id, p.a.user_id)
    assert await fraud.case_standing(session, p.case_id, p.a.user_id) == "not_final"

    appeal_id = (await appeal_of(session, notice_b.id)).id
    assert (await decide_appeal(client, reviewer, appeal_id, "overturned")).status_code == 200
    # The shared deeds were restored for both: the case no longer allows revoking a's titles.
    assert not await fraud.confirmed_case_for_user(session, p.case_id, p.a.user_id)
    assert await fraud.case_standing(session, p.case_id, p.a.user_id) == "not_confirmed"


async def test_revocation_allowed_once_every_co_subject_is_final(
    client: AsyncClient, session: AsyncSession
) -> None:
    decider = await admin(session)
    reviewer = await admin(session)
    p = await pair(session)
    await confirm(session, decider, p.case_id, {p.a_to_b: "exclude", p.b_to_a: "exclude"})
    notice_b = await notice_of(session, p.case_id, p.b)
    assert (await appeal(client, p.b, notice_b.id)).status_code == 200
    await finalize(session, _now() + LATER)
    appeal_id = (await appeal_of(session, notice_b.id)).id
    assert (await decide_appeal(client, reviewer, appeal_id, "upheld")).status_code == 200
    assert await fraud.confirmed_case_for_user(session, p.case_id, p.a.user_id)
    assert await fraud.confirmed_case_for_user(session, p.case_id, p.b.user_id)
    assert await fraud.case_standing(session, p.case_id, p.a.user_id) == "final"


async def test_case_has_subject_and_covers_month(session: AsyncSession) -> None:
    p = await pair(session, first_days_ago=40)
    assert await fraud.case_has_subject(session, p.case_id, p.a.user_id)
    assert not await fraud.case_has_subject(session, p.case_id, uuid.uuid4())
    assert not await fraud.case_has_subject(session, uuid.uuid4(), p.a.user_id)

    ist = (datetime.now(UTC) - timedelta(days=40)).astimezone(ZoneInfo("Asia/Kolkata"))
    month = date(ist.year, ist.month, 1)
    assert await fraud.case_covers_month(session, p.case_id, month)
    assert await fraud.case_covers_month(session, p.case_id, month.replace(day=15))
    assert not await fraud.case_covers_month(session, p.case_id, date(2001, 1, 1))
    assert not await fraud.case_covers_month(session, uuid.uuid4(), month)


# --------------------------------------------------- L5: member rate limits


async def test_member_reads_and_writes_have_their_own_limits(
    client: AsyncClient, session: AsyncSession
) -> None:
    await publish_fraud(session, rate_member_read="2/60", rate_member_write="1/60")
    await session.commit()
    p = await pair(session)
    h = p.a.headers
    assert (await client.get("/api/reviews", headers=h)).status_code == 200
    assert (await client.post("/api/reviews/seen", headers=h)).status_code == 204
    assert (await client.get("/api/reviews", headers=h)).status_code == 429
    assert (await client.post("/api/reviews/seen", headers=h)).status_code == 429

    unknown = uuid.uuid4()
    first = await client.post(
        f"/api/reviews/explanations/{unknown}/reply", json={"reply": "Hello"}, headers=h
    )
    assert first.status_code == 404
    second = await client.post(
        f"/api/reviews/notices/{unknown}/appeal", json={"reason": "x" * 20}, headers=h
    )
    assert second.status_code == 429
    # Another member has their own buckets.
    assert (await client.get("/api/reviews", headers=p.b.headers)).status_code == 200


# --------------------------------------------------- L6: finalize without the outbox


async def test_finalize_never_resends_even_if_the_outbox_is_pruned(
    session: AsyncSession,
) -> None:
    decider = await admin(session)
    p = await pair(session)
    await confirm(session, decider, p.case_id, {p.a_to_b: "reject", p.b_to_a: "release"})
    result = await finalize(session, _now() + LATER)
    assert result.rejected == {p.case_id: [p.a_to_b]}
    sent = list(
        await session.scalars(
            select(FlagRejectionSent.deed_id).where(FlagRejectionSent.case_id == p.case_id)
        )
    )
    assert sent == [p.a_to_b]

    # A retention job prunes the outbox: the marker still stops a re-send.
    await session.execute(delete(OutboxEvent).where(OutboxEvent.event_type == FINAL_EVENT))
    await session.commit()
    assert await final_events(session, p.case_id) == []
    assert (await finalize(session, _now() + 2 * LATER)).rejected == {}
    assert await final_events(session, p.case_id) == []
