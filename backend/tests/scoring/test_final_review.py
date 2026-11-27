"""Fixes from the final M5 review (docs/M5-DESIGN.md D90, D100, Q82):

- N2: an overturn goes back to the latest earlier override that was not itself overturned, or
  to the rules (rule 20; cut to 12, then to 10; overturning both gives back 20, not 12).
- N1: the member's notice says "Points restored after another review" only when the overturn
  gave points back (scoring's ``OverrideAppealApplied``), else a neutral text.
- N3: the override's ``case_id`` is in every audit row about it (decline, appeal no-op, the
  notice, its appeal), so the audit view hides them from admins conflicted with the case.
- N5: citing a case needs it to be visible in the case queue (``fraud.service.case_visible_to``),
  including the deed-party rule for deeds older than the lookback.
"""

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.fraud import service as fraud
from app.modules.fraud.models import DecisionNotice, FlagCaseType
from app.modules.identity.models import Role
from app.modules.scoring.models import LedgerRole
from tests.deeds.helpers import Member, members, scan_and_report
from tests.fraud.appeal_helpers import appeal, appeal_of, decide_appeal
from tests.fraud.helpers import days_ago, deed, run_fraud
from tests.fraud.review_helpers import admin, open_case, with_role
from tests.scoring.helpers import points
from tests.scoring.override_helpers import (
    audits,
    decide,
    deed_worth,
    events,
    override,
    run_scoring_for,
    scored_deed,
)

DOER = LedgerRole.DOER
RESTORED = "Points restored after another review"
ALREADY_CHANGED = "We looked again. Your points were already changed by a later decision."


async def _notice_for(session: AsyncSession, override_id: str) -> DecisionNotice:
    row = await session.scalar(
        select(DecisionNotice).where(DecisionNotice.override_id == uuid.UUID(override_id))
    )
    assert row is not None
    return row


async def _overturn(
    client: AsyncClient,
    session: AsyncSession,
    person: Member,
    reviewer: Member,
    notice: DecisionNotice,
    deed_id: uuid.UUID,
) -> uuid.UUID:
    """The person appeals, ``reviewer`` overturns, scoring and then fraud handle it."""
    assert (await appeal(client, person, notice.id)).status_code == 200
    appeal_id = (await appeal_of(session, notice.id)).id
    res = await decide_appeal(client, reviewer, appeal_id, "overturned")
    assert res.status_code == 200, res.text
    assert await run_scoring_for(session, "fraud.appeal_decided") == 1
    await run_fraud(session, deed_id)
    await session.commit()
    return appeal_id


def _effect(reviews: dict[str, Any], notice_id: uuid.UUID) -> str:
    (notice,) = [n for n in reviews["notices"] if n["id"] == str(notice_id)]
    text: str = notice["items"][0]["effect_text"]
    return text


async def test_overturns_skip_overrides_that_were_overturned(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, doer, deed_id = await deed_worth(session, 20)
    a1, reviewer = await admin(session), await admin(session)
    first = await override(client, a1, deed_id, points=12)
    assert first.status_code == 200, first.json()
    second = await override(client, a1, deed_id, points=10)
    assert second.status_code == 200, second.json()
    await run_fraud(session, deed_id)
    await session.commit()
    n1 = await _notice_for(session, first.json()["overrides"][-1]["id"])
    n2 = await _notice_for(session, second.json()["overrides"][-1]["id"])

    # N1 overturned while O2 is in force: nothing changes.
    a1_id = await _overturn(client, session, doer, reviewer, n1, deed_id)
    assert await points(session, deed_id, DOER) == 10
    # N2 overturned: O1 was overturned too, so back to the rules (20), not O1's 12.
    a2_id = await _overturn(client, session, doer, reviewer, n2, deed_id)
    assert await points(session, deed_id, DOER) == 20

    applied = {
        p["appeal_id"]: p["restored"]
        for p in await events(session, "scoring.override_appeal_applied")
    }
    assert applied == {str(a1_id): False, str(a2_id): True}
    reviews = (await client.get("/api/reviews", headers=doer.headers)).json()
    assert _effect(reviews, n1.id) == ALREADY_CHANGED
    assert _effect(reviews, n2.id) == RESTORED
    rows = {
        x.action
        for x in await audits(session, "appeal", str(a1_id))
        + await audits(session, "appeal", str(a2_id))
    }
    assert "appeal.override_applied" in rows


async def test_overturn_after_a_restoring_overturn_still_goes_back_to_the_rules(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Rule 20; O1 12, O2 10. N2 overturned first gives back 12 (O1); then N1 overturned gives
    back 20: the row written for the first overturn is not a decision of its own."""
    _, doer, deed_id = await deed_worth(session, 20)
    a1, reviewer = await admin(session), await admin(session)
    first = await override(client, a1, deed_id, points=12)
    second = await override(client, a1, deed_id, points=10)
    await run_fraud(session, deed_id)
    await session.commit()
    n1 = await _notice_for(session, first.json()["overrides"][-1]["id"])
    n2 = await _notice_for(session, second.json()["overrides"][-1]["id"])
    await _overturn(client, session, doer, reviewer, n2, deed_id)
    assert await points(session, deed_id, DOER) == 12
    await _overturn(client, session, doer, reviewer, n1, deed_id)
    assert await points(session, deed_id, DOER) == 20
    reviews = (await client.get("/api/reviews", headers=doer.headers)).json()
    assert _effect(reviews, n1.id) == RESTORED
    assert _effect(reviews, n2.id) == RESTORED


async def test_rows_citing_a_case_are_hidden_from_conflicted_admins(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, doer, deed_id = await scored_deed(session)
    a1, a2, reviewer = await admin(session), await admin(session), await admin(session)
    case_id = await open_case(session, FlagCaseType.CONTENT, [doer], [deed_id])
    cite = {"case_id": str(case_id)}

    cut = await override(client, a1, deed_id, points=5, **cite)
    assert cut.status_code == 200, cut.json()
    assert (await override(client, a2, deed_id, points=7, **cite)).status_code == 200
    big = await override(client, a1, deed_id, points=30, **cite)
    assert big.status_code == 202, big.json()
    declined = await decide(client, a2, big.json()["id"], "decline", note="Not justified here.")
    assert declined.status_code == 200
    await run_fraud(session, deed_id)
    await session.commit()
    notice = await _notice_for(session, cut.json()["overrides"][-1]["id"])
    appeal_id = await _overturn(client, session, doer, reviewer, notice, deed_id)
    assert await points(session, deed_id, DOER) == 7  # a later decision is in force: no-op

    entities = (str(deed_id), str(notice.id), str(appeal_id))
    wanted = {
        "deed_points.override_declined",
        "deed_points.appeal_overturn_noop",
        "decision_notice.created",
        "appeal.overturned",
        "appeal.override_applied",
    }

    async def seen(who: Member) -> set[str]:
        out: set[str] = set()
        for entity in entities:
            res = await client.get(
                "/api/admin/audit", params={"entity_id": entity}, headers=who.headers
            )
            assert res.status_code == 200
            out |= {i["action"] for i in res.json()["items"]}
        return out

    visible = await seen(reviewer)
    assert wanted <= visible
    # Every one of them names the case.
    for entity_type, entity in (
        ("deed_points", str(deed_id)),
        ("decision_notice", str(notice.id)),
        ("appeal", str(appeal_id)),
    ):
        for row in await audits(session, entity_type, entity):
            if row.action in wanted:
                assert row.new_value is not None and row.new_value["case_id"] == str(case_id)

    # An admin with a recent deed with the doer (a subject of the case) sees none of them.
    conflicted = await admin(session)
    await scan_and_report(session, conflicted, doer)
    await session.commit()
    assert not (await seen(conflicted)) & wanted


async def test_a_case_with_an_old_deed_of_the_admin_cant_be_cited(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The admin reported a case deed 200 days ago (outside the lookback): the case queue hides
    the case from them, so the override can't cite it (422) and it is not visible."""
    _, doer, deed_id = await scored_deed(session)
    (person,) = await members(session, 1)
    who = await with_role(session, person, Role.ADMIN)
    old = await deed(session, who, doer, days_ago(200))
    case_id = await open_case(session, FlagCaseType.CONTENT, [doer], [deed_id, old])
    await session.commit()

    assert not await fraud.case_visible_to(session, who.user_id, case_id)
    res = await override(client, who, deed_id, points=5, case_id=str(case_id))
    assert (res.status_code, res.json()["code"]) == (422, "unknown_case")

    other = await admin(session)
    assert await fraud.case_visible_to(session, other.user_id, case_id)
    assert not await fraud.case_visible_to(session, other.user_id, uuid.uuid4())
    ok = await override(client, other, deed_id, points=5, case_id=str(case_id))
    assert ok.status_code == 200, ok.json()
