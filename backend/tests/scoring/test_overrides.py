"""Score overrides (docs/M5-DESIGN.md D90, D91, Q77, §3.4 #13, #17, #18, §4.6; §8 "Overrides and
revocation"). An override replaces the rule points of one role through ``reconcile_deed``;
lowering points tells the person (``PointsOverridden``); a big cut waits for a second admin."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit
from app.core.runtime_config import publish
from app.modules.fraud.service import AppealDecided
from app.modules.identity.models import Role
from app.modules.scoring import _overrides
from app.modules.scoring._ledger import reconcile_deed
from app.modules.scoring._limits import load_scoring
from app.modules.scoring.models import (
    LedgerKind,
    LedgerReason,
    LedgerRole,
    OverrideRequest,
    OverrideRequestStatus,
)
from tests.deeds.helpers import make_member, members, scan_and_report
from tests.deeds.invite_helpers import withdraw
from tests.fraud.review_helpers import admin, with_role
from tests.scoring.helpers import active, ledger, points, run_scoring
from tests.scoring.hold_helpers import hold
from tests.scoring.override_helpers import (
    NOTE,
    audits,
    decide,
    deed_worth,
    overridden_events,
    override,
    overrides,
    run_scoring_for,
    scored_deed,
)

DOER, REPORTER = LedgerRole.DOER, LedgerRole.REPORTER


# --------------------------------------------------- applied at once


async def test_lowering_override_rewrites_the_ledger_audits_and_tells_the_person(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, doer, deed_id = await scored_deed(session)
    a = await admin(session)

    res = await override(client, a, deed_id, points=8)
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["held"] is False
    (row,) = body["overrides"]
    assert (row["role"], row["points"], row["previous_points"]) == ("doer", 8, 10)
    assert (row["reason_code"], row["note"], row["actor"]["handle"]) == (
        "fake_photo",
        NOTE,
        a.handle,
    )

    entry = await active(session, deed_id, DOER)
    assert entry is not None
    assert (entry.kind, entry.points, entry.deed_units, entry.reason) == (
        LedgerKind.AWARD,
        8,
        1,
        LedgerReason.ADMIN_OVERRIDE,
    )
    assert entry.breakdown["override_id"] == row["id"]
    assert entry.breakdown["rule_points"] == 10
    reversal = [r for r in await ledger(session, deed_id) if r.kind == LedgerKind.REVERSAL]
    assert [(r.points, r.reason) for r in reversal] == [(-10, LedgerReason.ADMIN_OVERRIDE)]
    assert await points(session, deed_id, REPORTER) == 2  # the other role is untouched

    (audit,) = await audits(session, "deed_points", str(deed_id))
    assert audit.action == "deed_points.overridden" and audit.actor_id == a.user_id
    assert audit.old_value is not None and audit.new_value is not None
    assert (audit.old_value["points"], audit.new_value["points"]) == (10, 8)
    assert audit.reason == NOTE

    (event,) = await overridden_events(session, deed_id)
    assert event == {
        "override_id": row["id"],
        "deed_id": str(deed_id),
        "role": "doer",
        "user_id": str(doer.user_id),
        "old_points": 10,
        "new_points": 8,
        "reason_code": "fake_photo",
        "actor_id": str(a.user_id),
        "requested_by_id": None,
    }

    # A later re-score keeps the override.
    before = len(await ledger(session, deed_id))
    assert await reconcile_deed(session, deed_id) == []
    assert len(await ledger(session, deed_id)) == before
    assert await points(session, deed_id, DOER) == 8

    # Clearing goes back to the rules; raising tells nobody.
    cleared = await override(client, a, deed_id, points=None, reason_code="rule_misfire")
    assert cleared.status_code == 200, cleared.json()
    assert [o["points"] for o in cleared.json()["overrides"]] == [8, None]
    entry = await active(session, deed_id, DOER)
    assert entry is not None
    assert (entry.points, entry.reason) == (10, LedgerReason.ADMIN_OVERRIDE)
    assert "override_id" not in entry.breakdown
    assert len(await overridden_events(session, deed_id)) == 1
    # Sorted: audit rows written in one test transaction share their created_at.
    actions = sorted(x.action for x in await audits(session, "deed_points", str(deed_id)))
    assert actions == ["deed_points.overridden", "deed_points.override_cleared"]


async def test_raising_points_applies_at_once_and_sends_no_notice(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, _, deed_id = await scored_deed(session)
    a = await admin(session)
    res = await override(client, a, deed_id, role="reporter", points=6, reason_code="rule_misfire")
    assert res.status_code == 200, res.json()
    assert await points(session, deed_id, REPORTER) == 6
    assert await overridden_events(session, deed_id) == []


async def test_small_cut_applies_at_once(client: AsyncClient, session: AsyncSession) -> None:
    """12 -> 5 is a cut of 7, within ``max_cut_points`` (10)."""
    _, _, deed_id = await scored_deed(session)
    a = await admin(session)
    assert (await override(client, a, deed_id, points=12)).status_code == 200
    res = await override(client, a, deed_id, points=5)
    assert res.status_code == 200, res.json()
    assert await points(session, deed_id, DOER) == 5
    (event,) = await overridden_events(session, deed_id)
    assert (event["old_points"], event["new_points"]) == (12, 5)


# --------------------------------------------------- validation and conflicts


async def test_validation_errors(client: AsyncClient, session: AsyncSession) -> None:
    _, _, deed_id = await scored_deed(session)
    a = await admin(session)
    cases: list[tuple[dict[str, Any], int, str]] = [
        ({"points": 51}, 422, "points_out_of_range"),
        ({"points": -1}, 422, "points_out_of_range"),
        ({"points": 5, "reason_code": "bogus"}, 422, "invalid_reason_code"),
        ({"points": 5, "note": "short"}, 422, "note_required"),
        ({"points": 5, "note": "x" * 2001}, 422, "note_too_long"),
        ({"points": 5, "case_id": str(uuid.uuid4())}, 422, "unknown_case"),
        ({"points": 10}, 409, "override_unchanged"),
        ({"points": None}, 409, "override_unchanged"),
    ]
    for body, status, code in cases:
        res = await override(client, a, deed_id, **body)
        assert (res.status_code, res.json().get("code")) == (status, code), body
    assert await overrides(session, deed_id) == []
    assert (await override(client, a, uuid.uuid4(), points=5)).status_code == 404


async def test_admin_only_and_conflicted_admins_get_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, deed_id = await scored_deed(session)
    moderator = await with_role(session, await make_member(session), Role.MODERATOR)
    assert (await override(client, moderator, deed_id, points=5)).status_code == 403
    assert (await override(client, doer, deed_id, points=5)).status_code == 403

    # Own deed: the reporter is an admin.
    await with_role(session, reporter, Role.ADMIN)
    own = await override(client, reporter, deed_id, points=5)
    assert (own.status_code, own.json()["code"]) == (404, "deed_not_found")

    # A related admin: a verified deed with the doer inside the lookback.
    related = await admin(session)
    out = await scan_and_report(session, related, doer, at=datetime.now(UTC) - timedelta(days=89))
    assert out.status == "verified"
    await session.commit()
    res = await override(client, related, deed_id, points=5)
    assert (res.status_code, res.json()["code"]) == (404, "deed_not_found")
    read = await client.get(f"/api/admin/scoring/deeds/{deed_id}", headers=related.headers)
    assert read.status_code == 404
    assert await overrides(session, deed_id) == []


async def test_nothing_to_override_for_withdrawn_or_capped_deeds(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, _, deed_id = await scored_deed(session)
    a = await admin(session)
    await withdraw(session, reporter, deed_id)
    await run_scoring(session, deed_id)
    await session.commit()
    for role in ("doer", "reporter"):
        res = await override(client, a, deed_id, role=role, points=5)
        assert (res.status_code, res.json()["code"]) == (409, "nothing_to_override")

    # Capped: the doer's second deed of the day with a daily cap of 1.
    _, scoring = await load_scoring(session)
    await publish(
        session, scoring.model_copy(update={"doer_daily_cap": 1}), actor_id=None, reason="test"
    )
    r1, r2, doer = await members(session, 3)
    start = (datetime.now(UTC) - timedelta(days=1)).replace(
        hour=1, minute=0, second=0, microsecond=0
    )
    first = await scan_and_report(session, r1, doer, at=start)
    second = await scan_and_report(session, r2, doer, at=start + timedelta(minutes=125))
    await run_scoring(session, first.id, second.id)
    await session.commit()
    capped = await active(session, second.id, DOER)
    assert capped is not None and capped.kind == LedgerKind.DECISION
    res = await override(client, a, second.id, points=5)
    assert (res.status_code, res.json()["code"]) == (409, "nothing_to_override")


# --------------------------------------------------- big cuts need a second admin (Q77)


async def test_cut_to_zero_waits_for_a_second_admin_then_applies(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, doer, deed_id = await scored_deed(session)
    a1, a2 = await admin(session), await admin(session)
    assert (await override(client, a1, deed_id, points=12)).status_code == 200
    before = len(await ledger(session, deed_id))

    res = await override(client, a1, deed_id, points=0)
    assert res.status_code == 202, res.json()
    request = res.json()
    assert (request["status"], request["points"], request["previous_points"]) == (
        "pending",
        0,
        12,
    )
    assert request["requested_by"]["handle"] == a1.handle
    assert request["user"]["handle"] == doer.handle
    assert len(await ledger(session, deed_id)) == before  # nothing changed
    assert await overridden_events(session, deed_id) == []
    again = await override(client, a1, deed_id, points=1)
    assert (again.status_code, again.json()["code"]) == (409, "request_pending")

    # The requester neither sees nor decides their own request.
    mine = await client.get("/api/admin/scoring/override-requests", headers=a1.headers)
    assert mine.json()["items"] == [] and mine.json()["counts"]["pending"] == 0
    own = await decide(client, a1, request["id"], "approve")
    assert own.status_code == 404
    theirs = await client.get("/api/admin/scoring/override-requests", headers=a2.headers)
    assert [r["id"] for r in theirs.json()["items"]] == [request["id"]]

    approved = await decide(client, a2, request["id"], "approve", note="Agreed, the photo is fake.")
    assert approved.status_code == 200, approved.json()
    out = approved.json()
    assert out["request"]["status"] == "approved"
    assert out["request"]["decided_by"]["handle"] == a2.handle
    assert out["deed"]["overrides"][-1]["request_id"] == request["id"]

    entry = await active(session, deed_id, DOER)
    assert entry is not None
    assert (entry.kind, entry.points, entry.deed_units, entry.counts_for_prize_rank) == (
        LedgerKind.DECISION,
        0,
        0,
        False,
    )
    assert entry.reason == LedgerReason.ADMIN_OVERRIDE
    assert await points(session, deed_id, DOER) == 0
    (event,) = await overridden_events(session, deed_id)
    assert (event["old_points"], event["new_points"], event["actor_id"]) == (
        12,
        0,
        str(a2.user_id),
    )
    assert event["requested_by_id"] == str(a1.user_id)
    # Sorted: audit rows written in one test transaction share their created_at.
    actions = sorted(x.action for x in await audits(session, "deed_points", str(deed_id)))
    assert actions == [
        "deed_points.overridden",
        "deed_points.overridden",
        "deed_points.override_requested",
    ]

    twice = await decide(client, a2, request["id"], "decline")
    assert (twice.status_code, twice.json()["code"]) == (409, "request_decided")
    decided = await client.get(
        "/api/admin/scoring/override-requests?status=decided", headers=a2.headers
    )
    assert [r["status"] for r in decided.json()["items"]] == ["approved"]


async def test_big_cut_declined_changes_nothing(client: AsyncClient, session: AsyncSession) -> None:
    """Rule points 25: 25 -> 12 is a cut of 13."""
    _, _, deed_id = await deed_worth(session, 25)
    a1, a2 = await admin(session), await admin(session)
    res = await override(client, a1, deed_id, points=12)
    assert res.status_code == 202, res.json()
    short = await decide(client, a2, res.json()["id"], "decline", note="no")
    assert (short.status_code, short.json()["code"]) == (422, "note_required")
    declined = await decide(client, a2, res.json()["id"], "decline", note="The photo is fine.")
    assert declined.status_code == 200, declined.json()
    assert declined.json()["request"]["status"] == "declined"
    assert declined.json()["request"]["decision_note"] == "The photo is fine."
    assert declined.json()["deed"] is None
    assert await points(session, deed_id, DOER) == 25
    assert await overridden_events(session, deed_id) == []
    # Declined: a new request may be made.
    assert (await override(client, a1, deed_id, points=12)).status_code == 202


async def test_requests_expire(client: AsyncClient, session: AsyncSession) -> None:
    _, _, deed_id = await scored_deed(session)
    a1, a2 = await admin(session), await admin(session)
    res = await override(client, a1, deed_id, points=0)
    assert res.status_code == 202
    request_id = uuid.UUID(res.json()["id"])
    expires_at = datetime.fromisoformat(res.json()["expires_at"])
    assert timedelta(days=13) < expires_at - datetime.now(UTC) <= timedelta(days=14)

    assert await _overrides.expire_requests(session, now=expires_at - timedelta(seconds=1)) == 0
    assert await _overrides.expire_requests(session, now=expires_at + timedelta(seconds=1)) == 1
    await session.commit()
    row = await session.get(OverrideRequest, request_id, populate_existing=True)
    assert row is not None and row.status == OverrideRequestStatus.EXPIRED
    late = await decide(client, a2, str(request_id), "approve")
    assert (late.status_code, late.json()["code"]) == (409, "request_expired")
    assert await points(session, deed_id, DOER) == 10


async def test_approval_needs_the_deed_to_still_have_points(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, _, deed_id = await scored_deed(session)
    a1, a2 = await admin(session), await admin(session)
    res = await override(client, a1, deed_id, points=0)
    assert res.status_code == 202
    await withdraw(session, reporter, deed_id)
    await run_scoring(session, deed_id)
    await session.commit()
    gone = await decide(client, a2, res.json()["id"], "approve")
    assert (gone.status_code, gone.json()["code"]) == (409, "nothing_to_override")


async def test_conflicted_admins_dont_see_or_decide_requests(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, doer, deed_id = await scored_deed(session)
    a1, related = await admin(session), await admin(session)
    await scan_and_report(session, related, doer)
    await session.commit()
    res = await override(client, a1, deed_id, points=0)
    assert res.status_code == 202
    listed = await client.get("/api/admin/scoring/override-requests", headers=related.headers)
    assert listed.json()["items"] == [] and listed.json()["counts"] == {"pending": 0, "decided": 0}
    assert (await decide(client, related, res.json()["id"], "approve")).status_code == 404
    bad = await client.get(
        "/api/admin/scoring/override-requests?cursor=nonsense", headers=a1.headers
    )
    assert bad.json()["code"] == "invalid_cursor"


# --------------------------------------------------- #9 additions


async def test_admin_deed_shows_hold_and_overrides(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, _, deed_id = await scored_deed(session)
    a = await admin(session)
    path = f"/api/admin/scoring/deeds/{deed_id}"
    body = (await client.get(path, headers=a.headers)).json()
    assert (body["held"], body["overrides"]) == (False, [])
    await hold(session, deed_id, a)
    await run_scoring(session, deed_id)
    await session.commit()
    assert (await client.get(path, headers=a.headers)).json()["held"] is True

    # An override on a held deed keeps it off prize rank.
    assert (await override(client, a, deed_id, points=7)).status_code == 200
    entry = await active(session, deed_id, DOER)
    assert entry is not None and (entry.points, entry.counts_for_prize_rank) == (7, False)


# --------------------------------------------------- appeals (D100)


async def _appeal(
    session: AsyncSession,
    override_id: uuid.UUID,
    user_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    outcome: Literal["upheld", "overturned"],
) -> None:
    emit(
        session,
        AppealDecided(
            appeal_id=uuid.uuid4(),
            notice_kind="override",
            case_id=None,
            override_id=override_id,
            user_id=user_id,
            outcome=outcome,
            reviewer_id=reviewer_id,
        ),
    )
    await session.flush()


async def test_overturned_appeal_clears_the_override(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, doer, deed_id = await scored_deed(session)
    a1, reviewer = await admin(session), await admin(session)
    res = await override(client, a1, deed_id, points=4)
    assert res.status_code == 200
    override_id = uuid.UUID(res.json()["overrides"][0]["id"])

    await _appeal(session, override_id, doer.user_id, reviewer.user_id, "upheld")
    assert await run_scoring_for(session, "fraud.appeal_decided") == 1
    assert await points(session, deed_id, DOER) == 4  # upheld: nothing changes

    await _appeal(session, override_id, doer.user_id, reviewer.user_id, "overturned")
    assert await run_scoring_for(session, "fraud.appeal_decided") == 1
    rows = await overrides(session, deed_id)
    assert [(o.points, o.reason_code, o.note, o.actor_id) for o in rows[1:]] == [
        (None, "other", "Appeal overturned", reviewer.user_id)
    ]
    entry = await active(session, deed_id, DOER)
    assert entry is not None and (entry.points, entry.reason) == (10, LedgerReason.ADMIN_OVERRIDE)
    assert len(await overridden_events(session, deed_id)) == 1  # raising sends no notice

    # A repeated event changes nothing: the override is no longer in force.
    await _appeal(session, override_id, doer.user_id, reviewer.user_id, "overturned")
    await run_scoring_for(session, "fraud.appeal_decided")
    assert len(await overrides(session, deed_id)) == 2


async def test_overturned_appeal_leaves_a_later_override_alone(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, doer, deed_id = await scored_deed(session)
    a1, reviewer = await admin(session), await admin(session)
    first = await override(client, a1, deed_id, points=4)
    old_id = uuid.UUID(first.json()["overrides"][0]["id"])
    assert (await override(client, a1, deed_id, points=6)).status_code == 200
    await _appeal(session, old_id, doer.user_id, reviewer.user_id, "overturned")
    await run_scoring_for(session, "fraud.appeal_decided")
    assert len(await overrides(session, deed_id)) == 2
    assert await points(session, deed_id, DOER) == 6


async def test_no_requests_listed_by_default(client: AsyncClient, session: AsyncSession) -> None:
    a = await admin(session)
    res = await client.get("/api/admin/scoring/override-requests", headers=a.headers)
    assert res.status_code == 200
    assert res.json() == {"items": [], "counts": {"pending": 0, "decided": 0}, "next_cursor": None}
    assert (
        await session.scalar(
            select(OverrideRequest).where(OverrideRequest.requested_by == a.user_id)
        )
    ) is None
