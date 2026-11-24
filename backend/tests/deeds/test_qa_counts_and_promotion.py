"""QA1: the Verified Deeds count (D15) and withdraw-then-promote loops (D13).

D15 (replaces D12): ``verified`` counts counted verified reports (``counts_for_points``), so it
equals the leaderboard figure; self-logs never count.
D13: however withdrawals and new reports are interleaved, a same-act group never has more than
``cap`` counted verified reports, never two counted reports by one reporter, and its counters
always match its rows.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, engine
from app.core.errors import AppError
from app.modules.deeds import _grouping, _invites
from app.modules.deeds.models import Deed, DeedStatus, Invite, InviteStatus, SameActGroup
from tests.deeds.helpers import (
    Member,
    make_member,
    members,
    publish_limits,
    report,
    scan,
    scan_and_report,
)
from tests.deeds.invite_helpers import accept, create_invite, withdraw
from tests.deeds.test_concurrency import _in_own_session, _meet_before


async def _verified_count(client: AsyncClient, who: Member) -> int:
    response = await client.get(f"/api/users/{who.handle}/deed-counts")
    assert response.status_code == 200, response.text
    value: int = response.json()["verified"]
    return value


async def _group_of(session: AsyncSession, deed_id: uuid.UUID) -> SameActGroup:
    deed = await session.get(Deed, deed_id, populate_existing=True)
    assert deed is not None and deed.same_act_group_id is not None
    group = await session.get(SameActGroup, deed.same_act_group_id, populate_existing=True)
    assert group is not None
    return group


async def assert_group_invariants(session: AsyncSession, group_id: uuid.UUID) -> SameActGroup:
    """The counters match the rows, and the cap and once-per-reporter rules hold."""
    group = await session.get(SameActGroup, group_id, populate_existing=True)
    assert group is not None
    deeds = (
        await session.scalars(
            select(Deed)
            .where(Deed.same_act_group_id == group_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    verified = [d for d in deeds if d.status == DeedStatus.VERIFIED]
    counted = [d for d in verified if d.counts_for_points]
    assert group.verified_count == len(verified)
    assert group.counted_count == len(counted)
    assert group.distinct_reporters == len({d.reporter_user_id for d in verified})
    assert len(counted) <= group.cap, "same-act cap exceeded"
    reporters = [d.reporter_user_id for d in counted]
    assert len(reporters) == len(set(reporters)), "a reporter counts twice in one act"
    # Free slots are never left while an eligible uncounted report waits.
    counted_reporters = set(reporters)
    waiting = [
        d
        for d in verified
        if not d.counts_for_points and d.reporter_user_id not in counted_reporters
    ]
    if len(counted) < group.cap:
        assert waiting == [], "a free slot was left while an eligible report waits"
    return group


# --------------------------------------------------- D15: Verified Deeds counts counted reports


async def test_verified_counts_only_counted_reports(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Seven witnesses of one act: only 5 count for points, so the profile shows 5 (D15)."""
    doer = await make_member(session)
    witnesses = await members(session, 7)
    t0 = datetime.now(UTC) - timedelta(hours=3)
    deeds = [
        await scan_and_report(session, w, doer, at=t0 + timedelta(minutes=i), east_m=i * 5)
        for i, w in enumerate(witnesses)
    ]
    await session.commit()
    assert sum(1 for d in deeds if d.same_act and d.same_act.counts_for_points) == 5
    assert await _verified_count(client, doer) == 5


async def test_twenty_reports_of_one_act_count_as_five(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer = await make_member(session)
    witnesses = await members(session, 20)
    t0 = datetime.now(UTC) - timedelta(hours=3)
    for i, w in enumerate(witnesses):
        await scan_and_report(session, w, doer, at=t0 + timedelta(minutes=i), east_m=i * 5)
    await session.commit()
    assert await _verified_count(client, doer) == 5


async def test_reports_in_separate_windows_each_count(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Separate same-act windows (more than 2 h apart) are separate acts: each report counts."""
    doer = await make_member(session)
    witnesses = await members(session, 6)
    t0 = datetime.now(UTC) - timedelta(days=2)
    for i, w in enumerate(witnesses):
        await scan_and_report(session, w, doer, at=t0 + timedelta(hours=3 * i))
    await session.commit()
    assert await _verified_count(client, doer) == 6


async def test_same_reporter_twice_across_midnight_counts_once(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer, witness = await members(session, 2)
    # 23:45 IST and 00:05 IST: one act, two reports, the second uncounted.
    first = await scan_and_report(
        session, witness, doer, at=datetime(2026, 11, 12, 18, 15, tzinfo=UTC)
    )
    second = await scan_and_report(
        session, witness, doer, at=datetime(2026, 11, 12, 18, 35, tzinfo=UTC)
    )
    await session.commit()
    assert second.same_act is not None and second.same_act.counts_for_points is False
    assert (await _group_of(session, first.id)).id == (await _group_of(session, second.id)).id
    assert await _verified_count(client, doer) == 1


async def test_invite_deeds_count_only_once_accepted(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, lapsed_reporter, doer = await members(session, 3)
    created, token = await create_invite(session, reporter)
    lapsed, _ = await create_invite(
        session, lapsed_reporter, at=datetime.now(UTC) - timedelta(days=8)
    )
    await _invites.expire_invites(session)
    await session.commit()
    assert await _verified_count(client, doer) == 0  # pending and unverified: no doer at all

    await accept(session, token, doer)
    await session.commit()
    assert await _verified_count(client, doer) == 1

    # Withdrawn after acceptance: back to 0.
    await withdraw(session, reporter, created.id)
    await session.commit()
    assert await _verified_count(client, doer) == 0
    lapsed_row = await session.get(Deed, lapsed.id)
    assert lapsed_row is not None and lapsed_row.status == DeedStatus.UNVERIFIED


@pytest.mark.parametrize(
    "status",
    [DeedStatus.UNVERIFIED, DeedStatus.REJECTED, DeedStatus.REMOVED],
)
async def test_closed_statuses_do_not_count(
    client: AsyncClient, session: AsyncSession, status: DeedStatus
) -> None:
    doer, w1, w2 = await members(session, 3)
    await scan_and_report(session, w1, doer)
    closed = await scan_and_report(session, w2, doer, east_m=20)
    await session.execute(update(Deed).where(Deed.id == closed.id).values(status=status))
    await session.commit()
    assert await _verified_count(client, doer) == 1


async def test_promotion_after_withdrawal_keeps_the_count_consistent(
    client: AsyncClient, session: AsyncSession
) -> None:
    await publish_limits(session, same_act_cap=1)
    doer, a, b = await members(session, 3)
    t0 = datetime.now(UTC) - timedelta(hours=3)
    first = await scan_and_report(session, a, doer, at=t0)
    await scan_and_report(session, b, doer, at=t0 + timedelta(minutes=1), east_m=10)
    await session.commit()
    assert await _verified_count(client, doer) == 1  # cap 1: only a counts
    await withdraw(session, a, first.id)  # b is promoted
    await session.commit()
    assert await _verified_count(client, doer) == 1  # b now counts; a no longer does
    group = await assert_group_invariants(session, (await _group_of(session, first.id)).id)
    assert group.counted_count == 1
    # Self-logs by the doer never move it.
    for _ in range(3):
        response = await client.post(
            "/api/me/self-logs",
            json={
                "category": "animals",
                "description": "Fed the street dogs near the park",
                "occurred_at": datetime.now(UTC).isoformat(),
            },
            headers=doer.headers,
        )
        assert response.status_code == 201
    counts = (await client.get(f"/api/users/{doer.handle}/deed-counts")).json()
    assert counts == {"self_logged": 3, "verified": 1}


# --------------------------------------------------- D13: withdraw-then-promote loops


async def test_withdraw_then_promote_loop_never_exceeds_the_cap(session: AsyncSession) -> None:
    """Colluding reporters withdraw counted reports one after another, re-report on the next
    IST day and add new reporters, trying to push more than 5 counted reports into one act."""
    doer = await make_member(session)
    crew = await members(session, 9)
    # 22:30 IST on the 12th; the act's window runs to 00:30 IST on the 13th.
    t0 = datetime(2026, 11, 12, 17, 0, tzinfo=UTC)
    deeds: dict[int, uuid.UUID] = {}
    for i in range(7):
        out = await scan_and_report(session, crew[i], doer, at=t0 + timedelta(minutes=i), east_m=i)
        deeds[i] = out.id
    group_id = (await _group_of(session, deeds[0])).id
    await assert_group_invariants(session, group_id)

    # Withdraw counted reports one by one; each promotes exactly one waiting report.
    for i in range(3):
        await withdraw(session, crew[i], deeds[i], at=t0 + timedelta(minutes=10 + i))
        group = await assert_group_invariants(session, group_id)
        assert group.counted_count == (5 if i < 2 else 4)  # two waiting reports, then none

    # The withdrawers re-report after IST midnight (pair-day is a new day), within the window.
    after_midnight = datetime(2026, 11, 12, 18, 40, tzinfo=UTC)
    for i in range(3):
        out = await scan_and_report(
            session, crew[i], doer, at=after_midnight + timedelta(minutes=i), east_m=i
        )
        assert (await _group_of(session, out.id)).id == group_id
        await assert_group_invariants(session, group_id)
    # Two newcomers on top.
    for i in (7, 8):
        await scan_and_report(
            session, crew[i], doer, at=after_midnight + timedelta(minutes=i), east_m=i
        )
        await assert_group_invariants(session, group_id)
    group = await assert_group_invariants(session, group_id)
    assert group.counted_count == 5

    # Withdraw everything that counts, in turn: each time the cap still holds.
    while True:
        counted = (
            await session.scalars(
                select(Deed)
                .where(
                    Deed.same_act_group_id == group_id,
                    Deed.status == DeedStatus.VERIFIED,
                    Deed.counts_for_points.is_(True),
                )
                .order_by(Deed.verified_at)
                .limit(1)
            )
        ).first()
        if counted is None:
            break
        reporter = next(m for m in crew if m.user_id == counted.reporter_user_id)
        await withdraw(session, reporter, counted.id)
        await assert_group_invariants(session, group_id)
    group = await assert_group_invariants(session, group_id)
    assert group.counted_count == 0 and group.verified_count == 0


async def test_cap_lowered_in_config_keeps_promoting_to_the_groups_own_cap(
    session: AsyncSession,
) -> None:
    doer = await make_member(session)
    witnesses = await members(session, 7)
    t0 = datetime.now(UTC) - timedelta(hours=4)
    deeds = [
        await scan_and_report(session, w, doer, at=t0 + timedelta(minutes=i), east_m=i * 5)
        for i, w in enumerate(witnesses[:6])
    ]
    group_id = (await _group_of(session, deeds[0].id)).id
    await publish_limits(session, same_act_cap=2)

    await withdraw(session, witnesses[0], deeds[0].id)
    group = await assert_group_invariants(session, group_id)
    assert (group.cap, group.counted_count) == (5, 5)  # the 6th was promoted under cap 5
    sixth = await session.get(Deed, deeds[5].id, populate_existing=True)
    assert sixth is not None and sixth.counts_for_points is True

    # Raising the cap later doesn't let the old group take a 6th counted report either.
    await publish_limits(session, same_act_cap=10)
    late = await scan_and_report(
        session, witnesses[6], doer, at=t0 + timedelta(minutes=30), east_m=40
    )
    assert late.same_act is not None and late.same_act.counts_for_points is False
    await assert_group_invariants(session, group_id)


# --------------------------------------------------- real races (committed data)


async def _cleanup(user_ids: list[uuid.UUID]) -> None:
    ids = {"ids": user_ids}
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
            ledgers = ("verification_events", "deed_revisions", "audit_log")
            for table in ledgers:
                await conn.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {table}_append_only"))
            await conn.execute(text("DELETE FROM verification_events WHERE deed_id = ANY(:d)"), d)
            await conn.execute(text("DELETE FROM deed_revisions WHERE deed_id = ANY(:d)"), d)
            await conn.execute(
                text("DELETE FROM audit_log WHERE entity_type = 'deed' AND entity_id = ANY(:s)"), d
            )
            for table in ledgers:
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
        await conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), ids)


class Committed:
    def __init__(self) -> None:
        self.user_ids: list[uuid.UUID] = []

    async def member(self) -> Member:
        async with SessionLocal() as s:
            m = await make_member(s, phone_history=False)
        self.user_ids.append(m.user_id)
        return m


@pytest.fixture
async def committed_qa(migrated_db: None) -> AsyncIterator[Committed]:
    tracker = Committed()
    yield tracker
    if tracker.user_ids:
        await _cleanup(tracker.user_ids)


async def test_two_concurrent_withdrawals_promote_two_distinct_reports(
    committed_qa: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    doer = await committed_qa.member()
    witnesses = [await committed_qa.member() for _ in range(7)]
    async with SessionLocal() as s:
        deeds = [await scan_and_report(s, w, doer, east_m=i * 5) for i, w in enumerate(witnesses)]
        await s.commit()
    _meet_before(monkeypatch, _grouping, "lock_doer")

    results = await asyncio.gather(
        _in_own_session(lambda s: withdraw(s, witnesses[0], deeds[0].id)),
        _in_own_session(lambda s: withdraw(s, witnesses[1], deeds[1].id)),
    )
    assert not any(isinstance(r, AppError) for r in results), results
    async with SessionLocal() as s:
        group = await assert_group_invariants(s, (await _group_of(s, deeds[2].id)).id)
        assert (group.verified_count, group.counted_count) == (5, 5)
        promoted = await s.scalar(
            select(func.count())
            .select_from(Deed)
            .where(Deed.id.in_([deeds[5].id, deeds[6].id]), Deed.counts_for_points.is_(True))
        )
        assert promoted == 2


async def test_accept_racing_withdrawal_of_the_pending_deed(
    committed_qa: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exactly one wins, and the deed and invite never disagree (e.g. a withdrawn deed with an
    accepted invite, or a verified deed with a cancelled one)."""
    reporter, doer = await committed_qa.member(), await committed_qa.member()
    async with SessionLocal() as s:
        created, token = await create_invite(s, reporter, north_m=0)
        await s.commit()
    # Both start together: accept as it locks the invite, withdrawal as it begins.
    barrier = asyncio.Barrier(2)
    real_lock = _invites._lock_invite

    async def lock_after_meeting(*args: object) -> Invite | None:
        async with asyncio.timeout(10):
            await barrier.wait()
        return await real_lock(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(_invites, "_lock_invite", lock_after_meeting)

    async def withdraw_after_meeting(s: AsyncSession) -> object:
        async with asyncio.timeout(10):
            await barrier.wait()
        return await withdraw(s, reporter, created.id)

    results = await asyncio.gather(
        _in_own_session(lambda s: accept(s, token, doer)),
        _in_own_session(withdraw_after_meeting),
    )
    errors = [r for r in results if isinstance(r, AppError)]
    async with SessionLocal() as s:
        deed = await s.get(Deed, created.id)
        invite = (await s.scalars(select(Invite).where(Invite.deed_id == created.id))).one()
        assert deed is not None
        pair = (deed.status, invite.status)
        assert pair in {
            (DeedStatus.WITHDRAWN, InviteStatus.CANCELLED),
            (DeedStatus.WITHDRAWN, InviteStatus.ACCEPTED),  # accepted first, then withdrawn
        }, pair
        if pair[1] == InviteStatus.CANCELLED:
            assert len(errors) == 1 and errors[0].code == "invite_cancelled"
            assert deed.doer_user_id is None and deed.same_act_group_id is None
        else:
            assert errors == []
            group = await assert_group_invariants(s, deed.same_act_group_id)  # type: ignore[arg-type]
            assert group.verified_count == 0


async def test_withdraw_racing_two_new_reports_at_full_cap(
    committed_qa: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    doer = await committed_qa.member()
    witnesses = [await committed_qa.member() for _ in range(7)]
    async with SessionLocal() as s:
        deeds = [
            await scan_and_report(s, w, doer, east_m=i * 5) for i, w in enumerate(witnesses[:5])
        ]
        scans = [await scan(s, w, doer, east_m=60 + i) for i, w in enumerate(witnesses[5:])]
        await s.commit()
    barrier = asyncio.Barrier(3)
    real = _grouping.lock_doer

    async def meet(*args: object, **kwargs: object) -> None:
        async with asyncio.timeout(10):
            await barrier.wait()
        await real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_grouping, "lock_doer", meet)
    results = await asyncio.gather(
        _in_own_session(lambda s: withdraw(s, witnesses[0], deeds[0].id)),
        _in_own_session(lambda s: report(s, witnesses[5], scans[0].scan_id)),
        _in_own_session(lambda s: report(s, witnesses[6], scans[1].scan_id)),
    )
    assert not any(isinstance(r, AppError) for r in results), results
    async with SessionLocal() as s:
        group = await assert_group_invariants(s, (await _group_of(s, deeds[1].id)).id)
        assert (group.verified_count, group.counted_count) == (6, 5)
