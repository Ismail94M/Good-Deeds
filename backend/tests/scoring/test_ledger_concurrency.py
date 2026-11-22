"""Real races on the points ledger (docs/M4-DESIGN.md §4.1, D52): the doer-day advisory lock
keeps the daily cap exact, and two runs for one deed write one entry. Like the deeds concurrency
tests these commit for real, so the ``committed_scoring`` fixture deletes everything they created
afterwards, including the append-only rows (the one sanctioned exception)."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.modules.scoring import _ledger
from app.modules.scoring._ledger import reconcile_deed
from app.modules.scoring.models import LedgerKind, LedgerRole
from tests.deeds.helpers import Member, make_member, scan_and_report
from tests.deeds.test_concurrency import _in_own_session, _meet_before
from tests.scoring.helpers import active, ledger

_APPEND_ONLY = ("points_ledger", "impact_evaluations", "verification_events", "deed_revisions")
# 12 November 2026 00:10 IST.
DAY_START = datetime(2026, 11, 11, 18, 40, tzinfo=UTC)


@dataclass
class Committed:
    user_ids: list[uuid.UUID] = field(default_factory=list)

    async def member(self) -> Member:
        async with SessionLocal() as s:
            m = await make_member(s, phone_history=False)
        self.user_ids.append(m.user_id)
        return m


@pytest.fixture
async def committed_scoring(migrated_db: None) -> AsyncIterator[Committed]:
    tracker = Committed()
    yield tracker
    if not tracker.user_ids:
        return
    ids = {"ids": tracker.user_ids}
    async with engine.begin() as conn:
        deed_ids = list(
            (
                await conn.execute(
                    text(
                        "SELECT id FROM deeds WHERE reporter_user_id = ANY(:ids) "
                        "OR doer_user_id = ANY(:ids)"
                    ),
                    ids,
                )
            ).scalars()
        )
        if deed_ids:
            d = {"d": deed_ids, "s": [str(x) for x in deed_ids]}
            for table in _APPEND_ONLY:
                await conn.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {table}_append_only"))
            await conn.execute(text("DELETE FROM points_ledger WHERE deed_id = ANY(:d)"), d)
            await conn.execute(text("DELETE FROM impact_evaluations WHERE deed_id = ANY(:d)"), d)
            await conn.execute(text("DELETE FROM verification_events WHERE deed_id = ANY(:d)"), d)
            await conn.execute(text("DELETE FROM deed_revisions WHERE deed_id = ANY(:d)"), d)
            for table in _APPEND_ONLY:
                await conn.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {table}_append_only"))
            await conn.execute(
                text("DELETE FROM audit_log WHERE entity_type = 'deed' AND entity_id = ANY(:s)"), d
            )
            await conn.execute(
                text("DELETE FROM outbox_events WHERE (payload->>'deed_id')::uuid = ANY(:d)"), d
            )
            await conn.execute(text("DELETE FROM deeds WHERE id = ANY(:d)"), d)
        await conn.execute(text("DELETE FROM same_act_groups WHERE doer_user_id = ANY(:ids)"), ids)
        await conn.execute(
            text(
                "DELETE FROM qr_scans WHERE witness_user_id = ANY(:ids) OR doer_user_id = ANY(:ids)"
            ),
            ids,
        )
        await conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), ids)


async def _committed_deed(reporter: Member, doer: Member, at: datetime) -> uuid.UUID:
    async with SessionLocal() as s:
        out = await scan_and_report(s, reporter, doer, at=at)
        await s.commit()
    return out.id


async def test_two_deeds_racing_for_the_last_daily_slot_get_one_award(
    committed_scoring: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    doer = await committed_scoring.member()
    reporters = [await committed_scoring.member() for _ in range(11)]
    # Separate acts: two hours apart (same_act_window_minutes = 120), one IST day.
    deed_ids = [
        await _committed_deed(r, doer, DAY_START + i * timedelta(minutes=125))
        for i, r in enumerate(reporters)
    ]
    for deed_id in deed_ids[:9]:
        async with SessionLocal() as s:
            await reconcile_deed(s, deed_id)
            await s.commit()

    # Both wait together just before their first lock, then race for the 10th slot.
    _meet_before(monkeypatch, _ledger, "load_scoring")
    results = await asyncio.gather(
        _in_own_session(lambda s: reconcile_deed(s, deed_ids[9])),
        _in_own_session(lambda s: reconcile_deed(s, deed_ids[10])),
    )
    assert all(isinstance(r, list) for r in results)

    async with SessionLocal() as s:
        kinds = []
        for deed_id in deed_ids[9:]:
            row = await active(s, deed_id, LedgerRole.DOER)
            assert row is not None
            kinds.append(row.kind)
    assert sorted(kinds) == [LedgerKind.AWARD, LedgerKind.DECISION]


async def test_two_runs_for_one_deed_write_one_entry(
    committed_scoring: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    reporter, doer = await committed_scoring.member(), await committed_scoring.member()
    deed_id = await _committed_deed(reporter, doer, DAY_START)
    _meet_before(monkeypatch, _ledger, "load_scoring")
    results = await asyncio.gather(
        _in_own_session(lambda s: reconcile_deed(s, deed_id)),
        _in_own_session(lambda s: reconcile_deed(s, deed_id)),
    )
    written = sorted(len(r) for r in results if isinstance(r, list))
    assert written == [0, 2]
    async with SessionLocal() as s:
        rows = await ledger(s, deed_id)
    assert sorted((r.role.value, r.points) for r in rows) == [("doer", 10), ("reporter", 2)]
