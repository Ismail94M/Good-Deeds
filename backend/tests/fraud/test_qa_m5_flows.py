"""QA10: end-to-end M5 flows across deeds, fraud, scoring and feed (docs/M5-DESIGN.md §0.0, D80,
D88, D89, D91, D93, D98 to D101, §8). Each flow starts from real verified deeds, runs the
worker's handlers as the outbox would, and checks what members, admins, the feed and the boards
see at every step:

1. detection -> nothing visible -> explanation -> reply -> confirm (reject + exclude) -> notice ->
   no appeal -> ``finalize_decisions`` -> deed rejected -> feed post gone, points reversed, board
   refresh drops it;
2. the same decision overturned on appeal restores everything;
3. a big cut -> second admin approves -> notice -> appeal decided by a third admin -> points back.
"""

import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds.models import Deed, DeedStatus, VerificationEvent, VerificationEventKind
from app.modules.feed.models import FeedPost, PostState
from app.modules.fraud.models import FlagCaseType, PrizeHold
from app.modules.identity.models import Role, User
from app.modules.scoring import _leaderboard
from app.modules.scoring._limits import load_leaderboard
from app.modules.scoring.models import LedgerRole, PointsLedger
from tests.deeds.helpers import Member, count, make_member, members
from tests.deeds.invite_helpers import withdraw
from tests.fraud.appeal_helpers import (
    appeal,
    decide_appeal,
    drain,
    final_events,
    finalize,
)
from tests.fraud.helpers import cases_for, days_ago, deed, run_fraud
from tests.fraud.review_helpers import NOTE, admin, ask, confirm_body, with_role
from tests.scoring.board_helpers import entries
from tests.scoring.helpers import active, points
from tests.scoring.override_helpers import decide, override, run_scoring_for, scored_deed

IST = ZoneInfo("Asia/Kolkata")
LATER = timedelta(days=31)
HOUR = timedelta(hours=1)


# --------------------------------------------------- helpers


async def _month_of(session: AsyncSession, deed_id: uuid.UUID) -> date:
    verified = await session.scalar(select(Deed.verified_at).where(Deed.id == deed_id))
    assert verified is not None
    return verified.astimezone(IST).date().replace(day=1)


async def _status(session: AsyncSession, deed_id: uuid.UUID) -> DeedStatus:
    status = await session.scalar(
        select(Deed.status).where(Deed.id == deed_id).execution_options(populate_existing=True)
    )
    assert status is not None
    return status


async def _net(session: AsyncSession, deed_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
                PointsLedger.deed_id == deed_id
            )
        )
        or 0
    )


async def _on_boards(session: AsyncSession, month: date) -> set[uuid.UUID]:
    """Everyone on any live board of ``month`` (computed from the ledger)."""
    _, config = await load_leaderboard(session)
    boards = await _leaderboard.compute_boards(session, month, config)
    return {row.user_id for _, rows in boards.values() for row in rows}


async def _refreshed(session: AsyncSession, month: date, region_ids: set[int]) -> set[uuid.UUID]:
    """Run the board refresh (what the worker's ``scoring.refresh_boards`` does) and return
    everyone on the newest snapshots of these regions."""
    version, config = await load_leaderboard(session)
    await _leaderboard.refresh_month(
        session, month, config=config, config_version=version, now=datetime.now(UTC)
    )
    users: set[uuid.UUID] = set()
    for region_id in region_ids:
        snap = await _leaderboard.latest_snapshot(session, region_id, month)
        if snap is not None:
            users |= set(await entries(session, snap.id))
    return users


async def _regions(session: AsyncSession, month: date) -> set[int]:
    _, config = await load_leaderboard(session)
    return set(await _leaderboard.compute_boards(session, month, config))


async def _get(client: AsyncClient, path: str, who: Member | None = None) -> Any:
    res = await client.get(path, headers=who.headers if who else {})
    return res.status_code, res.json()


async def _member_view(
    client: AsyncClient, people: list[Member], deed_ids: list[uuid.UUID], viewer: Member
) -> dict[str, Any]:
    """Everything a member or the public can read about these people and deeds."""
    view: dict[str, Any] = {}
    for p in people:
        for path in (
            "/api/reviews",
            "/api/me/deeds",
            "/api/scoring/me",
            "/api/scoring/me/ledger",
            "/api/users/me",
        ):
            view[f"{p.handle} {path}"] = await _get(client, path, p)
        for path in (
            f"/api/users/{p.handle}",
            f"/api/users/{p.handle}/deed-counts",
            f"/api/scoring/users/{p.handle}",
            f"/api/scoring/users/{p.handle}/ranks",
            f"/api/feed/users/{p.handle}/deeds",
        ):
            view[f"viewer {path}"] = await _get(client, path, viewer)
    for d in deed_ids:
        for path in (f"/api/feed/deeds/{d}", f"/api/scoring/deeds/{d}", f"/api/deeds/{d}"):
            view[f"viewer {path}"] = await _get(client, path, viewer)
        view[f"public {d}"] = await _get(client, f"/api/public/deeds/{d}")
    return view


# --------------------------------------------------- 1. detection to rejection, no appeal


async def test_detection_explain_confirm_no_appeal_then_rejected_everywhere(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, b, viewer = await members(session, 3)
    who = await admin(session)
    ab = await deed(session, a, b, days_ago(0.3))  # doer b
    ba = await deed(session, b, a, days_ago(0.2))  # doer a: closes the reciprocal pair
    await drain(session, [ab, ba])  # feed posts published, points awarded
    month = await _month_of(session, ab)
    regions = await _regions(session, month)
    assert {a.user_id, b.user_id} <= await _on_boards(session, month)
    assert {a.user_id, b.user_id} <= await _refreshed(session, month, regions)
    doer_points = {d: await points(session, d, LedgerRole.DOER) for d in (ab, ba)}
    before = await _member_view(client, [a, b], [ab, ba], viewer)
    for key in (
        f"viewer /api/feed/deeds/{ab}",
        f"public {ab}",
        f"{a.handle} /api/scoring/me/ledger",
        f"viewer /api/scoring/users/{b.handle}/ranks",
    ):
        assert before[key][0] == 200, (key, before[key])
    assert before[f"{a.handle} /api/reviews"][1]["explanations"] == []

    # Detection (principle 1): a case in the admin queue and nothing else, anywhere.
    assert await run_fraud(session, ab, ba) >= 1
    await session.commit()
    (case,) = await cases_for(session, FlagCaseType.RECIPROCAL, a, b)
    assert await count(session, PrizeHold, PrizeHold.case_id == case.id) == 0
    assert await drain(session, [ab, ba], [case.id]) == 0  # nothing for scoring or the feed
    assert await _member_view(client, [a, b], [ab, ba], viewer) == before
    assert {a.user_id, b.user_id} <= await _refreshed(session, month, regions)

    # The admin finds it in the queue and asks both people.
    queue = (await client.get("/api/admin/fraud/cases", headers=who.headers)).json()
    assert str(case.id) in {i["case_id"] for i in queue["items"]}
    base = f"/api/admin/fraud/cases/{case.id}"
    res = await client.post(f"{base}/explanations", json={"note": NOTE}, headers=who.headers)
    assert res.status_code == 200, res.text
    assert {e["user"]["handle"] for e in res.json()["explanations"]} == {a.handle, b.handle}

    # Each sees a neutral request about their own deeds and replies.
    for person in (a, b):
        mine = (await client.get("/api/reviews", headers=person.headers)).json()
        (request,) = mine["explanations"]
        assert request["status"] == "waiting" and mine["notices"] == []
        assert {r["deed_id"] for r in request["deeds"]} == {str(ab), str(ba)}
        assert mine["unread"] >= 1
        sent = await client.post(
            f"/api/reviews/explanations/{request['id']}/reply",
            json={"reply": "We live on the same street."},
            headers=person.headers,
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["status"] == "replied"

    # Confirm: a -> b rejected (held until final), b -> a excluded.
    res = await client.post(
        f"{base}/decision", json=confirm_body({ab: "reject", ba: "exclude"}), headers=who.headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["case"]["held_count"] == 2
    await drain(session, [ab, ba], [case.id])

    # Off the boards at the next refresh; points kept; the post is still in the feed.
    assert {a.user_id, b.user_id}.isdisjoint(await _on_boards(session, month))
    assert {a.user_id, b.user_id}.isdisjoint(await _refreshed(session, month, regions))
    for d in (ab, ba):
        assert await points(session, d, LedgerRole.DOER) == doer_points[d]
        assert await _status(session, d) == DeedStatus.VERIFIED
    assert (await client.get(f"/api/feed/deeds/{ab}", headers=viewer.headers)).status_code == 200
    for person in (a, b):
        (notice,) = (await client.get("/api/reviews", headers=person.headers)).json()["notices"]
        assert notice["can_appeal"] is True and notice["final"] is False
        assert notice["appeal"] is None

    # Nobody appeals. Inside the window nothing is final; after it, the rejection runs.
    assert (await finalize(session)).rejected == {}
    result = await finalize(session, datetime.now(UTC) + LATER)
    assert result.rejected == {case.id: [ab]}
    assert len(await final_events(session, case.id)) == 1
    await drain(session, [ab, ba], [case.id])

    assert await _status(session, ab) == DeedStatus.REJECTED
    assert await _status(session, ba) == DeedStatus.VERIFIED  # excluded only
    post = await session.get(FeedPost, ab, populate_existing=True)
    assert post is not None and post.state == PostState.REJECTED
    assert (await client.get(f"/api/feed/deeds/{ab}", headers=viewer.headers)).status_code == 404
    assert (await client.get(f"/api/public/deeds/{ab}")).status_code == 404
    feed_b = (await client.get(f"/api/feed/users/{b.handle}/deeds", headers=viewer.headers)).json()
    assert str(ab) not in str(feed_b)
    assert await _net(session, ab) == 0  # doer and reporter points reversed
    assert await points(session, ba, LedgerRole.DOER) == doer_points[ba]
    entry = await active(session, ba, LedgerRole.DOER)
    assert entry is not None and entry.counts_for_prize_rank is False
    assert {a.user_id, b.user_id}.isdisjoint(await _refreshed(session, month, regions))
    for person in (a, b):
        (notice,) = (await client.get("/api/reviews", headers=person.headers)).json()["notices"]
        assert notice["final"] is True and notice["can_appeal"] is False

    # Running the hourly job again changes nothing.
    assert (await finalize(session, datetime.now(UTC) + 2 * LATER)).rejected == {}
    assert len(await final_events(session, case.id)) == 1


# --------------------------------------------------- 2. overturned on appeal


async def test_overturned_appeal_restores_board_points_and_feed(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, b, viewer = await members(session, 3)
    decider, reviewer = await admin(session), await admin(session)
    ab = await deed(session, a, b, days_ago(0.3))
    ba = await deed(session, b, a, days_ago(0.2))
    await drain(session, [ab, ba])
    month = await _month_of(session, ab)
    regions = await _regions(session, month)
    on_board_before = await _refreshed(session, month, regions)
    assert {a.user_id, b.user_id} <= on_board_before
    ledger_points = {d: await _net(session, d) for d in (ab, ba)}

    await run_fraud(session, ab, ba)
    await session.commit()
    (case,) = await cases_for(session, FlagCaseType.RECIPROCAL, a, b)
    await ask(session, decider, case.id, now=datetime.now(UTC) - timedelta(days=20))  # past due
    res = await client.post(
        f"/api/admin/fraud/cases/{case.id}/decision",
        json=confirm_body({ab: "reject", ba: "exclude"}),
        headers=decider.headers,
    )
    assert res.status_code == 200, res.text
    await drain(session, [ab, ba], [case.id])
    assert {a.user_id, b.user_id}.isdisjoint(await _refreshed(session, month, regions))

    # a appeals through the Reviews page; the deciding admin can't see it; another overturns.
    (notice,) = (await client.get("/api/reviews", headers=a.headers)).json()["notices"]
    assert (await appeal(client, a, uuid.UUID(notice["id"]))).status_code == 200
    queue = (await client.get("/api/admin/fraud/appeals", headers=decider.headers)).json()
    assert queue["items"] == []
    (row,) = (await client.get("/api/admin/fraud/appeals", headers=reviewer.headers)).json()[
        "items"
    ]
    res = await decide_appeal(client, reviewer, uuid.UUID(row["appeal_id"]), "overturned")
    assert res.status_code == 200, res.text
    await drain(session, [ab, ba], [case.id])

    # Everything is back: holds released, prize rank restored, boards, feed, points.
    assert (
        await count(
            session, PrizeHold, PrizeHold.case_id == case.id, PrizeHold.released_at.is_(None)
        )
        == 0
    )
    for d in (ab, ba):
        entry = await active(session, d, LedgerRole.DOER)
        assert entry is not None and entry.counts_for_prize_rank is True
        assert await _net(session, d) == ledger_points[d]
    assert {a.user_id, b.user_id} <= await _on_boards(session, month)
    assert {a.user_id, b.user_id} <= await _refreshed(session, month, regions)

    # b never appealed: after the window nothing is rejected (a's overturn cancelled it).
    result = await finalize(session, datetime.now(UTC) + LATER)
    assert result.rejected == {}
    assert await final_events(session, case.id) == []
    await drain(session, [ab, ba], [case.id])
    assert await _status(session, ab) == DeedStatus.VERIFIED
    assert (await client.get(f"/api/feed/deeds/{ab}", headers=viewer.headers)).status_code == 200
    assert (await client.get(f"/api/public/deeds/{ab}")).status_code == 200
    assert {a.user_id, b.user_id} <= await _refreshed(session, month, regions)
    mine = (await client.get("/api/reviews", headers=a.headers)).json()
    assert mine["notices"][0]["appeal"]["outcome"] == "overturned"
    assert mine["notices"][0]["final"] is True


# --------------------------------------------------- 3. big cut, second admin, third admin


async def test_big_cut_second_admin_notice_and_appeal_to_a_third_admin(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, deed_id = await scored_deed(session)
    requester, approver, third = await admin(session), await admin(session), await admin(session)
    rule_points = await points(session, deed_id, LedgerRole.DOER)
    assert rule_points == 10

    # 10 -> 0 is a big cut: a pending request, no effect, no notice.
    res = await override(client, requester, deed_id, points=0)
    assert res.status_code == 202, res.text
    request_id = res.json()["id"]
    await run_fraud(session, deed_id)
    await session.commit()
    assert (await client.get("/api/reviews", headers=doer.headers)).json()["notices"] == []
    assert await points(session, deed_id, LedgerRole.DOER) == rule_points

    # A second admin approves: applied at once, then fraud turns it into a notice.
    approved = await decide(client, approver, request_id, "approve", note="The photo is fake.")
    assert approved.status_code == 200, approved.text
    assert await points(session, deed_id, LedgerRole.DOER) == 0
    assert await run_fraud(session, deed_id) >= 1
    await session.commit()
    mine = (await client.get("/api/reviews", headers=doer.headers)).json()
    (notice,) = mine["notices"]
    assert notice["reason_text"] == "The photo didn't show this deed."
    assert notice["items"][0]["effect_text"] == "Points changed from 10 to 0"
    assert notice["items"][0]["deed"]["other_person"]["handle"] == reporter.handle
    assert notice["can_appeal"] is True
    # The reporter's own points were not touched and they got no notice.
    assert (await client.get("/api/reviews", headers=reporter.headers)).json()["notices"] == []

    # The doer appeals; neither the requester nor the approver may decide it.
    assert (await appeal(client, doer, uuid.UUID(notice["id"]))).status_code == 200
    (row,) = (await client.get("/api/admin/fraud/appeals", headers=third.headers)).json()["items"]
    appeal_id = uuid.UUID(row["appeal_id"])
    for who in (requester, approver):
        listed = (await client.get("/api/admin/fraud/appeals", headers=who.headers)).json()
        assert listed["items"] == []
        assert (await decide_appeal(client, who, appeal_id, "overturned")).status_code == 404
    res = await decide_appeal(client, third, appeal_id, "overturned")
    assert res.status_code == 200, res.text

    # Scoring clears the override: back to the rules, no new notice (raising is never told).
    assert await run_scoring_for(session, "fraud.appeal_decided") == 1
    await session.commit()
    assert await points(session, deed_id, LedgerRole.DOER) == rule_points
    await run_fraud(session, deed_id)
    await session.commit()
    mine = (await client.get("/api/reviews", headers=doer.headers)).json()
    (notice,) = mine["notices"]
    assert notice["appeal"]["outcome"] == "overturned" and notice["final"] is True
    assert notice["items"][0]["effect_text"] == "Points restored after another review"
    detail = await client.get(f"/api/admin/scoring/deeds/{deed_id}", headers=third.headers)
    assert detail.status_code == 200
    assert detail.json()["overrides"][-1]["points"] is None


async def test_small_cut_needs_no_second_admin_and_is_appealable(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Boundary of Q77: a cut of exactly ``max_cut_points`` (10) applies at once and tells the
    person; one more point needs a second admin."""
    _, doer, deed_id = await scored_deed(session)
    a1 = await admin(session)
    assert (await override(client, a1, deed_id, points=25)).status_code == 200
    res = await override(client, a1, deed_id, points=15)  # cut of exactly 10
    assert res.status_code == 200, res.text
    assert await points(session, deed_id, LedgerRole.DOER) == 15
    res = await override(client, a1, deed_id, points=4)  # cut of 11
    assert res.status_code == 202, res.text
    assert await points(session, deed_id, LedgerRole.DOER) == 15
    await run_fraud(session, deed_id)
    await session.commit()
    (notice,) = (await client.get("/api/reviews", headers=doer.headers)).json()["notices"]
    assert notice["items"][0]["effect_text"] == "Points changed from 25 to 15"


async def test_conflicted_admin_cannot_approve_a_big_cut_or_review_the_appeal(
    client: AsyncClient, session: AsyncSession
) -> None:
    """D90 for #18 and #12: an admin who recognised the doer 10 days ago is conflicted."""
    _, doer, deed_id = await scored_deed(session)
    requester, approver = await admin(session), await admin(session)
    conflicted = await make_member(session)
    await deed(session, conflicted, doer, days_ago(10))
    await session.commit()
    await with_role(session, conflicted, Role.ADMIN)
    res = await override(client, requester, deed_id, points=0)
    assert res.status_code == 202
    request_id = res.json()["id"]
    listed = await client.get("/api/admin/scoring/override-requests", headers=conflicted.headers)
    assert listed.json()["items"] == []
    assert (await decide(client, conflicted, request_id, "approve")).status_code == 404
    assert await points(session, deed_id, LedgerRole.DOER) == 10
    assert (await decide(client, approver, request_id, "approve")).status_code == 200
    await run_fraud(session, deed_id)
    await session.commit()
    (notice,) = (await client.get("/api/reviews", headers=doer.headers)).json()["notices"]
    assert (await appeal(client, doer, uuid.UUID(notice["id"]))).status_code == 200
    assert (await client.get("/api/admin/fraud/appeals", headers=conflicted.headers)).json()[
        "items"
    ] == []


# --------------------------------------------------- 4. edge cases of the final step


async def test_a_deed_withdrawn_before_the_rejection_is_final_is_skipped(
    client: AsyncClient, session: AsyncSession
) -> None:
    """D89 / #6: the reporter withdraws the deed while its rejection waits for the appeal window;
    when the decision becomes final, ``deeds`` skips it (no second status change, no
    ``rejected`` history row)."""
    a, b = await members(session, 2)
    decider = await admin(session)
    ab = await deed(session, a, b, days_ago(0.3))
    ba = await deed(session, b, a, days_ago(0.2))
    await drain(session, [ab, ba])
    await run_fraud(session, ab, ba)
    await session.commit()
    (case,) = await cases_for(session, FlagCaseType.RECIPROCAL, a, b)
    await ask(session, decider, case.id, now=datetime.now(UTC) - timedelta(days=20))
    res = await client.post(
        f"/api/admin/fraud/cases/{case.id}/decision",
        json=confirm_body({ab: "reject", ba: "release"}),
        headers=decider.headers,
    )
    assert res.status_code == 200, res.text
    await drain(session, [ab, ba], [case.id])
    await withdraw(session, a, ab)
    await session.commit()
    await drain(session, [ab, ba], [case.id])
    assert await _status(session, ab) == DeedStatus.WITHDRAWN

    await finalize(session, datetime.now(UTC) + LATER)
    await drain(session, [ab, ba], [case.id])
    assert await _status(session, ab) == DeedStatus.WITHDRAWN
    assert (
        await count(
            session,
            VerificationEvent,
            VerificationEvent.deed_id == ab,
            VerificationEvent.kind == VerificationEventKind.REJECTED,
        )
        == 0
    )
    assert await _net(session, ab) == 0  # the withdrawal already reversed the points
    assert await _status(session, ba) == DeedStatus.VERIFIED


async def test_in_a_ring_one_persons_overturn_spares_only_their_deeds(
    client: AsyncClient, session: AsyncSession
) -> None:
    """D100 with three people: every deed rejected; a's appeal is overturned, b and c never
    appeal. a's two deeds (a -> b, c -> a) stay verified and come back to the boards; b -> c,
    which a is not on, is rejected once b's and c's notices are final."""
    a, b, c = await members(session, 3)
    decider, reviewer = await admin(session), await admin(session)
    t0 = days_ago(0.4)
    ab = await deed(session, a, b, t0)
    bc = await deed(session, b, c, t0 + 2 * HOUR)
    ca = await deed(session, c, a, t0 + 4 * HOUR)
    made = [ab, bc, ca]
    await drain(session, made)
    month = await _month_of(session, ab)
    await run_fraud(session, *made)
    await session.commit()
    (case,) = await cases_for(session, FlagCaseType.RING, a, b, c)
    await ask(session, decider, case.id, now=datetime.now(UTC) - timedelta(days=20))
    res = await client.post(
        f"/api/admin/fraud/cases/{case.id}/decision",
        json=confirm_body({ab: "reject", bc: "reject", ca: "reject"}),
        headers=decider.headers,
    )
    assert res.status_code == 200, res.text
    await drain(session, made, [case.id])
    assert {a.user_id, b.user_id, c.user_id}.isdisjoint(await _on_boards(session, month))

    (notice,) = (await client.get("/api/reviews", headers=a.headers)).json()["notices"]
    assert len(notice["items"]) == 2  # only a's own deeds
    assert (await appeal(client, a, uuid.UUID(notice["id"]))).status_code == 200
    (row,) = (await client.get("/api/admin/fraud/appeals", headers=reviewer.headers)).json()[
        "items"
    ]
    res = await decide_appeal(client, reviewer, uuid.UUID(row["appeal_id"]), "overturned")
    assert res.status_code == 200, res.text
    await drain(session, made, [case.id])

    result = await finalize(session, datetime.now(UTC) + LATER)
    assert result.rejected == {case.id: [bc]}
    await drain(session, made, [case.id])
    assert await _status(session, bc) == DeedStatus.REJECTED
    assert await _status(session, ab) == DeedStatus.VERIFIED
    assert await _status(session, ca) == DeedStatus.VERIFIED
    # a -> b (doer b) and c -> a (doer a) are back on the boards; b -> c (doer c) is gone.
    on_board = await _on_boards(session, month)
    assert {a.user_id, b.user_id} <= on_board and c.user_id not in on_board
    assert await _net(session, bc) == 0
    # Each person's notice tells what really happened to each of their deeds.
    expected = {
        a: {str(ab): "Restored after another review", str(ca): "Restored after another review"},
        b: {str(ab): "Restored after another review", str(bc): "Removed after review"},
        c: {str(bc): "Removed after review", str(ca): "Restored after another review"},
    }
    for person, effects in expected.items():
        (shown,) = (await client.get("/api/reviews", headers=person.headers)).json()["notices"]
        assert {i["deed"]["deed_id"]: i["effect_text"] for i in shown["items"]} == effects
        assert shown["final"] is True


# --------------------------------------------------- 5. overrides meet holds and rejections


async def _confirmed_pair(
    client: AsyncClient, session: AsyncSession, actions: dict[str, str]
) -> tuple[Member, Member, uuid.UUID, uuid.UUID, uuid.UUID, Member]:
    """a <-> b detected for real, asked (past due) and confirmed over HTTP with ``actions``
    keyed ``"ab"`` / ``"ba"``. Returns ``a, b, ab, ba, case_id, decider``."""
    a, b = await members(session, 2)
    decider = await admin(session)
    ab = await deed(session, a, b, days_ago(0.3))
    ba = await deed(session, b, a, days_ago(0.2))
    await drain(session, [ab, ba])
    await run_fraud(session, ab, ba)
    await session.commit()
    (case,) = await cases_for(session, FlagCaseType.RECIPROCAL, a, b)
    await ask(session, decider, case.id, now=datetime.now(UTC) - timedelta(days=20))
    ids = {"ab": ab, "ba": ba}
    res = await client.post(
        f"/api/admin/fraud/cases/{case.id}/decision",
        json=confirm_body({ids[k]: v for k, v in actions.items()}),
        headers=decider.headers,
    )
    assert res.status_code == 200, res.text
    await drain(session, [ab, ba], [case.id])
    return a, b, ab, ba, case.id, decider


async def test_an_override_on_an_excluded_deed_keeps_the_hold_until_an_overturn(
    client: AsyncClient, session: AsyncSession
) -> None:
    """D91: the override replaces the points; the prize hold still applies. Overturning the
    case releases the hold and keeps the override."""
    _, b, ab, _, case_id, _ = await _confirmed_pair(
        client, session, {"ab": "exclude", "ba": "release"}
    )
    overrider, reviewer = await admin(session), await admin(session)
    res = await override(client, overrider, ab, points=8)
    assert res.status_code == 200, res.text
    assert res.json()["held"] is True
    entry = await active(session, ab, LedgerRole.DOER)
    assert entry is not None
    assert (entry.points, entry.counts_for_prize_rank) == (8, False)

    (case_notice,) = (await client.get("/api/reviews", headers=b.headers)).json()["notices"]
    assert (await appeal(client, b, uuid.UUID(case_notice["id"]))).status_code == 200
    rows = (await client.get("/api/admin/fraud/appeals", headers=reviewer.headers)).json()
    appeal_id = next(r["appeal_id"] for r in rows["items"] if r["kind"] == "case")
    res = await decide_appeal(client, reviewer, uuid.UUID(appeal_id), "overturned")
    assert res.status_code == 200, res.text
    await drain(session, [ab], [case_id])
    await run_scoring_for(session, "fraud.appeal_decided")
    entry = await active(session, ab, LedgerRole.DOER)
    assert entry is not None
    assert (entry.points, entry.counts_for_prize_rank) == (8, True)


async def test_a_rejected_deed_has_nothing_to_override(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, _, ab, _, case_id, _ = await _confirmed_pair(
        client, session, {"ab": "reject", "ba": "release"}
    )
    await finalize(session, datetime.now(UTC) + LATER)
    await drain(session, [ab], [case_id])
    assert await _status(session, ab) == DeedStatus.REJECTED
    overrider = await admin(session)
    for role in ("doer", "reporter"):
        res = await override(client, overrider, ab, role=role, points=3)
        assert (res.status_code, res.json()["code"]) == (409, "nothing_to_override")


# --------------------------------------------------- 6. the audit trail of a whole flow


async def test_the_audit_view_shows_the_whole_flow_without_personal_data(
    client: AsyncClient, session: AsyncSession
) -> None:
    """§9 "Audit view exposure": every M5 action is in the audit view with its actor, and no
    old/new value holds a phone number, a phone or device hash, or coordinates."""
    a, b, ab, ba, case_id, decider = await _confirmed_pair(
        client, session, {"ab": "reject", "ba": "exclude"}
    )
    reviewer, overrider = await admin(session), await admin(session)
    (notice,) = (await client.get("/api/reviews", headers=a.headers)).json()["notices"]
    assert (await appeal(client, a, uuid.UUID(notice["id"]))).status_code == 200
    (row,) = (await client.get("/api/admin/fraud/appeals", headers=reviewer.headers)).json()[
        "items"
    ]
    res = await decide_appeal(client, reviewer, uuid.UUID(row["appeal_id"]), "upheld")
    assert res.status_code == 200, res.text
    assert (await override(client, overrider, ba, points=9)).status_code == 200
    await finalize(session, datetime.now(UTC) + LATER)
    await drain(session, [ab, ba], [case_id])
    assert await _status(session, ab) == DeedStatus.REJECTED

    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params = {"cursor": cursor} if cursor else {}
        page = await client.get("/api/admin/audit", params=params, headers=reviewer.headers)
        assert page.status_code == 200, page.text
        items += page.json()["items"]
        cursor = page.json()["next_cursor"]
        if cursor is None:
            break
    actions = {(i["action"], (i["actor"] or {}).get("handle")) for i in items}
    assert ("flag_case.explanation_requested", decider.handle) in actions
    assert ("flag_case.confirmed", decider.handle) in actions
    assert ("appeal.upheld", reviewer.handle) in actions
    assert ("deed_points.overridden", overrider.handle) in actions
    assert any(i["action"] == "deed.rejected" and i["entity_id"] == str(ab) for i in items)

    phones = [
        p
        for p in await session.scalars(
            select(User.phone_e164).where(User.id.in_([a.user_id, b.user_id]))
        )
        if p
    ]
    text = json.dumps(items)
    for secret in [*phones, a.phone_hash, b.phone_hash]:
        assert secret not in text
    assert not re.search(r"\b(lat|lng|lon|latitude|longitude|location|device_hash|ip_hash)\b", text)
    assert "12.97" not in text and "77.59" not in text  # the test deeds' coordinates
