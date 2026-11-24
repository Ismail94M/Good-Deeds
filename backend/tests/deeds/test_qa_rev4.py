"""QA of revision 4 (docs/M2-DESIGN.md §0.2): D15, D16 and D17, on top of the developer tests.

- D16 (monthly reporting limit): the IST month edge to the microsecond, a report at 23:59:59 IST
  that only reaches the database after midnight, ``reported_at`` being server time only, the
  149/150/151 boundary (D19 value) over the API on both paths and the scan pre-check, a
  pre-check that passes followed by a refused submit, concurrency across both paths, and bypass
  attempts (statuses, edits, scan stockpiling, accepting an invite in a later month, a disabled
  invite).
- D17 (invites off): ordering against D16 and that a refused invite leaves nothing behind.
- D15 (counted reports): the profile count through a withdraw-and-promote sequence.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.errors import AppError
from app.modules.deeds import _edits, _invites, _qr_deeds
from app.modules.deeds._common import ist_month
from app.modules.deeds._limits import load_limits
from app.modules.deeds._reporter_limit import reported_this_month
from app.modules.deeds.models import Deed, DeedStatus, Invite, QrScan
from app.modules.deeds.schemas import DeedEdit
from app.modules.identity.service import LAUNCH_TZ
from tests.deeds.helpers import (
    Member,
    count,
    deed_body,
    location,
    make_member,
    members,
    publish_limits,
    report,
    scan,
    scan_and_report,
    seed_deeds,
    token_for,
)
from tests.deeds.invite_helpers import accept, create_invite, invite_body, withdraw
from tests.deeds.test_concurrency import _in_own_session
from tests.deeds.test_invite_concurrency import Committed, committed_b6  # noqa: F401
from tests.deeds.test_qa_counts_and_promotion import (
    _group_of,
    _verified_count,
    assert_group_invariants,
)

# 00:00 IST on 1 November 2026 is 18:30 UTC on 31 October.
NOV_1_IST = datetime(2026, 10, 31, 18, 30, tzinfo=UTC)
OCT_31_MORNING = datetime(2026, 10, 31, 4, 0, tzinfo=UTC)  # 09:30 IST
_US = timedelta(microseconds=1)


def _ist(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0, us: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, us, tzinfo=LAUNCH_TZ)


# The seeded total (D19 raised it from 15); test_config.py checks the config row.
LIMIT = 150


async def _fill(session: AsyncSession, witness: Member, n: int, start: datetime) -> None:
    """``n`` deeds by ``witness`` (seeded rows, no doer), one second apart."""
    await seed_deeds(session, witness, n, start=start)


async def _expect_limited(coro: Any) -> None:
    with pytest.raises(AppError) as exc:
        await coro
    assert exc.value.code == "monthly_report_limit", exc.value
    assert exc.value.status_code == 429


# ======================================================================= D16: the IST month


@pytest.mark.parametrize(
    ("at", "start", "end"),
    [
        # The last microsecond of October IST and the first of November IST.
        (NOV_1_IST - _US, _ist(2026, 10, 1), _ist(2026, 11, 1)),
        (NOV_1_IST, _ist(2026, 11, 1), _ist(2026, 12, 1)),
        # New Year: 00:00 IST on 1 Jan 2027 is 18:30 UTC on 31 Dec 2026.
        (
            datetime(2026, 12, 31, 18, 29, 59, 999999, tzinfo=UTC),
            _ist(2026, 12, 1),
            _ist(2027, 1, 1),
        ),
        (datetime(2026, 12, 31, 18, 30, tzinfo=UTC), _ist(2027, 1, 1), _ist(2027, 2, 1)),
        # February, leap and not; 31-day months must not skip a month (+32 days trick).
        (_ist(2028, 2, 29, 23, 59, 59), _ist(2028, 2, 1), _ist(2028, 3, 1)),
        (_ist(2027, 1, 31, 12), _ist(2027, 1, 1), _ist(2027, 2, 1)),
        (_ist(2027, 2, 28, 23, 59, 59), _ist(2027, 2, 1), _ist(2027, 3, 1)),
        (_ist(2027, 7, 31, 23, 59, 59), _ist(2027, 7, 1), _ist(2027, 8, 1)),
    ],
)
def test_ist_month_bounds(at: datetime, start: datetime, end: datetime) -> None:
    got_start, got_end = ist_month(at)
    assert (got_start, got_end) == (start, end)
    assert got_start <= at < got_end


async def test_report_at_235959_ist_counts_for_the_month_it_was_submitted_in(
    session: AsyncSession,
) -> None:
    """A submit stamped 23:59:59.999999 IST is October's 150th, even if the row only lands
    after midnight: the check and ``reported_at`` use the same server ``now``. November starts
    fresh, and its first report at exactly 00:00:00 IST belongs to November, not October."""
    witness = await make_member(session)
    await _fill(session, witness, LIMIT - 1, OCT_31_MORNING)
    doer = await make_member(session)
    last_us = NOV_1_IST - _US
    s = await scan(session, witness, doer, at=NOV_1_IST - timedelta(seconds=5))
    fifteenth = await report(session, witness, s.scan_id, at=last_us)
    assert fifteenth.reported_at == last_us
    assert await reported_this_month(session, witness.user_id, last_us) == LIMIT
    assert await reported_this_month(session, witness.user_id, NOV_1_IST) == 0

    # 00:00:00.000000 IST on the 1st is November's first, not October's 151st.
    await scan_and_report(session, witness, await make_member(session), at=NOV_1_IST)
    assert await reported_this_month(session, witness.user_id, last_us) == LIMIT
    assert await reported_this_month(session, witness.user_id, NOV_1_IST) == 1


async def test_scan_refused_at_2359_becomes_a_deed_after_midnight(session: AsyncSession) -> None:
    """A scan made at 149/150, refused at 23:59:55 IST because the 150th slot went elsewhere, is
    still unused, so the same scan becomes November's first report at 00:00:10 IST."""
    witness, doer = await make_member(session), await make_member(session)
    await _fill(session, witness, LIMIT - 1, OCT_31_MORNING)
    early = await scan(session, witness, doer, at=NOV_1_IST - timedelta(seconds=20))
    await scan_and_report(
        session, witness, await make_member(session), at=NOV_1_IST - timedelta(seconds=15)
    )  # the 150th
    await _expect_limited(
        report(session, witness, early.scan_id, at=NOV_1_IST - timedelta(seconds=5))
    )
    out = await report(session, witness, early.scan_id, at=NOV_1_IST + timedelta(seconds=10))
    assert out.status == "verified"
    assert out.reported_at == NOV_1_IST + timedelta(seconds=10)
    # occurred_at stays October (the scan time); the slot used is November's.
    assert out.occurred_at < NOV_1_IST
    assert await reported_this_month(session, witness.user_id, NOV_1_IST) == 1


async def test_occurred_at_in_the_previous_month_does_not_dodge_a_full_month(
    session: AsyncSession,
) -> None:
    """The count is by ``reported_at`` (server time), not ``occurred_at``: a deed that happened
    in October but is submitted in a full November is refused."""
    await publish_limits(session, reporter_monthly_limit=1)
    witness, doer = await make_member(session), await make_member(session)
    october_scan = await scan(session, witness, doer, at=NOV_1_IST - timedelta(minutes=2))
    await scan_and_report(
        session, witness, await make_member(session), at=NOV_1_IST + timedelta(minutes=1)
    )
    await _expect_limited(
        report(
            session,
            witness,
            october_scan.scan_id,
            at=NOV_1_IST + timedelta(minutes=2),
            occurred_at=NOV_1_IST - timedelta(minutes=3),
        )
    )


# ======================================================================= D16: reported_at


async def test_client_cannot_send_reported_at_or_report_day(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await make_member(session), await make_member(session)
    s = await scan(session, witness, doer)
    await session.commit()
    for extra in (
        {"reported_at": "2026-01-01T00:00:00+00:00"},
        {"report_day": "2026-01-01"},
        {"reporter_user_id": str(uuid.uuid4())},
    ):
        response = await client.post(
            "/api/deeds", json=deed_body(s.scan_id, **extra), headers=witness.headers
        )
        assert response.status_code == 422, (extra, response.text)
    assert await count(session, Deed, Deed.reporter_user_id == witness.user_id) == 0

    before = datetime.now(UTC)
    response = await client.post("/api/deeds", json=deed_body(s.scan_id), headers=witness.headers)
    after = datetime.now(UTC)
    assert response.status_code == 201, response.text
    reported_at = datetime.fromisoformat(response.json()["reported_at"])
    assert before <= reported_at <= after

    # And an edit can't move it either.
    deed_id = response.json()["id"]
    edited = await client.patch(
        f"/api/deeds/{deed_id}",
        json={"reported_at": "2026-01-01T00:00:00+00:00"},
        headers=witness.headers,
    )
    assert edited.status_code == 422, edited.text


# ======================================================================= D16: over the API


async def _seed_now(session: AsyncSession, witness: Member, n: int) -> None:
    """``n`` deeds by ``witness`` in the last few seconds (real clock, as the API uses)."""
    step = timedelta(milliseconds=20)
    await seed_deeds(session, witness, n, start=datetime.now(UTC) - (n + 1) * step, step=step)
    await session.commit()


async def _api_scan(
    client: AsyncClient, session: AsyncSession, witness: Member, doer: Member
) -> Any:
    return await client.post(
        "/api/scans",
        json={"token": await token_for(session, doer), "location": location()},
        headers=witness.headers,
    )


async def _remaining(client: AsyncClient, who: Member) -> int:
    response = await client.get("/api/deeds/options", headers=who.headers)
    assert response.status_code == 200, response.text
    value: int = response.json()["reports_remaining_this_month"]
    return value


async def test_api_qr_path_150th_succeeds_151st_refused_with_default_limit(
    client: AsyncClient, session: AsyncSession
) -> None:
    """With the seeded limit (150): the scan pre-check at 149 passes, the 150th deed is 201,
    the pre-check at 150 is 429 and records nothing; options goes 1 -> 0."""
    witness = await make_member(session)
    d15, d16 = await make_member(session), await make_member(session)
    await _seed_now(session, witness, LIMIT - 1)
    assert await _remaining(client, witness) == 1

    scanned = await _api_scan(client, session, witness, d15)
    assert scanned.status_code == 201, scanned.text
    created = await client.post(
        "/api/deeds", json=deed_body(scanned.json()["scan_id"]), headers=witness.headers
    )
    assert created.status_code == 201, created.text
    assert await _remaining(client, witness) == 0

    refused = await _api_scan(client, session, witness, d16)
    assert refused.status_code == 429, refused.text
    assert refused.json()["code"] == "monthly_report_limit"
    assert await count(session, QrScan, QrScan.doer_user_id == d16.user_id) == 0
    assert await count(session, Deed, Deed.reporter_user_id == witness.user_id) == LIMIT


async def test_api_invite_path_150th_succeeds_151st_refused(
    client: AsyncClient, session: AsyncSession, invites_on: None
) -> None:
    witness = await make_member(session)
    await publish_limits(session, invites_per_day=20)
    await _seed_now(session, witness, LIMIT - 1)
    first = await client.post("/api/deeds", json=invite_body(), headers=witness.headers)
    assert first.status_code == 201, first.text
    second = await client.post("/api/deeds", json=invite_body(), headers=witness.headers)
    assert second.status_code == 429, second.text
    assert second.json()["code"] == "monthly_report_limit"
    assert await count(session, Invite, Invite.reporter_user_id == witness.user_id) == 1
    assert await count(session, Deed, Deed.reporter_user_id == witness.user_id) == LIMIT


async def test_api_scan_precheck_passes_then_an_invite_takes_the_last_slot(
    client: AsyncClient, session: AsyncSession, invites_on: None
) -> None:
    """Scan at 149/150 (pre-check passes), an invite becomes the 150th, then the scan's deed is
    refused by the authoritative check and its scan stays unused."""
    witness, doer = await make_member(session), await make_member(session)
    await _seed_now(session, witness, LIMIT - 1)
    scanned = await _api_scan(client, session, witness, doer)
    assert scanned.status_code == 201, scanned.text
    invite = await client.post("/api/deeds", json=invite_body(), headers=witness.headers)
    assert invite.status_code == 201, invite.text

    refused = await client.post(
        "/api/deeds", json=deed_body(scanned.json()["scan_id"]), headers=witness.headers
    )
    assert refused.status_code == 429, refused.text
    assert refused.json()["code"] == "monthly_report_limit"
    row = await session.get(QrScan, uuid.UUID(scanned.json()["scan_id"]), populate_existing=True)
    assert row is not None and row.consumed_at is None


async def test_options_never_negative_and_per_user(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, other = await make_member(session), await make_member(session)
    await _seed_now(session, witness, 3)
    await publish_limits(session, reporter_monthly_limit=2)  # lowered below what's used
    assert await _remaining(client, witness) == 0
    assert await _remaining(client, other) == 2
    assert (await client.get("/api/deeds/options")).status_code == 401


# ======================================================================= D16: bypass attempts


async def test_stockpiled_scans_at_fourteen_yield_one_deed(session: AsyncSession) -> None:
    """Scanning several doers while at 149 (each pre-check passes) doesn't buy extra slots."""
    witness = await make_member(session)
    await _fill(session, witness, LIMIT - 1, OCT_31_MORNING)
    at = OCT_31_MORNING + timedelta(hours=1)
    scans = [await scan(session, witness, await make_member(session), at=at) for _ in range(3)]
    await report(session, witness, scans[0].scan_id, at=at + timedelta(minutes=1))
    for s in scans[1:]:
        await _expect_limited(report(session, witness, s.scan_id, at=at + timedelta(minutes=2)))
    assert await count(session, Deed, Deed.reporter_user_id == witness.user_id) == LIMIT
    unused = await count(
        session,
        QrScan,
        QrScan.witness_user_id == witness.user_id,
        QrScan.consumed_at.is_(None),
    )
    assert unused == 2


@pytest.mark.parametrize(
    "status",
    [
        DeedStatus.UNVERIFIED,
        DeedStatus.REJECTED,
        DeedStatus.REMOVED,
        DeedStatus.WITHDRAWN,
    ],
)
async def test_every_status_still_counts(session: AsyncSession, status: DeedStatus) -> None:
    """Deeds forced into each closed status (pending is invite-only and is covered by the
    invite tests) still fill the month. One of them is a real QR deed."""
    witness = await make_member(session)
    await _fill(session, witness, LIMIT - 1, OCT_31_MORNING)
    await scan_and_report(
        session, witness, await make_member(session), at=OCT_31_MORNING + timedelta(minutes=30)
    )
    await session.execute(
        update(Deed)
        .where(Deed.reporter_user_id == witness.user_id)
        .values(
            status=status,
            counts_for_points=False,
            withdrawn_at=OCT_31_MORNING if status == DeedStatus.WITHDRAWN else None,
        )
    )
    await _expect_limited(
        scan_and_report(
            session, witness, await make_member(session), at=OCT_31_MORNING + timedelta(hours=1)
        )
    )


async def test_edits_do_not_free_a_slot_or_move_reported_at(session: AsyncSession) -> None:
    witness = await make_member(session)
    await _fill(session, witness, LIMIT - 3, OCT_31_MORNING)
    for i in range(3):  # real (verified, so editable) reports
        await scan_and_report(
            session,
            witness,
            await make_member(session),
            at=OCT_31_MORNING + timedelta(minutes=10 + i),
        )
    _, limits = await load_limits(session)
    deeds = list(
        await session.scalars(select(Deed).where(Deed.reporter_user_id == witness.user_id))
    )
    before = {d.id: d.reported_at for d in deeds}
    for d in [d for d in deeds if d.status == DeedStatus.VERIFIED]:
        await _edits.edit_deed(
            session,
            witness.user_id,
            d.id,
            DeedEdit(description="Carried shopping bags up the stairs"),
            limits=limits,
            now=OCT_31_MORNING + timedelta(minutes=30),
        )
    rows = await session.scalars(
        select(Deed)
        .where(Deed.reporter_user_id == witness.user_id)
        .execution_options(populate_existing=True)
    )
    assert {d.id: d.reported_at for d in rows} == before
    await _expect_limited(
        scan_and_report(
            session, witness, await make_member(session), at=OCT_31_MORNING + timedelta(hours=1)
        )
    )


async def test_invite_counts_in_its_creation_month_not_when_accepted(
    session: AsyncSession,
) -> None:
    """An October invite accepted in November used October's slot; accepting it neither uses a
    November slot for the reporter nor any slot for the doer."""
    reporter, doer = await make_member(session), await make_member(session)
    created, token = await create_invite(session, reporter, at=OCT_31_MORNING)
    assert await reported_this_month(session, reporter.user_id, OCT_31_MORNING) == 1
    await accept(session, token, doer, at=NOV_1_IST + timedelta(hours=1))
    deed = await session.get(Deed, created.id, populate_existing=True)
    assert deed is not None and deed.reported_at == OCT_31_MORNING
    assert await reported_this_month(session, reporter.user_id, NOV_1_IST) == 0
    assert await reported_this_month(session, doer.user_id, NOV_1_IST) == 0


async def test_withdrawn_invite_deed_still_counts(session: AsyncSession) -> None:
    await publish_limits(session, reporter_monthly_limit=1)
    reporter = await make_member(session)
    created, _ = await create_invite(session, reporter, at=OCT_31_MORNING)
    await withdraw(session, reporter, created.id, at=OCT_31_MORNING + timedelta(minutes=5))
    await _expect_limited(create_invite(session, reporter, at=OCT_31_MORNING + timedelta(hours=1)))


async def test_limit_is_per_reporter_not_per_doer(session: AsyncSession) -> None:
    """Sixteen different witnesses can each report the same doer in one month (different acts)."""
    await publish_limits(session, reporter_monthly_limit=1)
    doer = await make_member(session)
    witnesses = await members(session, 3)
    for i, w in enumerate(witnesses):
        await scan_and_report(session, w, doer, at=OCT_31_MORNING + timedelta(hours=3 * i))
    assert await count(session, Deed, Deed.doer_user_id == doer.user_id) == 3


# ======================================================================= D16: concurrency


def _meet_across(monkeypatch: pytest.MonkeyPatch, targets: list[object], parties: int) -> None:
    """All ``parties`` callers of ``ensure_under_monthly_limits`` in any of ``targets`` wait for
    each other before it runs (one shared barrier, so the QR and invite paths really race)."""
    barrier = asyncio.Barrier(parties)
    for module in targets:
        real = module.ensure_under_monthly_limits  # type: ignore[attr-defined]

        def make(real_fn: Any) -> Any:
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                async with asyncio.timeout(10):
                    await barrier.wait()
                return await real_fn(*args, **kwargs)

            return wrapper

        monkeypatch.setattr(module, "ensure_under_monthly_limits", make(real))


async def test_qr_and_invite_racing_at_149_allow_one(
    committed_b6: Committed,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    witness, doer = await committed_b6.member(), await committed_b6.member()
    t0 = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)
    async with SessionLocal() as s:
        await seed_deeds(s, witness, LIMIT - 1, start=t0)
        at = t0 + timedelta(hours=1)
        last = await scan(s, witness, doer, at=at)
        await s.commit()
    _meet_across(monkeypatch, [_qr_deeds, _invites], 2)

    async def qr(s: AsyncSession) -> Any:
        return await report(s, witness, last.scan_id, at=at + timedelta(minutes=1))

    async def inv(s: AsyncSession) -> Any:
        return await create_invite(s, witness, at=at + timedelta(minutes=1))

    results = await asyncio.gather(_in_own_session(qr), _in_own_session(inv))
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1, results
    assert errors[0].code == "monthly_report_limit"
    async with SessionLocal() as s:
        total = await s.scalar(
            select(func.count()).select_from(Deed).where(Deed.reporter_user_id == witness.user_id)
        )
    assert total == LIMIT


async def test_three_concurrent_qr_submits_at_148_allow_two(
    committed_b6: Committed,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    witness = await committed_b6.member()
    doers = [await committed_b6.member() for _ in range(3)]
    t0 = datetime(2026, 8, 12, 6, 0, tzinfo=UTC)
    async with SessionLocal() as s:
        await seed_deeds(s, witness, LIMIT - 2, start=t0)
        at = t0 + timedelta(hours=1)
        scans = [await scan(s, witness, d, at=at) for d in doers]
        await s.commit()
    _meet_across(monkeypatch, [_qr_deeds], 3)

    def submit(scan_id: uuid.UUID) -> Any:
        return lambda s: report(s, witness, scan_id, at=at + timedelta(minutes=1))

    results = await asyncio.gather(*(_in_own_session(submit(sc.scan_id)) for sc in scans))
    errors = [r for r in results if isinstance(r, AppError)]
    assert [e.code for e in errors] == ["monthly_report_limit"], results
    async with SessionLocal() as s:
        total = await s.scalar(
            select(func.count()).select_from(Deed).where(Deed.reporter_user_id == witness.user_id)
        )
    assert total == LIMIT


# ======================================================================= D17


async def test_disabled_invite_create_leaves_nothing_and_uses_no_slot(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter = await make_member(session)
    for _ in range(3):
        response = await client.post("/api/deeds", json=invite_body(), headers=reporter.headers)
        assert response.status_code == 403 and response.json()["code"] == "invites_disabled"
    assert await count(session, Deed, Deed.reporter_user_id == reporter.user_id) == 0
    assert await count(session, Invite, Invite.reporter_user_id == reporter.user_id) == 0
    body = (await client.get("/api/deeds/options", headers=reporter.headers)).json()
    assert body["invites_enabled"] is False
    assert body["reports_remaining_this_month"] == LIMIT


async def test_disabled_check_comes_before_the_monthly_limit(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A reporter at the limit sees ``invites_disabled``, not ``monthly_report_limit``."""
    reporter = await make_member(session)
    await publish_limits(session, reporter_monthly_limit=0)
    response = await client.post("/api/deeds", json=invite_body(), headers=reporter.headers)
    assert response.status_code == 403 and response.json()["code"] == "invites_disabled"


@pytest.mark.parametrize("method", ["Invite", "INVITE", "invite_accept", " invite"])
async def test_invite_method_variants_do_not_slip_past(
    client: AsyncClient, session: AsyncSession, method: str
) -> None:
    reporter = await make_member(session)
    response = await client.post(
        "/api/deeds", json=invite_body(method=method), headers=reporter.headers
    )
    assert response.status_code == 422, response.text
    assert await count(session, Deed, Deed.reporter_user_id == reporter.user_id) == 0


async def test_disabled_invite_routes_with_malformed_tokens_get_403(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Short, long and odd tokens all get the same 403 (no shape oracle while off)."""
    doer = await make_member(session)
    for token in ("a", "x" * 200, "%2E%2E", "AAAA-BBBB"):
        preview = await client.get(f"/api/invites/by-token/{token}")
        assert preview.status_code == 403, (token, preview.text)
        accepted = await client.post(f"/api/invites/by-token/{token}/accept", headers=doer.headers)
        assert accepted.status_code == 403, (token, accepted.text)


# ======================================================================= D15


async def test_verified_count_through_withdraw_and_promotion(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Seven witnesses of one act (cap 5). At every step the profile count equals the group's
    ``counted_count`` and the number of counted verified rows."""
    doer = await make_member(session)
    witnesses = await members(session, 7)
    t0 = datetime.now(UTC) - timedelta(hours=1)
    deeds = [
        await scan_and_report(session, w, doer, at=t0 + timedelta(minutes=i), east_m=i * 5)
        for i, w in enumerate(witnesses)
    ]
    await session.commit()
    group_id = (await _group_of(session, deeds[0].id)).id

    async def check(expected: int) -> None:
        group = await assert_group_invariants(session, group_id)
        rows = await count(
            session,
            Deed,
            Deed.doer_user_id == doer.user_id,
            Deed.status == DeedStatus.VERIFIED,
            Deed.counts_for_points.is_(True),
        )
        assert (await _verified_count(client, doer), group.counted_count, rows) == (
            expected,
            expected,
            expected,
        )

    await check(5)
    counted = [d for d in deeds if d.same_act and d.same_act.counts_for_points]
    uncounted = [d for d in deeds if d.same_act and not d.same_act.counts_for_points]
    assert len(counted) == 5 and len(uncounted) == 2

    # Withdraw a counted report: one uncounted is promoted, count stays 5.
    await withdraw(session, witnesses[deeds.index(counted[0])], counted[0].id)
    await session.commit()
    await check(5)
    # Withdraw the remaining reports one by one: 5, then 4, 3...
    still_verified = [
        d
        for d in deeds
        if (row := await session.get(Deed, d.id, populate_existing=True)) is not None
        and row.status == DeedStatus.VERIFIED
    ]
    assert len(still_verified) == 6
    expected = [5, 4, 3, 2, 1, 0]
    for d, exp in zip(still_verified, expected, strict=True):
        await withdraw(session, witnesses[deeds.index(d)], d.id)
        await session.commit()
        await check(exp)
