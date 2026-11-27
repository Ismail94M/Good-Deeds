"""Decision notices, appeals and making decisions final (docs/M5-DESIGN.md D89, D90, D100,
§3.3 #11, #12, "Appeal decision", PO Q78, §8 "Notices and appeals")."""

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.events import OutboxEvent
from app.modules.deeds.models import Deed, DeedStatus
from app.modules.feed.models import FeedPost, PostState
from app.modules.fraud._limits import load_fraud
from app.modules.fraud.models import (
    DecisionNotice,
    HoldReleaseReason,
    NoticeKind,
    PrizeHold,
)
from app.modules.identity.models import Role
from app.modules.scoring.models import PointsLedger
from tests.deeds.helpers import Member, count, make_member, outbox
from tests.fraud.appeal_helpers import (
    APPEAL_EVENT,
    HOLD_EVENT,
    appeal,
    appeal_of,
    confirm,
    decide_appeal,
    drain,
    final_events,
    finalize,
    notice_of,
    pair,
    points_overridden,
)
from tests.fraud.helpers import days_ago, deed, publish_fraud
from tests.fraud.review_helpers import NOTE, admin, with_role

LATER = timedelta(days=31)


def _now() -> datetime:
    return datetime.now(UTC)


async def _status(session: AsyncSession, deed_id: uuid.UUID) -> DeedStatus:
    status = await session.scalar(
        select(Deed.status).where(Deed.id == deed_id).execution_options(populate_existing=True)
    )
    assert status is not None
    return status


async def _active_holds(session: AsyncSession, deed_id: uuid.UUID) -> int:
    return await count(
        session, PrizeHold, PrizeHold.deed_id == deed_id, PrizeHold.released_at.is_(None)
    )


# --------------------------------------------------- the member's appeal (M3)


async def test_appeal_within_the_window_once(client: AsyncClient, session: AsyncSession) -> None:
    who = await admin(session)
    p = await pair(session)
    await confirm(session, who, p.case_id, {p.a_to_b: "reject", p.b_to_a: "release"})
    notice = await notice_of(session, p.case_id, p.a)

    res = await appeal(client, p.a, notice.id)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == str(notice.id)
    assert body["can_appeal"] is False and body["final"] is False
    assert body["appeal"]["outcome"] is None and body["appeal"]["reason"].startswith("We are")

    again = await appeal(client, p.a, notice.id)
    assert again.status_code == 409 and again.json()["code"] == "already_appealed"
    # Someone else's notice is 404, also for the other person in the case.
    other = await appeal(client, p.b, notice.id)
    assert other.status_code == 404
    assert (
        await client.post(
            f"/api/reviews/notices/{uuid.uuid4()}/appeal",
            json={"reason": "x" * 20},
            headers=p.a.headers,
        )
    ).status_code == 404


async def test_appeal_after_the_window_is_closed(
    client: AsyncClient, session: AsyncSession
) -> None:
    who = await admin(session)
    p = await pair(session, first_days_ago=60)
    await confirm(
        session, who, p.case_id, {p.a_to_b: "exclude", p.b_to_a: "release"}, at=days_ago(31)
    )
    notice = await notice_of(session, p.case_id, p.a)
    res = await appeal(client, p.a, notice.id)
    assert res.status_code == 409 and res.json()["code"] == "appeal_closed"


async def test_appeal_reason_bounds_come_from_config(
    client: AsyncClient, session: AsyncSession
) -> None:
    who = await admin(session)
    p = await pair(session)
    await confirm(session, who, p.case_id, {p.a_to_b: "exclude", p.b_to_a: "release"})
    notice = await notice_of(session, p.case_id, p.a)
    short = await appeal(client, p.a, notice.id, reason="   too short   ")
    assert short.status_code == 422 and short.json()["code"] == "reason_required"
    await publish_fraud(session, appeal={"reason_max_chars": 30})
    await session.commit()
    long = await appeal(client, p.a, notice.id, reason="x" * 31)
    assert long.status_code == 422 and long.json()["code"] == "reason_too_long"
    assert (await appeal(client, p.a, notice.id, reason="x" * 30)).status_code == 200


# --------------------------------------------------- who may decide (#11, #12)


async def test_decider_and_conflicted_admins_cannot_see_the_appeal(
    client: AsyncClient, session: AsyncSession
) -> None:
    decider = await admin(session)
    p = await pair(session)
    await confirm(session, decider, p.case_id, {p.a_to_b: "exclude", p.b_to_a: "release"})
    notice = await notice_of(session, p.case_id, p.a)
    assert (await appeal(client, p.a, notice.id)).status_code == 200
    appeal_id = (await appeal_of(session, notice.id)).id

    conflicted = await admin(session)
    await deed(session, conflicted, p.a, days_ago(10))  # recognised a 10 days ago (D90)
    await session.commit()
    reviewer = await admin(session)

    for who in (decider, conflicted):
        listed = await client.get("/api/admin/fraud/appeals", headers=who.headers)
        assert listed.status_code == 200
        assert listed.json()["items"] == [] and listed.json()["counts"]["open"] == 0
        detail = await client.get(f"/api/admin/fraud/appeals/{appeal_id}", headers=who.headers)
        assert detail.status_code == 404
        res = await decide_appeal(client, who, appeal_id, "overturned")
        assert res.status_code == 404
    # The appellant (even as an admin) and moderators can't either.
    moderator = await with_role(session, await make_member(session), Role.MODERATOR)
    assert (
        await client.get("/api/admin/fraud/appeals", headers=moderator.headers)
    ).status_code == 403
    assert (await client.get("/api/admin/fraud/appeals", headers=p.a.headers)).status_code == 403

    listed = await client.get("/api/admin/fraud/appeals", headers=reviewer.headers)
    body = listed.json()
    assert body["counts"] == {"open": 1, "decided": 0}
    (row,) = body["items"]
    assert row["appeal_id"] == str(appeal_id) and row["kind"] == "case"
    assert row["case_id"] == str(p.case_id) and row["deed_count"] == 1
    assert row["user"]["handle"] == p.a.handle
    assert row["original_decider"]["handle"] == decider.handle
    detail = await client.get(f"/api/admin/fraud/appeals/{appeal_id}", headers=reviewer.headers)
    assert detail.status_code == 200
    d = detail.json()
    assert d["reason"].startswith("We are") and d["notice"]["items"][0]["effect"] == "excluded"
    assert d["notice"]["items"][0]["deed_id"] == str(p.a_to_b)
    assert d["outcome"] is None

    # Nothing changed for the refused admins.
    assert (await appeal_of(session, notice.id)).outcome is None
    res = await decide_appeal(client, reviewer, appeal_id, "upheld", note="short")
    assert res.status_code == 422 and res.json()["code"] == "note_required"


async def test_appeals_queue_tabs_and_cursor(client: AsyncClient, session: AsyncSession) -> None:
    decider = await admin(session)
    reviewer = await admin(session)
    ids: list[uuid.UUID] = []
    for _ in range(3):
        p = await pair(session)
        await confirm(session, decider, p.case_id, {p.a_to_b: "exclude", p.b_to_a: "release"})
        notice = await notice_of(session, p.case_id, p.a)
        assert (await appeal(client, p.a, notice.id)).status_code == 200
        ids.append((await appeal_of(session, notice.id)).id)
    await publish_fraud(session, page_size=2)
    await session.commit()
    first = (await client.get("/api/admin/fraud/appeals", headers=reviewer.headers)).json()
    assert [i["appeal_id"] for i in first["items"]] == [str(i) for i in ids[:2]]
    assert first["next_cursor"]
    second = (
        await client.get(
            "/api/admin/fraud/appeals",
            params={"cursor": first["next_cursor"]},
            headers=reviewer.headers,
        )
    ).json()
    assert [i["appeal_id"] for i in second["items"]] == [str(ids[2])]
    assert second["next_cursor"] is None
    bad = await client.get(
        "/api/admin/fraud/appeals",
        params={"status": "decided", "cursor": first["next_cursor"]},
        headers=reviewer.headers,
    )
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_cursor"

    assert (await decide_appeal(client, reviewer, ids[1], "upheld")).status_code == 200
    decided = (
        await client.get(
            "/api/admin/fraud/appeals", params={"status": "decided"}, headers=reviewer.headers
        )
    ).json()
    assert decided["counts"] == {"open": 2, "decided": 1}
    assert [i["appeal_id"] for i in decided["items"]] == [str(ids[1])]
    assert decided["items"][0]["outcome"] == "upheld"


# --------------------------------------------------- overturn and uphold


async def _shown_held(client: AsyncClient, who: Member, case_id: uuid.UUID) -> int:
    """The held count an admin sees on the case page, checked against the resolved queue."""
    page = await client.get(f"/api/admin/fraud/cases/{case_id}", headers=who.headers)
    assert page.status_code == 200, page.text
    queue = await client.get(
        "/api/admin/fraud/cases", params={"status": "resolved"}, headers=who.headers
    )
    (row,) = [c for c in queue.json()["items"] if c["case_id"] == str(case_id)]
    assert row["held_count"] == page.json()["case"]["held_count"]
    return int(row["held_count"])


async def test_overturn_releases_holds_and_cancels_the_rejection(
    client: AsyncClient, session: AsyncSession
) -> None:
    decider = await admin(session)
    reviewer = await admin(session)
    p = await pair(session)
    await confirm(session, decider, p.case_id, {p.a_to_b: "reject", p.b_to_a: "exclude"})
    assert await _active_holds(session, p.a_to_b) == 1
    assert await _shown_held(client, decider, p.case_id) == 2
    notice = await notice_of(session, p.case_id, p.a)
    assert (await appeal(client, p.a, notice.id)).status_code == 200
    appeal_id = (await appeal_of(session, notice.id)).id

    res = await decide_appeal(client, reviewer, appeal_id, "overturned")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outcome"] == "overturned" and body["reviewer"]["handle"] == reviewer.handle
    assert body["notice"]["final_at"] is not None

    holds = list(await session.scalars(select(PrizeHold).where(PrizeHold.case_id == p.case_id)))
    assert {h.deed_id for h in holds} == {p.a_to_b, p.b_to_a}
    assert all(h.release_reason == HoldReleaseReason.APPEAL_OVERTURNED for h in holds)
    # The resolved case row is frozen, but the queue and the case page count live holds.
    assert await _shown_held(client, decider, p.case_id) == 0
    for deed_id in (p.a_to_b, p.b_to_a):
        events = await outbox(session, HOLD_EVENT, deed_id)
        assert [e.payload["held"] for e in events] == [True, False]
    (decided,) = list(
        await session.scalars(
            select(OutboxEvent.payload).where(OutboxEvent.event_type == APPEAL_EVENT)
        )
    )
    assert decided == {
        "appeal_id": str(appeal_id),
        "notice_kind": "case",
        "case_id": str(p.case_id),
        "override_id": None,
        "user_id": str(p.a.user_id),
        "outcome": "overturned",
        "reviewer_id": str(reviewer.user_id),
    }
    audit = list(
        await session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == "appeal", AuditLog.entity_id == str(appeal_id)
            )
        )
    )
    assert [a.action for a in audit] == ["appeal.overturned"]
    assert audit[0].actor_id == reviewer.user_id and audit[0].reason == NOTE

    again = await decide_appeal(client, reviewer, appeal_id, "upheld")
    assert again.status_code == 409 and again.json()["code"] == "appeal_decided"

    # b never appealed; once b's window closes nothing is rejected: a's overturn cancelled it.
    result = await finalize(session, _now() + LATER)
    assert result.notices_final == 1 and result.rejected == {}
    assert await final_events(session, p.case_id) == []
    await drain(session, [p.a_to_b, p.b_to_a], [p.case_id])
    assert await _status(session, p.a_to_b) == DeedStatus.VERIFIED


async def test_uphold_then_the_rejection_runs_once_both_are_final(
    client: AsyncClient, session: AsyncSession
) -> None:
    decider = await admin(session)
    reviewer = await admin(session)
    p = await pair(session)
    await confirm(session, decider, p.case_id, {p.a_to_b: "reject", p.b_to_a: "release"})
    notice = await notice_of(session, p.case_id, p.a)
    assert (await appeal(client, p.a, notice.id)).status_code == 200
    appeal_id = (await appeal_of(session, notice.id)).id
    res = await decide_appeal(client, reviewer, appeal_id, "upheld")
    assert res.status_code == 200 and res.json()["outcome"] == "upheld"
    assert (await notice_of(session, p.case_id, p.a)).final_at is not None
    assert await _active_holds(session, p.a_to_b) == 1  # nothing released on uphold
    assert (
        len(
            list(
                await session.scalars(
                    select(OutboxEvent).where(OutboxEvent.event_type == APPEAL_EVENT)
                )
            )
        )
        == 1
    )

    # a is final, b's window is still open: nothing yet.
    assert (await finalize(session)).rejected == {}
    assert (await notice_of(session, p.case_id, p.b)).final_at is None
    # After b's window: both final -> one FlagDecisionFinal, never again.
    result = await finalize(session, _now() + LATER)
    assert result.rejected == {p.case_id: [p.a_to_b]}
    assert (await finalize(session, _now() + 2 * LATER)).rejected == {}
    (event,) = await final_events(session, p.case_id)
    assert event["rejected_deed_ids"] == [str(p.a_to_b)]
    assert event["actor_id"] == str(decider.user_id)

    await drain(session, [p.a_to_b, p.b_to_a], [p.case_id])
    assert await _status(session, p.a_to_b) == DeedStatus.REJECTED
    assert await _status(session, p.b_to_a) == DeedStatus.VERIFIED


async def test_no_appeal_final_after_the_window_then_feed_and_points_follow(
    session: AsyncSession,
) -> None:
    """No appeal: both notices become final once the window closes, the deed is rejected, its
    feed post leaves the feed and its points are reversed by the existing handlers (D89)."""
    decider = await admin(session)
    p = await pair(session)
    await drain(session, [p.a_to_b, p.b_to_a])  # verified: post published, points awarded
    post = await session.get(FeedPost, p.a_to_b)
    assert post is not None and post.state == PostState.PUBLISHED
    awarded = await session.scalar(
        select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
            PointsLedger.deed_id == p.a_to_b
        )
    )
    assert awarded and awarded > 0

    await confirm(session, decider, p.case_id, {p.a_to_b: "reject", p.b_to_a: "release"})
    # Inside the window nothing is final or rejected.
    assert (await finalize(session)).notices_final == 0
    assert await _status(session, p.a_to_b) == DeedStatus.VERIFIED

    result = await finalize(session, _now() + LATER)
    assert result.notices_final == 2
    for person in (p.a, p.b):
        assert (await notice_of(session, p.case_id, person)).final_at is not None
    assert result.rejected == {p.case_id: [p.a_to_b]}
    await drain(session, [p.a_to_b, p.b_to_a], [p.case_id])

    assert await _status(session, p.a_to_b) == DeedStatus.REJECTED
    post = await session.get(FeedPost, p.a_to_b, populate_existing=True)
    assert post is not None and post.state == PostState.REJECTED
    net = await session.scalar(
        select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
            PointsLedger.deed_id == p.a_to_b
        )
    )
    assert net == 0
    # The released deed is untouched.
    assert await _status(session, p.b_to_a) == DeedStatus.VERIFIED


async def test_an_open_appeal_waits_and_needs_a_second_admin(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Q78: with only the deciding admin, an appeal waits however long, the rejection stays
    paused, and the status page counts it."""
    decider = await admin(session)
    p = await pair(session)
    await confirm(session, decider, p.case_id, {p.a_to_b: "reject", p.b_to_a: "release"})
    notice = await notice_of(session, p.case_id, p.a)
    assert (await appeal(client, p.a, notice.id)).status_code == 200

    status = (await client.get("/api/admin/fraud/status", headers=decider.headers)).json()
    assert status["appeals_waiting_for_second_admin"] == 1
    result = await finalize(session, _now() + 3 * LATER)
    assert result.rejected == {}
    assert (await notice_of(session, p.case_id, p.a)).final_at is None
    assert (await notice_of(session, p.case_id, p.b)).final_at is not None

    # A conflicted second admin doesn't count; an eligible one does.
    conflicted = await admin(session)
    await deed(session, p.b, conflicted, days_ago(3))
    await session.commit()
    status = (await client.get("/api/admin/fraud/status", headers=decider.headers)).json()
    assert status["appeals_waiting_for_second_admin"] == 1
    await admin(session)
    status = (await client.get("/api/admin/fraud/status", headers=decider.headers)).json()
    assert status["appeals_waiting_for_second_admin"] == 0


# --------------------------------------------------- override notices (D91, D100)


async def test_a_lowering_override_creates_a_notice_once(
    client: AsyncClient, session: AsyncSession
) -> None:
    actor = await admin(session)
    p = await pair(session)
    override_id, _ = await points_overridden(session, deed_id=p.a_to_b, user=p.b, actor=actor)
    notices = list(
        await session.scalars(
            select(DecisionNotice).where(DecisionNotice.override_id == override_id)
        )
    )
    assert len(notices) == 1
    notice = notices[0]
    _, config = await load_fraud(session)
    assert notice.kind == NoticeKind.OVERRIDE and notice.case_id is None
    assert notice.user_id == p.b.user_id and notice.reason_key == "fake_photo"
    assert notice.items == [
        {"deed_id": str(p.a_to_b), "effect": "points_changed", "old_points": 12, "new_points": 8}
    ]
    assert notice.appeal_until - notice.created_at == timedelta(days=config.appeal.window_days)

    # Redelivery (same override, another event) adds nothing; raising points notifies nobody.
    await points_overridden(
        session, deed_id=p.a_to_b, user=p.b, actor=actor, override_id=override_id
    )
    raised, _ = await points_overridden(
        session, deed_id=p.a_to_b, user=p.b, actor=actor, old_points=8, new_points=12
    )
    assert await count(session, DecisionNotice, DecisionNotice.override_id == override_id) == 1
    assert await count(session, DecisionNotice, DecisionNotice.override_id == raised) == 0

    # The member sees neutral text.
    reviews = (await client.get("/api/reviews", headers=p.b.headers)).json()
    (shown,) = reviews["notices"]
    assert shown["reason_text"] == "The photo didn't show this deed."
    assert shown["items"][0]["effect_text"] == "Points changed from 12 to 8"
    assert shown["items"][0]["deed"]["role"] == "doer"
    assert shown["items"][0]["deed"]["other_person"]["handle"] == p.a.handle

    # The override's actor can't decide the appeal; another admin overturns it.
    assert (await appeal(client, p.b, notice.id)).status_code == 200
    appeal_id = (await appeal_of(session, notice.id)).id
    assert (
        await client.get(f"/api/admin/fraud/appeals/{appeal_id}", headers=actor.headers)
    ).status_code == 404
    reviewer = await admin(session)
    listed = (await client.get("/api/admin/fraud/appeals", headers=reviewer.headers)).json()
    assert listed["items"][0]["kind"] == "override"
    assert listed["items"][0]["original_decider"]["handle"] == actor.handle
    res = await decide_appeal(client, reviewer, appeal_id, "overturned")
    assert res.status_code == 200
    payloads = list(
        await session.scalars(
            select(OutboxEvent.payload).where(OutboxEvent.event_type == APPEAL_EVENT)
        )
    )
    assert payloads == [
        {
            "appeal_id": str(appeal_id),
            "notice_kind": "override",
            "case_id": None,
            "override_id": str(override_id),
            "user_id": str(p.b.user_id),
            "outcome": "overturned",
            "reviewer_id": str(reviewer.user_id),
        }
    ]
    reviews = (await client.get("/api/reviews", headers=p.b.headers)).json()
    item = reviews["notices"][0]
    assert item["items"][0]["effect_text"] == "Points restored after another review"
    assert item["appeal"]["outcome_text"] == "We looked again and restored your deeds"
    assert item["final"] is True


async def test_the_admin_who_asked_for_a_big_cut_cant_decide_its_appeal(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Q77 + D100: a second-admin-approved cut has two deciders; neither may review the appeal."""
    requester, approver = await admin(session), await admin(session)
    p = await pair(session)
    await points_overridden(
        session,
        deed_id=p.a_to_b,
        user=p.b,
        actor=approver,
        requested_by=requester,
        old_points=25,
        new_points=0,
    )
    notice = await session.scalar(
        select(DecisionNotice).where(DecisionNotice.user_id == p.b.user_id)
    )
    assert notice is not None
    assert (await appeal(client, p.b, notice.id)).status_code == 200
    appeal_id = (await appeal_of(session, notice.id)).id
    for who in (requester, approver):
        res = await client.get(f"/api/admin/fraud/appeals/{appeal_id}", headers=who.headers)
        assert res.status_code == 404
        listed = (await client.get("/api/admin/fraud/appeals", headers=who.headers)).json()
        assert listed["items"] == []
    third = await admin(session)
    res = await client.get(f"/api/admin/fraud/appeals/{appeal_id}", headers=third.headers)
    assert res.status_code == 200
