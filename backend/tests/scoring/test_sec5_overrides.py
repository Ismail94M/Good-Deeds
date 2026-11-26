"""SEC5 fixes on the scoring side (docs/M5-SEC5.md; PO Q79, Q80):

- M2: a cut is measured from the rule points, so small steps add up (25 -> 15 -> 5 -> 1).
- M3: a raise to more than ``max_cut_points`` above the rule points waits for a second admin;
  approved raises are audited and send no notice. Migration 0028 lets a request raise points.
- L2: an overturned appeal that clears nothing writes only ``deed_points.appeal_overturn_noop``.
- L3: an override's ``case_id`` must have the deed's person as a subject and be visible to the
  admin, else 422 ``unknown_case``.
"""

import importlib.util
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.events import emit
from app.modules.fraud.models import FlagCaseType
from app.modules.fraud.service import AppealDecided
from app.modules.scoring.models import LedgerRole, OverrideRequest, OverrideRequestStatus
from tests.conftest import BACKEND_DIR
from tests.deeds.helpers import members, scan_and_report
from tests.fraud.review_helpers import admin, open_case
from tests.scoring.helpers import points
from tests.scoring.override_helpers import (
    audits,
    decide,
    deed_worth,
    overridden_events,
    override,
    overrides,
    run_scoring_for,
    scored_deed,
)

DOER = LedgerRole.DOER
M28 = BACKEND_DIR / "alembic" / "versions" / "2026_11_26_0200-0028_override_raise_requests.py"


# --------------------------------------------------- M2: cuts add up


async def test_small_steps_still_need_a_second_admin(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Rule points 25: 25 -> 15 is 10 below the rules (applies); 15 -> 5 is 20 below (202)."""
    _, _, deed_id = await deed_worth(session, 25)
    a1, a2 = await admin(session), await admin(session)

    first = await override(client, a1, deed_id, points=15)
    assert first.status_code == 200, first.json()
    assert await points(session, deed_id, DOER) == 15

    second = await override(client, a1, deed_id, points=5)
    assert second.status_code == 202, second.json()
    body = second.json()
    assert (body["points"], body["previous_points"]) == (5, 15)
    assert await points(session, deed_id, DOER) == 15

    declined = await decide(client, a2, body["id"], "decline", note="Not enough evidence.")
    assert declined.status_code == 200
    # A different, smaller step from 15 is still more than 10 below the rules.
    third = await override(client, a1, deed_id, points=1)
    assert third.status_code == 202, third.json()
    assert await points(session, deed_id, DOER) == 15
    assert len(await overridden_events(session, deed_id)) == 1  # only 25 -> 15


async def test_cut_measured_from_rules_not_from_a_raise(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Rule points 10: a raise to 20 then a cut to 8 is 2 below the rules: applies at once."""
    _, _, deed_id = await scored_deed(session)
    a = await admin(session)
    assert (await override(client, a, deed_id, points=20)).status_code == 200
    res = await override(client, a, deed_id, points=8)
    assert res.status_code == 200, res.json()
    (event,) = await overridden_events(session, deed_id)
    assert (event["old_points"], event["new_points"]) == (20, 8)


async def test_cut_to_zero_always_needs_a_second_admin(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Rule points 5: 0 is only 5 below the rules but still waits (``cut_to_zero``)."""
    _, _, deed_id = await deed_worth(session, 5)
    a = await admin(session)
    res = await override(client, a, deed_id, points=0)
    assert res.status_code == 202, res.json()


# --------------------------------------------------- M3: big raises need a second admin


async def test_raise_boundary(client: AsyncClient, session: AsyncSession) -> None:
    """Rule points 10: 21 (rules + 11) -> 202, 20 (rules + 10) -> 200."""
    _, _, deed_id = await scored_deed(session)
    a1, a2 = await admin(session), await admin(session)

    big = await override(client, a1, deed_id, points=21)
    assert big.status_code == 202, big.json()
    assert (big.json()["points"], big.json()["previous_points"]) == (21, 10)
    assert await points(session, deed_id, DOER) == 10
    pending = await override(client, a1, deed_id, points=20)
    assert (pending.status_code, pending.json()["code"]) == (409, "request_pending")
    declined = await decide(client, a2, big.json()["id"], "decline", note="Not justified here.")
    assert declined.status_code == 200

    small = await override(client, a1, deed_id, points=20)
    assert small.status_code == 200, small.json()
    assert await points(session, deed_id, DOER) == 20
    # Stepping up from 20 is still measured from the rules.
    step = await override(client, a1, deed_id, points=22)
    assert step.status_code == 202, step.json()
    assert await overridden_events(session, deed_id) == []


async def test_approved_raise_is_applied_audited_and_sends_no_notice(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, _, deed_id = await scored_deed(session)
    a1, a2 = await admin(session), await admin(session)
    res = await override(client, a1, deed_id, points=50, reason_code="rule_misfire")
    assert res.status_code == 202, res.json()
    approved = await decide(client, a2, res.json()["id"], "approve", note="Agreed, rules missed.")
    assert approved.status_code == 200, approved.json()
    assert approved.json()["request"]["status"] == "approved"
    assert await points(session, deed_id, DOER) == 50
    assert await overridden_events(session, deed_id) == []
    actions = sorted(x.action for x in await audits(session, "deed_points", str(deed_id)))
    assert actions == ["deed_points.overridden", "deed_points.override_requested"]


# --------------------------------------------------- migration 0028


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0028", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sync_conn(migrated_db: None) -> Iterator[Connection]:
    """A sync connection in a transaction that is always rolled back (DDL included)."""
    engine = create_engine(get_settings().database_url)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                yield conn
            finally:
                trans.rollback()
    finally:
        engine.dispose()


def _checks(conn: Connection) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'override_requests'::regclass "
            "AND contype = 'c' AND conname LIKE 'ck_override_requests_points_%'"
        )
    )
    return {r[0] for r in rows}


def _seed_request(conn: Connection, points: int, previous: int) -> None:
    """One pending request written straight to the table. The caller has switched foreign keys
    off (``session_replication_role = replica``); CHECKs still apply."""
    conn.execute(
        text(
            "INSERT INTO override_requests (id, deed_id, role, points, previous_points, "
            "reason_code, note, requested_by, created_at, expires_at, status) VALUES "
            "(:id, :deed, 'doer', :p, :prev, 'other', 'Migration test request.', :admin, "
            "now(), now() + interval '1 day', 'pending')"
        ),
        {
            "id": uuid.uuid4(),
            "deed": uuid.uuid4(),
            "p": points,
            "prev": previous,
            "admin": uuid.uuid4(),
        },
    )


def test_migration_0028_up_and_down(sync_conn: Connection) -> None:
    m = _load(M28)
    assert _checks(sync_conn) == {"ck_override_requests_points_changed"}
    sync_conn.execute(text("SET LOCAL session_replication_role = replica"))
    with Operations.context(MigrationContext.configure(sync_conn)):
        m.downgrade()
        assert _checks(sync_conn) == {"ck_override_requests_points_is_cut"}
        with pytest.raises(IntegrityError), sync_conn.begin_nested():
            _seed_request(sync_conn, 30, 10)  # 0027 allows cuts only
        m.upgrade()
        assert _checks(sync_conn) == {"ck_override_requests_points_changed"}
        _seed_request(sync_conn, 30, 10)
        with pytest.raises(RuntimeError, match="raise points"):
            m.downgrade()
        assert _checks(sync_conn) == {"ck_override_requests_points_changed"}


def _request(
    deed_id: uuid.UUID, admin_id: uuid.UUID, points: int, previous: int
) -> OverrideRequest:
    now = datetime.now(UTC)
    return OverrideRequest(
        id=uuid.uuid4(),
        deed_id=deed_id,
        role=DOER,
        points=points,
        previous_points=previous,
        reason_code="other",
        note="Database constraint test.",
        requested_by=admin_id,
        created_at=now,
        expires_at=now + timedelta(days=1),
        status=OverrideRequestStatus.PENDING,
    )


async def test_request_must_change_the_points(session: AsyncSession) -> None:
    """CHECK ``points_changed``: a raise is accepted; the same points or below 0 are not."""
    _, _, deed_id = await scored_deed(session)
    a = await admin(session)
    async with session.begin_nested():
        session.add(_request(deed_id, a.user_id, 30, 10))
    for points_, previous in ((10, 10), (-1, 10)):
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(_request(deed_id, a.user_id, points_, previous))
                await session.flush()


# --------------------------------------------------- L2: overturn that clears nothing


async def _overturn(
    session: AsyncSession, override_id: uuid.UUID, user_id: uuid.UUID, reviewer_id: uuid.UUID
) -> uuid.UUID:
    appeal_id = uuid.uuid4()
    emit(
        session,
        AppealDecided(
            appeal_id=appeal_id,
            notice_kind="override",
            case_id=None,
            override_id=override_id,
            user_id=user_id,
            outcome="overturned",
            reviewer_id=reviewer_id,
        ),
    )
    await session.flush()
    return appeal_id


async def test_overturn_after_a_later_raise_records_a_noop(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Cut 12 -> 8 (notice), later raised to 10; overturning the cut clears nothing."""
    _, doer, deed_id = await scored_deed(session)
    a1, a2, reviewer = await admin(session), await admin(session), await admin(session)
    assert (await override(client, a1, deed_id, points=12)).status_code == 200
    cut = await override(client, a1, deed_id, points=8)
    cut_id = uuid.UUID(cut.json()["overrides"][-1]["id"])
    assert (await override(client, a2, deed_id, points=10)).status_code == 200
    before = len(await overrides(session, deed_id))
    events_before = len(await overridden_events(session, deed_id))

    appeal_id = await _overturn(session, cut_id, doer.user_id, reviewer.user_id)
    assert await run_scoring_for(session, "fraud.appeal_decided") == 1
    assert len(await overrides(session, deed_id)) == before
    assert len(await overridden_events(session, deed_id)) == events_before
    assert await points(session, deed_id, DOER) == 10
    noop = [
        x
        for x in await audits(session, "deed_points", str(deed_id))
        if x.action == "deed_points.appeal_overturn_noop"
    ]
    assert len(noop) == 1
    assert noop[0].actor_id == reviewer.user_id
    assert noop[0].new_value is not None
    assert noop[0].new_value["appeal_id"] == str(appeal_id)
    assert noop[0].new_value["override_id"] == str(cut_id)
    assert noop[0].new_value["in_force_points"] == 10


async def test_overturn_that_clears_records_no_noop(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, doer, deed_id = await scored_deed(session)
    a1, reviewer = await admin(session), await admin(session)
    cut = await override(client, a1, deed_id, points=4)
    cut_id = uuid.UUID(cut.json()["overrides"][0]["id"])
    await _overturn(session, cut_id, doer.user_id, reviewer.user_id)
    await run_scoring_for(session, "fraud.appeal_decided")
    assert await points(session, deed_id, DOER) == 10
    actions = {x.action for x in await audits(session, "deed_points", str(deed_id))}
    assert "deed_points.appeal_overturn_noop" not in actions


async def test_overturn_restores_the_points_from_before_the_appealed_change(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Rules 10, raised to 20, then cut to 15: overturning the cut gives back 20, not 10."""
    _, doer, deed_id = await scored_deed(session)
    a1, a2, reviewer = await admin(session), await admin(session), await admin(session)
    assert (await override(client, a1, deed_id, points=20)).status_code == 200
    cut = await override(client, a2, deed_id, points=15)
    assert cut.status_code == 200, cut.json()
    cut_id = uuid.UUID(cut.json()["overrides"][-1]["id"])
    assert await points(session, deed_id, DOER) == 15
    await _overturn(session, cut_id, doer.user_id, reviewer.user_id)
    assert await run_scoring_for(session, "fraud.appeal_decided") == 1
    assert await points(session, deed_id, DOER) == 20
    # Going back up is not a lowering, so no new notice event.
    assert [
        (e["old_points"], e["new_points"]) for e in await overridden_events(session, deed_id)
    ] == [(20, 15)]


# --------------------------------------------------- L3: case_id validation


async def test_case_id_must_be_about_the_person_and_visible(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, doer, deed_id = await scored_deed(session)
    a = await admin(session)
    other, stranger = await members(session, 2)
    about_doer = await open_case(session, FlagCaseType.CONTENT, [doer], [deed_id])
    about_other = await open_case(session, FlagCaseType.CONTENT, [other], [deed_id])

    for case_id in (uuid.uuid4(), about_other):
        res = await override(client, a, deed_id, points=5, case_id=str(case_id))
        assert (res.status_code, res.json()["code"]) == (422, "unknown_case"), case_id
    # The doer's case does not name the reporter.
    res = await override(client, a, deed_id, role="reporter", points=1, case_id=str(about_doer))
    assert (res.status_code, res.json()["code"]) == (422, "unknown_case")

    # An admin related to another subject of the case can't cite it.
    shared = await open_case(session, FlagCaseType.RECIPROCAL, [doer, stranger], [deed_id])
    related = await admin(session)
    await scan_and_report(session, related, stranger)
    await session.commit()
    res = await override(client, related, deed_id, points=5, case_id=str(shared))
    assert (res.status_code, res.json()["code"]) == (422, "unknown_case")

    ok = await override(client, a, deed_id, points=5, case_id=str(about_doer))
    assert ok.status_code == 200, ok.json()
    assert ok.json()["overrides"][-1]["case_id"] == str(about_doer)
