"""QA10: the database refuses edits the design forbids, on every table M5 adds (docs/M5-DESIGN.md
§2.1, §2.2; CLAUDE.md "Append-only data"). Rows are made by the real flow (detect, ask, confirm,
appeal, override, big-cut request); then raw SQL tries what no service may do."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.fraud.models import FlagCaseType
from app.modules.scoring.models import ConcentrationSignal
from tests.deeds.helpers import Member, members
from tests.fraud.appeal_helpers import (
    appeal,
    appeal_of,
    confirm,
    decide_appeal,
    notice_of,
)
from tests.fraud.helpers import cases_for, days_ago, deed, run_fraud
from tests.fraud.review_helpers import admin
from tests.scoring.helpers import run_scoring
from tests.scoring.override_helpers import override


@dataclass(frozen=True)
class Rows:
    a: Member
    b: Member
    ab: uuid.UUID
    ba: uuid.UUID
    case_id: uuid.UUID
    notice_id: uuid.UUID
    appeal_id: uuid.UUID
    request_id: str
    decider: Member


async def _rows(client: AsyncClient, session: AsyncSession) -> Rows:
    a, b = await members(session, 2)
    decider, reviewer, requester = await admin(session), await admin(session), await admin(session)
    ab = await deed(session, a, b, days_ago(4))
    ba = await deed(session, b, a, days_ago(3))
    await run_scoring(session, ab, ba)
    await run_fraud(session, ab, ba)
    await session.commit()
    (case,) = await cases_for(session, FlagCaseType.RECIPROCAL, a, b)
    # Asked 20 days ago (past due, nobody replied), then confirmed: a -> b excluded.
    await confirm(session, decider, case.id, {ab: "exclude", ba: "release"})
    notice = await notice_of(session, case.id, a)
    assert (await appeal(client, a, notice.id)).status_code == 200
    appeal_id = (await appeal_of(session, notice.id)).id
    assert (await decide_appeal(client, reviewer, appeal_id, "upheld")).status_code == 200
    assert (await override(client, requester, ba, points=8)).status_code == 200
    res = await override(client, requester, ba, points=0)
    assert res.status_code == 202, res.text
    session.add(
        ConcentrationSignal(
            doer_id=b.user_id,
            reporter_id=a.user_id,
            period_month=date(2026, 9, 1),
            share_bp=4000,
            doer_points=60,
            detected_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return Rows(a, b, ab, ba, case.id, notice.id, appeal_id, res.json()["id"], decider)


async def _refused(
    session: AsyncSession, sql: str, params: Mapping[str, object], match: str
) -> None:
    with pytest.raises(DBAPIError, match=match):
        async with session.begin_nested():
            await session.execute(text(sql), params)


async def test_append_only_tables_refuse_update_and_delete(
    client: AsyncClient, session: AsyncSession
) -> None:
    r = await _rows(client, session)
    c = {"c": r.case_id}
    statements: list[tuple[str, Mapping[str, object]]] = [
        ("UPDATE flag_case_users SET user_id = user_id WHERE case_id = :c", c),
        ("DELETE FROM flag_case_users WHERE case_id = :c", c),
        ("UPDATE flag_case_deeds SET added_at = clock_timestamp() WHERE case_id = :c", c),
        ("DELETE FROM flag_case_deeds WHERE case_id = :c", c),
        ("UPDATE flag_case_decisions SET note = 'another note' WHERE case_id = :c", c),
        ("DELETE FROM flag_case_decisions WHERE case_id = :c", c),
        ("UPDATE flag_case_deed_outcomes SET outcome = 'release' WHERE case_id = :c", c),
        ("DELETE FROM flag_case_deed_outcomes WHERE case_id = :c", c),
        ("UPDATE score_overrides SET points = 50 WHERE deed_id = :d", {"d": r.ba}),
        ("DELETE FROM score_overrides WHERE deed_id = :d", {"d": r.ba}),
        ("UPDATE concentration_signals SET share_bp = 1 WHERE doer_id = :u", {"u": r.b.user_id}),
        ("DELETE FROM concentration_signals WHERE doer_id = :u", {"u": r.b.user_id}),
    ]
    for sql, params in statements:
        await _refused(session, sql, params, "append-only")


async def test_a_resolved_case_never_changes_and_no_case_is_deleted(
    client: AsyncClient, session: AsyncSession
) -> None:
    r = await _rows(client, session)
    c = {"c": r.case_id}
    for sql in (
        "UPDATE flag_cases SET status = 'open', outcome = NULL, resolved_at = NULL, "
        "resolved_by = NULL WHERE id = :c",
        "UPDATE flag_cases SET held_count = 0 WHERE id = :c",
        "UPDATE flag_cases SET review_by = clock_timestamp() WHERE id = :c",
    ):
        await _refused(session, sql, c, "resolved flag case never changes")
    await _refused(session, "DELETE FROM flag_cases WHERE id = :c", c, "never deleted")


async def test_an_open_case_keeps_its_type_and_subjects(session: AsyncSession) -> None:
    a, b = await members(session, 2)
    ab = await deed(session, a, b, days_ago(4))
    ba = await deed(session, b, a, days_ago(3))
    await run_fraud(session, ab, ba)
    (case,) = await cases_for(session, FlagCaseType.RECIPROCAL, a, b)
    c = {"c": case.id}
    for sql in (
        "UPDATE flag_cases SET type = 'ring' WHERE id = :c",
        "UPDATE flag_cases SET subject_key = repeat('0', 64) WHERE id = :c",
        "UPDATE flag_cases SET opened_at = clock_timestamp() - interval '1 day' WHERE id = :c",
        "UPDATE flag_cases SET detector_config_version = 999 WHERE id = :c",
    ):
        await _refused(session, sql, c, "never change")
    # What the review does is allowed: park it.
    async with session.begin_nested():
        await session.execute(text("UPDATE flag_cases SET status = 'parked' WHERE id = :c"), c)


async def test_prize_holds_release_once_and_are_never_deleted(
    client: AsyncClient, session: AsyncSession
) -> None:
    r = await _rows(client, session)
    d = {"d": r.ab, "c": r.case_id}
    where = "WHERE deed_id = :d AND case_id = :c"
    await _refused(session, f"DELETE FROM prize_holds {where}", d, "never deleted")
    await _refused(
        session, f"UPDATE prize_holds SET held_at = clock_timestamp() {where}", d, "only set"
    )
    await _refused(
        session,
        f"UPDATE prize_holds SET deed_id = '{r.ba}', released_at = clock_timestamp(), "
        f"release_reason = 'appeal_overturned' {where}",
        d,
        "only set",
    )
    # Released once (what an overturn does) ...
    async with session.begin_nested():
        await session.execute(
            text(
                "UPDATE prize_holds SET released_at = clock_timestamp(), "
                f"release_reason = 'appeal_overturned' {where}"
            ),
            d,
        )
    # ... and never again, nor "unreleased".
    await _refused(
        session,
        f"UPDATE prize_holds SET release_reason = 'released_on_confirm' {where}",
        d,
        "only set",
    )
    await _refused(
        session,
        f"UPDATE prize_holds SET released_at = NULL, release_reason = NULL {where}",
        d,
        "only set",
    )


async def test_explanation_replies_are_set_once(client: AsyncClient, session: AsyncSession) -> None:
    r = await _rows(client, session)
    p = {"c": r.case_id, "u": r.a.user_id}
    where = "WHERE case_id = :c AND user_id = :u"
    await _refused(session, f"DELETE FROM explanation_requests {where}", p, "never deleted")
    await _refused(
        session,
        "UPDATE explanation_requests SET reply_due_at = clock_timestamp() + interval '30 days' "
        + where,
        p,
        "only set",
    )
    await _refused(
        session,
        f"UPDATE explanation_requests SET requested_by = user_id, reply = 'Hello there', "
        f"replied_at = clock_timestamp() {where}",
        p,
        "only set",
    )


async def test_the_database_refuses_a_reply_after_the_due_date(
    client: AsyncClient, session: AsyncSession
) -> None:
    """§2.1: the reply is accepted only before ``reply_due_at`` (CHECK replied_in_time)."""
    r = await _rows(client, session)  # a was asked 20 days ago: the 14-day due date has passed
    await _refused(
        session,
        "UPDATE explanation_requests SET reply = 'Too late now', replied_at = clock_timestamp() "
        "WHERE case_id = :c AND user_id = :u",
        {"c": r.case_id, "u": r.a.user_id},
        "replied_in_time",
    )


async def test_notices_are_final_once_and_never_change(
    client: AsyncClient, session: AsyncSession
) -> None:
    r = await _rows(client, session)
    n = {"n": r.notice_id}
    await _refused(session, "DELETE FROM decision_notices WHERE id = :n", n, "never deleted")
    # The upheld appeal made a's notice final: never again, never un-final, items fixed.
    for sql in (
        "UPDATE decision_notices SET final_at = clock_timestamp() WHERE id = :n",
        "UPDATE decision_notices SET final_at = NULL WHERE id = :n",
        "UPDATE decision_notices SET items = '[]'::jsonb WHERE id = :n",
        "UPDATE decision_notices SET appeal_until = clock_timestamp() + interval '90 days' "
        "WHERE id = :n",
    ):
        await _refused(session, sql, n, "only set")
    # b's notice (not final): only final_at may be set, nothing else.
    other = await notice_of(session, r.case_id, r.b)
    await _refused(
        session,
        "UPDATE decision_notices SET final_at = clock_timestamp(), reason_key = 'other' "
        "WHERE id = :n",
        {"n": other.id},
        "only set",
    )


async def test_appeals_are_decided_once(client: AsyncClient, session: AsyncSession) -> None:
    r = await _rows(client, session)
    p = {"a": r.appeal_id}
    await _refused(session, "DELETE FROM appeals WHERE id = :a", p, "never deleted")
    for sql in (
        "UPDATE appeals SET outcome = 'overturned' WHERE id = :a",
        "UPDATE appeals SET reason = 'A different reason' WHERE id = :a",
        "UPDATE appeals SET outcome = NULL, reviewer_id = NULL, reviewer_note = NULL, "
        "decided_at = NULL WHERE id = :a",
    ):
        await _refused(session, sql, p, "only set")


async def test_override_requests_are_decided_once_by_someone_else(
    client: AsyncClient, session: AsyncSession
) -> None:
    r = await _rows(client, session)
    q = {"q": r.request_id}
    await _refused(session, "DELETE FROM override_requests WHERE id = :q", q, "never deleted")
    await _refused(
        session, "UPDATE override_requests SET points = 1 WHERE id = :q", q, "decided once"
    )
    # The requester can't decide their own request (CHECK second_admin).
    with pytest.raises(DBAPIError, match="second_admin"):
        async with session.begin_nested():
            await session.execute(
                text(
                    "UPDATE override_requests SET status = 'declined', decided_by = requested_by, "
                    "decision_note = 'My own request', decided_at = clock_timestamp() WHERE id = :q"
                ),
                q,
            )
    # Decided once by another admin, then never again.
    async with session.begin_nested():
        await session.execute(
            text(
                "UPDATE override_requests SET status = 'declined', decided_by = :u, "
                "decision_note = 'Not agreed here', decided_at = clock_timestamp() WHERE id = :q"
            ),
            {**q, "u": r.decider.user_id},
        )
    await _refused(
        session,
        "UPDATE override_requests SET status = 'approved' WHERE id = :q",
        q,
        "decided once",
    )
