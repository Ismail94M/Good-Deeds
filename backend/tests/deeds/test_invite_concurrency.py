"""Real races for invites, withdrawal and the expiry job (docs/M2-DESIGN.md §7, §8 Race
conditions). Like `test_concurrency.py`, these commit for real, so the `committed_b6` fixture
deletes everything they created afterwards (including the append-only rows they wrote)."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, engine
from app.core.errors import AppError
from app.modules.deeds import _grouping, _invites
from app.modules.deeds.models import (
    Deed,
    DeedStatus,
    Invite,
    InviteStatus,
    SameActGroup,
    VerificationEvent,
    VerificationEventKind,
)
from tests.deeds.helpers import Member, make_member, report, scan, scan_and_report
from tests.deeds.invite_helpers import accept, create_invite, share_device, withdraw
from tests.deeds.test_concurrency import _in_own_session, _meet_before

_APPEND_ONLY = ("verification_events", "deed_revisions", "audit_log")


@dataclass
class Committed:
    user_ids: list[uuid.UUID] = field(default_factory=list)

    async def member(self) -> Member:
        async with SessionLocal() as s:
            m = await make_member(s, phone_history=False)
        self.user_ids.append(m.user_id)
        return m


@pytest.fixture
async def committed_b6(migrated_db: None) -> AsyncIterator[Committed]:
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
            # The ledgers are append-only; this cleanup is the one sanctioned exception.
            for table in _APPEND_ONLY:
                await conn.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {table}_append_only"))
            await conn.execute(text("DELETE FROM verification_events WHERE deed_id = ANY(:d)"), d)
            await conn.execute(text("DELETE FROM deed_revisions WHERE deed_id = ANY(:d)"), d)
            await conn.execute(
                text("DELETE FROM audit_log WHERE entity_type = 'deed' AND entity_id = ANY(:s)"), d
            )
            for table in _APPEND_ONLY:
                await conn.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {table}_append_only"))
            await conn.execute(
                text("DELETE FROM outbox_events WHERE (payload->>'deed_id')::uuid = ANY(:d)"), d
            )
            await conn.execute(text("DELETE FROM invites WHERE deed_id = ANY(:d)"), d)
            await conn.execute(text("DELETE FROM deeds WHERE id = ANY(:d)"), d)
        await conn.execute(text("DELETE FROM same_act_groups WHERE doer_user_id = ANY(:ids)"), ids)
        await conn.execute(
            text(
                "DELETE FROM qr_scans WHERE witness_user_id = ANY(:ids) OR doer_user_id = ANY(:ids)"
            ),
            ids,
        )
        # Consents, doer codes and devices cascade.
        await conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), ids)


def _meet(
    monkeypatch: pytest.MonkeyPatch, module: object, name: str, parties: int, *, after: bool
) -> None:
    """Like `_meet_before` for any number of callers; with ``after`` they meet once each has
    run the step (so each still holds what the step locked)."""
    real = getattr(module, name)
    barrier = asyncio.Barrier(parties)

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not after:
            async with asyncio.timeout(10):
                await barrier.wait()
            return await real(*args, **kwargs)
        result = await real(*args, **kwargs)
        async with asyncio.timeout(10):
            await barrier.wait()
        return result

    monkeypatch.setattr(module, name, wrapper)


async def _commit_invite(reporter: Member, at: datetime | None = None) -> tuple[uuid.UUID, str]:
    async with SessionLocal() as s:
        out, token = await create_invite(s, reporter, at=at)
        await s.commit()
    return out.id, token


async def test_six_concurrent_invite_creates_allow_five(
    committed_b6: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    reporter = await committed_b6.member()
    at = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)
    # All six pass validation and wait together before their first lock (D16's
    # ``reporter_month``, then ``invites``).
    _meet(monkeypatch, _invites, "ensure_under_monthly_limit", 6, after=False)

    async def create(s: AsyncSession) -> uuid.UUID:
        out, _ = await create_invite(s, reporter, at=at)
        return out.id

    results = await asyncio.gather(*(_in_own_session(create) for _ in range(6)))
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1, results
    assert errors[0].code == "invite_daily_limit"
    async with SessionLocal() as s:
        created = await s.scalar(
            select(func.count())
            .select_from(Invite)
            .where(Invite.reporter_user_id == reporter.user_id)
        )
    assert created == 5


async def test_two_accepters_race_for_one_invite(
    committed_b6: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    reporter, doer_1, doer_2 = [await committed_b6.member() for _ in range(3)]
    deed_id, token = await _commit_invite(reporter)
    _meet_before(monkeypatch, _invites, "_lock_invite")

    results = await asyncio.gather(
        _in_own_session(lambda s: accept(s, token, doer_1)),
        _in_own_session(lambda s: accept(s, token, doer_2)),
    )
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1, results
    assert errors[0].code == "invite_used"
    async with SessionLocal() as s:
        deed = await s.get(Deed, deed_id)
        assert deed is not None and deed.status == DeedStatus.VERIFIED
        assert deed.doer_user_id in {doer_1.user_id, doer_2.user_id}
        events = await s.scalar(
            select(func.count())
            .select_from(VerificationEvent)
            .where(VerificationEvent.deed_id == deed_id)
        )
    assert events == 1


async def test_two_shared_device_accepts_at_fourteen_of_fifteen(
    committed_b6: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    reporter, doer = await committed_b6.member(), await committed_b6.member()
    async with SessionLocal() as s:
        await share_device(s, reporter, doer)
        await s.commit()
    oct_1 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
    async with SessionLocal() as s:
        # The reporter may create only 15 deeds an IST month (D16), so 6 of the 14 accepted
        # invites were created on 25-30 September and accepted on 1 October: the shared-device
        # limit counts by accept month, the reporting limit by report month.
        for i in range(6, 0, -1):
            _, token = await create_invite(s, reporter, at=oct_1 - timedelta(days=i))
            await accept(s, token, doer, at=oct_1 + timedelta(minutes=i))
        for i in range(1, 9):
            day = oct_1 + timedelta(days=i)
            _, token = await create_invite(s, reporter, at=day)
            await accept(s, token, doer, at=day + timedelta(minutes=1))
        await s.commit()
    pending = [await _commit_invite(reporter, at=oct_1 + timedelta(days=d)) for d in (10, 11)]
    at = oct_1 + timedelta(days=12)
    # Both find a shared device and wait at the pair lock together.
    _meet_before(monkeypatch, _invites, "advisory_xact_lock")

    (_, t_1), (_, t_2) = pending
    results = await asyncio.gather(
        _in_own_session(lambda s: accept(s, t_1, doer, at=at)),
        _in_own_session(lambda s: accept(s, t_2, doer, at=at)),
    )
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1, results
    assert errors[0].code == "same_device_limit"
    async with SessionLocal() as s:
        shared = await s.scalar(
            select(func.count())
            .select_from(Invite)
            .where(
                Invite.reporter_user_id == reporter.user_id,
                Invite.shared_device.is_(True),
                Invite.status == InviteStatus.ACCEPTED,
            )
        )
    assert shared == 15


async def test_withdraw_racing_a_new_verification_never_exceeds_cap(
    committed_b6: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    doer = await committed_b6.member()
    witnesses = [await committed_b6.member() for _ in range(6)]
    async with SessionLocal() as s:
        deeds = [
            await scan_and_report(s, w, doer, east_m=i * 10) for i, w in enumerate(witnesses[:5])
        ]
        sixth_scan = await scan(s, witnesses[5], doer, east_m=60)
        await s.commit()
    # The group is at 5/5; both wait at the same_act lock together.
    _meet_before(monkeypatch, _grouping, "lock_doer")

    results = await asyncio.gather(
        _in_own_session(lambda s: withdraw(s, witnesses[0], deeds[0].id)),
        _in_own_session(lambda s: report(s, witnesses[5], sixth_scan.scan_id)),
    )
    assert not any(isinstance(r, AppError) for r in results), results
    async with SessionLocal() as s:
        group = (
            await s.scalars(select(SameActGroup).where(SameActGroup.doer_user_id == doer.user_id))
        ).one()
        assert (group.verified_count, group.counted_count, group.distinct_reporters) == (5, 5, 5)
        counted = await s.scalar(
            select(func.count())
            .select_from(Deed)
            .where(
                Deed.same_act_group_id == group.id,
                Deed.status == DeedStatus.VERIFIED,
                Deed.counts_for_points.is_(True),
            )
        )
        assert counted == 5
        # Whichever ran first, the new report ends up counted (slot taken or promoted).
        sixth = await s.scalar(select(Deed).where(Deed.scan_id == sixth_scan.scan_id))
        assert sixth is not None and sixth.counts_for_points is True


async def test_concurrent_expiry_runs_expire_each_invite_once(
    committed_b6: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    reporter = await committed_b6.member()
    created_at = datetime(2001, 1, 1, 6, 0, tzinfo=UTC)
    deed_ids = [(await _commit_invite(reporter, at=created_at))[0] for _ in range(3)]
    due = created_at + timedelta(days=8)
    # Each run selects its batch, then waits for the other while holding the row locks.
    _meet(monkeypatch, _invites, "_due_invites", 2, after=True)

    async def run(s: AsyncSession) -> int:
        return await _invites.expire_invites(s, now=due, batch_size=2)

    results = await asyncio.gather(_in_own_session(run), _in_own_session(run))
    assert not any(isinstance(r, AppError) for r in results), results
    assert sorted(r for r in results if isinstance(r, int)) == [1, 2]
    async with SessionLocal() as s:
        for deed_id in deed_ids:
            deed = await s.get(Deed, deed_id)
            assert deed is not None and deed.status == DeedStatus.UNVERIFIED
            expired = await s.scalar(
                select(func.count())
                .select_from(VerificationEvent)
                .where(
                    VerificationEvent.deed_id == deed_id,
                    VerificationEvent.kind == VerificationEventKind.INVITE_EXPIRED,
                )
            )
            assert expired == 1


async def test_withdraw_racing_a_prize_rank_upgrade_does_not_deadlock(
    committed_b6: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The QR report holds ``same_act`` while it upgrades the invite deed's row; withdrawal of
    that invite deed must not hold the row while waiting for ``same_act``."""
    reporter, doer, witness = [await committed_b6.member() for _ in range(3)]
    t0 = datetime.now(UTC) - timedelta(hours=1)
    async with SessionLocal() as s:
        invite_deed, token = await create_invite(s, reporter, at=t0, occurred_at=t0, north_m=0)
        await accept(s, token, doer, at=t0 + timedelta(minutes=1))
        witness_scan = await scan(s, witness, doer)
        await s.commit()
    _meet_before(monkeypatch, _grouping, "lock_doer")

    results = await asyncio.gather(
        _in_own_session(lambda s: withdraw(s, reporter, invite_deed.id)),
        _in_own_session(lambda s: report(s, witness, witness_scan.scan_id)),
    )
    assert not any(isinstance(r, AppError) for r in results), results
    async with SessionLocal() as s:
        deed = await s.get(Deed, invite_deed.id)
        assert deed is not None and deed.status == DeedStatus.WITHDRAWN
        group = await s.get(SameActGroup, deed.same_act_group_id)
        assert group is not None
        assert (group.verified_count, group.counted_count, group.distinct_reporters) == (1, 1, 1)
