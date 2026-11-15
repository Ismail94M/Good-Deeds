"""Real races between separate database sessions (docs/M2-DESIGN.md §7, §8 Race conditions).

Unlike the other tests, these commit for real (two connections can't see each other's
uncommitted rows), so the `committed` fixture deletes everything they created afterwards.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, engine
from app.core.errors import AppError
from app.modules.deeds import _qr_deeds, _scans
from app.modules.deeds.models import Deed, QrScan, SameActGroup
from tests.deeds.helpers import Member, make_member, report, scan, scan_and_report


@dataclass
class Committed:
    user_ids: list[uuid.UUID] = field(default_factory=list)

    async def member(self) -> Member:
        async with SessionLocal() as s:
            m = await make_member(s, phone_history=False)
        self.user_ids.append(m.user_id)
        return m


@pytest.fixture
async def committed(migrated_db: None) -> AsyncIterator[Committed]:
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
            d = {"d": deed_ids}
            # The ledger is append-only; this cleanup is the one sanctioned exception.
            await conn.execute(
                text(
                    "ALTER TABLE verification_events "
                    "DISABLE TRIGGER verification_events_append_only"
                )
            )
            await conn.execute(text("DELETE FROM verification_events WHERE deed_id = ANY(:d)"), d)
            await conn.execute(
                text(
                    "ALTER TABLE verification_events ENABLE TRIGGER verification_events_append_only"
                )
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
        # Consents and doer codes cascade.
        await conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), ids)


def _meet_before(monkeypatch: pytest.MonkeyPatch, module: object, name: str) -> None:
    """Make two concurrent callers of ``module.name`` wait for each other before running it,
    so both really are inside the contended step at once (opening a connection can otherwise
    let one finish before the other starts)."""
    real = getattr(module, name)
    barrier = asyncio.Barrier(2)

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        async with asyncio.timeout(10):
            await barrier.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(module, name, wrapper)


async def _in_own_session[T](fn: Callable[[AsyncSession], Awaitable[T]]) -> T | AppError:
    async with SessionLocal() as s:
        try:
            result = await fn(s)
        except AppError as exc:
            await s.rollback()
            return exc
        await s.commit()
        return result


async def test_two_submits_of_one_scan_create_one_deed(
    committed: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    witness, doer = await committed.member(), await committed.member()
    async with SessionLocal() as s:
        scanned = await scan(s, witness, doer)
        await s.commit()
    # Both wait together before their first lock (the D16 ``reporter_month`` lock, which is
    # taken before the scan row), then race to consume the one scan.
    _meet_before(monkeypatch, _qr_deeds, "ensure_under_monthly_limit")

    results = await asyncio.gather(
        _in_own_session(lambda s: report(s, witness, scanned.scan_id)),
        _in_own_session(lambda s: report(s, witness, scanned.scan_id)),
    )
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1, results
    assert errors[0].code == "scan_used"
    async with SessionLocal() as s:
        deeds = await s.scalar(
            select(func.count()).select_from(Deed).where(Deed.scan_id == scanned.scan_id)
        )
    assert deeds == 1


async def test_two_verifications_race_for_the_last_counted_slot(
    committed: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    doer = await committed.member()
    witnesses = [await committed.member() for _ in range(6)]
    async with SessionLocal() as s:
        for i, w in enumerate(witnesses[:4]):
            await scan_and_report(s, w, doer, east_m=i * 10)
        scans = [await scan(s, w, doer, east_m=50 + i) for i, w in enumerate(witnesses[4:])]
        await s.commit()
    # Both reach grouping with the group at 4/5; only the advisory lock keeps it at the cap.
    _meet_before(monkeypatch, _qr_deeds, "assign_group")

    (w5, w6), (s5, s6) = witnesses[4:], scans
    results = await asyncio.gather(
        _in_own_session(lambda s: report(s, w5, s5.scan_id)),
        _in_own_session(lambda s: report(s, w6, s6.scan_id)),
    )
    assert not any(isinstance(r, AppError) for r in results), results
    counted = [
        r.same_act.counts_for_points for r in results if not isinstance(r, AppError) and r.same_act
    ]
    assert sorted(counted) == [False, True]
    async with SessionLocal() as s:
        groups = (
            await s.scalars(select(SameActGroup).where(SameActGroup.doer_user_id == doer.user_id))
        ).all()
        assert len(groups) == 1
        group = groups[0]
        assert (group.verified_count, group.counted_count, group.distinct_reporters) == (6, 5, 6)
        counted_deeds = await s.scalar(
            select(func.count())
            .select_from(Deed)
            .where(Deed.same_act_group_id == group.id, Deed.counts_for_points)
        )
        assert counted_deeds == 5


async def test_concurrent_scans_of_one_window_record_one(
    committed: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    witness, doer = await committed.member(), await committed.member()
    # Both pass the friendly pre-checks; the unique constraints decide.
    _meet_before(monkeypatch, _scans, "_window_used")
    results = await asyncio.gather(
        _in_own_session(lambda s: scan(s, witness, doer)),
        _in_own_session(lambda s: scan(s, witness, doer)),
    )
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1, results
    # Both unique constraints are violated; whichever Postgres reports, it is a clean 409.
    assert errors[0].status_code == 409
    assert errors[0].code in {"already_scanned", "already_recognized_today"}
    async with SessionLocal() as s:
        scans = await s.scalar(
            select(func.count())
            .select_from(QrScan)
            .where(QrScan.witness_user_id == witness.user_id)
        )
    assert scans == 1


async def test_two_reports_at_fourteen_of_fifteen_allow_one(
    committed: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D16: two simultaneous submits by a reporter at 14/15 this month, about different doers:
    the ``reporter_month`` advisory lock lets exactly one become the 15th."""
    witness = await committed.member()
    doers = [await committed.member() for _ in range(16)]
    t0 = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    async with SessionLocal() as s:
        for i, d in enumerate(doers[:14]):
            await scan_and_report(s, witness, d, at=t0 + timedelta(minutes=10 * i))
        at = t0 + timedelta(hours=3)
        s1, s2 = [await scan(s, witness, d, at=at) for d in doers[14:]]
        await s.commit()
    # Both wait together, then race for the lock and the count.
    _meet_before(monkeypatch, _qr_deeds, "ensure_under_monthly_limit")

    submit = timedelta(minutes=1)
    results = await asyncio.gather(
        _in_own_session(lambda s: report(s, witness, s1.scan_id, at=at + submit)),
        _in_own_session(lambda s: report(s, witness, s2.scan_id, at=at + submit)),
    )
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1, results
    assert errors[0].code == "monthly_report_limit" and errors[0].status_code == 429
    async with SessionLocal() as s:
        reported = await s.scalar(
            select(func.count()).select_from(Deed).where(Deed.reporter_user_id == witness.user_id)
        )
        unused = await s.scalar(
            select(func.count())
            .select_from(QrScan)
            .where(QrScan.witness_user_id == witness.user_id, QrScan.consumed_at.is_(None))
        )
    assert reported == 15
    assert unused == 1  # the refused report's scan was not consumed
