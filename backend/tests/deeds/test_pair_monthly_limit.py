"""D19: at most ``reporter_doer_monthly_limit`` (15) reports per reporter about one doer per IST
calendar month, plus the D20 remaining counts and the D27 error bodies naming the doer.

Counted like the total (D16 rules): every ``deeds`` row the reporter created about the doer
that month, whatever its status. Checked twice: an unlocked pre-check on ``POST /api/scans``
(after the doer resolved and the self and phone checks passed; no scan is recorded, so the
pair's IST day isn't used up) and the authoritative check under the ``reporter_month`` lock on
``POST /api/deeds``. One report per pair per IST day, so pair rows are seeded on different days.
The concurrency test is in ``test_concurrency.py``.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.deeds import _reads, _scans
from app.modules.deeds._limits import load_limits
from app.modules.deeds._reporter_limit import ensure_under_monthly_limits
from app.modules.deeds.models import Deed, DeedStatus, QrScan
from app.modules.identity.service import LAUNCH_TZ
from tests.deeds.helpers import (
    Member,
    code_for,
    count,
    deed_body,
    location,
    make_member,
    publish_limits,
    report,
    scan,
    scan_and_report,
    seed_deeds,
    token_for,
)
from tests.deeds.invite_helpers import create_invite

PAIR = 15  # the seeded pair limit (D19); test_config.py checks the config row
TOTAL = 150
OCT_1 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)  # 11:30 IST
OCT_20 = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
NOV_1_IST = datetime(2026, 10, 31, 18, 30, tzinfo=UTC)  # 00:00 IST on 1 November
DAY = timedelta(days=1)


async def _seed_pair(
    session: AsyncSession,
    witness: Member,
    doer: Member,
    n: int,
    *,
    status: DeedStatus | None = None,
) -> None:
    """``n`` deeds by ``witness`` about ``doer`` on 1, 2, ... October (one per IST day)."""
    await seed_deeds(session, witness, n, start=OCT_1, step=DAY, doer=doer, status=status)


async def _expect(coro: Any, code: str) -> AppError:
    with pytest.raises(AppError) as exc:
        await coro
    assert exc.value.code == code, exc.value
    assert exc.value.status_code == 429
    return exc.value


async def _scans_of(session: AsyncSession, witness: Member, doer: Member) -> int:
    return await count(
        session,
        QrScan,
        QrScan.witness_user_id == witness.user_id,
        QrScan.doer_user_id == doer.user_id,
    )


# ======================================================================= boundaries (service)


async def test_14th_and_15th_succeed_and_16th_is_refused_at_scan(session: AsyncSession) -> None:
    witness, ahmed = await make_member(session), await make_member(session)
    await _seed_pair(session, witness, ahmed, PAIR - 2)  # 13 on 1-13 October

    s14 = await scan(session, witness, ahmed, at=OCT_20)
    assert s14.pair_reports_remaining == 2
    await report(session, witness, s14.scan_id, at=OCT_20)  # the 14th
    s15 = await scan(session, witness, ahmed, at=OCT_20 + DAY)
    assert s15.pair_reports_remaining == 1  # "this is the last one this month"
    await report(session, witness, s15.scan_id, at=OCT_20 + DAY)  # the 15th
    assert await count(session, Deed, Deed.doer_user_id == ahmed.user_id) == PAIR

    scans_before = await _scans_of(session, witness, ahmed)
    error = await _expect(scan(session, witness, ahmed, at=OCT_20 + 2 * DAY), "pair_monthly_limit")
    # The pre-check records no scan, so the pair's IST day is not used up.
    assert await _scans_of(session, witness, ahmed) == scans_before
    assert error.extra == {
        "doer": {"handle": ahmed.handle, "display_name": ahmed.display_name, "avatar": None}
    }


async def test_16th_is_refused_on_deed_create_and_leaves_its_scan_unused(
    session: AsyncSession,
) -> None:
    """Two live scans of one doer can straddle IST midnight (one per IST day). Both pass the
    pre-check at 14; whichever is sent second is refused by the locked check."""
    witness, ahmed = await make_member(session), await make_member(session)
    await _seed_pair(session, witness, ahmed, PAIR - 1)
    late = datetime(2026, 10, 20, 18, 25, tzinfo=UTC)  # 23:55 IST, 20 October
    early = datetime(2026, 10, 20, 18, 31, tzinfo=UTC)  # 00:01 IST, 21 October
    first = await scan(session, witness, ahmed, at=late)
    second = await scan(session, witness, ahmed, at=early)
    assert first.pair_reports_remaining == second.pair_reports_remaining == 1
    sent = early + timedelta(minutes=1)
    await report(session, witness, second.scan_id, at=sent)  # the 15th
    error = await _expect(report(session, witness, first.scan_id, at=sent), "pair_monthly_limit")
    assert error.extra == {}  # on deed create the form already knows the doer
    row = await session.get(QrScan, first.scan_id, populate_existing=True)
    assert row is not None and row.consumed_at is None


async def test_a_full_pair_does_not_affect_other_doers_or_other_reporters(
    session: AsyncSession,
) -> None:
    witness, ahmed, kavya = [await make_member(session) for _ in range(3)]
    other_witness = await make_member(session)
    await _seed_pair(session, witness, ahmed, PAIR)
    await _expect(scan(session, witness, ahmed, at=OCT_20), "pair_monthly_limit")

    # The same reporter can still report Kavya, with her full pair allowance.
    to_kavya = await scan(session, witness, kavya, at=OCT_20)
    assert to_kavya.pair_reports_remaining == PAIR
    await report(session, witness, to_kavya.scan_id, at=OCT_20)
    # Another reporter can still report Ahmed: deeds by other reporters don't count.
    by_other = await scan(session, other_witness, ahmed, at=OCT_20, east_m=50)
    assert by_other.pair_reports_remaining == PAIR
    await report(session, other_witness, by_other.scan_id, at=OCT_20)


@pytest.mark.parametrize(
    "status",
    [DeedStatus.WITHDRAWN, DeedStatus.REJECTED, DeedStatus.UNVERIFIED, DeedStatus.REMOVED],
)
async def test_every_status_counts_towards_the_pair(
    session: AsyncSession, status: DeedStatus
) -> None:
    witness, ahmed = await make_member(session), await make_member(session)
    await _seed_pair(session, witness, ahmed, PAIR, status=status)
    await _expect(scan(session, witness, ahmed, at=OCT_20), "pair_monthly_limit")


async def test_pair_resets_at_midnight_ist_on_the_first(session: AsyncSession) -> None:
    witness, ahmed = await make_member(session), await make_member(session)
    await _seed_pair(session, witness, ahmed, PAIR)
    await _expect(
        scan(session, witness, ahmed, at=NOV_1_IST - timedelta(minutes=1)), "pair_monthly_limit"
    )
    fresh = await scan(session, witness, ahmed, at=NOV_1_IST)  # 00:00 IST, still 31 Oct UTC
    assert fresh.pair_reports_remaining == PAIR
    out = await report(session, witness, fresh.scan_id, at=NOV_1_IST)
    assert out.status == "verified"


async def test_total_is_checked_before_the_pair(session: AsyncSession) -> None:
    """A reporter at both limits gets ``monthly_report_limit``, at scan and at deed create."""
    witness, ahmed = await make_member(session), await make_member(session)
    await _seed_pair(session, witness, ahmed, PAIR)
    await seed_deeds(session, witness, TOTAL - PAIR, start=OCT_1)
    await _expect(scan(session, witness, ahmed, at=OCT_20), "monthly_report_limit")
    _, limits = await load_limits(session)
    await _expect(
        ensure_under_monthly_limits(session, witness.user_id, ahmed.user_id, limits, OCT_20),
        "monthly_report_limit",
    )


async def test_pair_limit_comes_from_config(session: AsyncSession) -> None:
    witness, ahmed = await make_member(session), await make_member(session)
    await publish_limits(session, reporter_doer_monthly_limit=1)
    first = await scan(session, witness, ahmed, at=OCT_20)
    assert first.pair_reports_remaining == 1
    await report(session, witness, first.scan_id, at=OCT_20)
    await _expect(scan(session, witness, ahmed, at=OCT_20 + DAY), "pair_monthly_limit")
    await publish_limits(session, reporter_doer_monthly_limit=2)
    again = await scan(session, witness, ahmed, at=OCT_20 + 2 * DAY)
    assert again.pair_reports_remaining == 1


async def test_invite_create_checks_only_the_total(session: AsyncSession, invites_on: None) -> None:
    """The doer of an invite is unknown until acceptance (D21), so a full pair doesn't block
    creating one."""
    witness, ahmed = await make_member(session), await make_member(session)
    await _seed_pair(session, witness, ahmed, PAIR)
    out, _ = await create_invite(session, witness, at=OCT_20)
    assert out.status == "pending"


# ======================================================================= remaining counts (D20)


async def test_pair_remaining_is_counted_before_this_report(session: AsyncSession) -> None:
    witness, ahmed = await make_member(session), await make_member(session)
    await _seed_pair(session, witness, ahmed, 12)
    s = await scan(session, witness, ahmed, at=OCT_20)
    assert s.pair_reports_remaining == 3
    assert s.scanned_at == OCT_20
    assert s.month_resets_at == datetime(2026, 11, 1, tzinfo=LAUNCH_TZ)
    assert s.month_resets_at.utcoffset() == timedelta(hours=5, minutes=30)
    row = await session.get(QrScan, s.scan_id)
    assert row is not None and row.scanned_at == s.scanned_at

    _, limits = await load_limits(session)
    await report(session, witness, s.scan_id, at=OCT_20 + timedelta(minutes=1))
    # Recomputed at read time: the deed just sent now counts.
    detail = await _scans.get_scan(
        session, witness.user_id, s.scan_id, limits=limits, now=OCT_20 + timedelta(minutes=2)
    )
    assert detail.consumed is True
    assert detail.pair_reports_remaining == 2
    assert detail.scanned_at == OCT_20


@pytest.mark.parametrize(
    ("now", "resets"),
    [
        # 23:59 IST on the last day of October, and 00:00 IST on 1 November.
        (NOV_1_IST - timedelta(minutes=1), datetime(2026, 11, 1, tzinfo=LAUNCH_TZ)),
        (NOV_1_IST, datetime(2026, 12, 1, tzinfo=LAUNCH_TZ)),
        # 23:59 IST on 31 December resets on 1 January.
        (datetime(2026, 12, 31, 18, 29, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=LAUNCH_TZ)),
    ],
)
async def test_month_resets_at_is_midnight_ist_on_the_next_first(
    session: AsyncSession, now: datetime, resets: datetime
) -> None:
    me = await make_member(session)
    _, limits = await load_limits(session)
    options = await _reads.options(session, me.user_id, limits=limits, now=now)
    assert options.month_resets_at == resets
    assert options.month_resets_at.isoformat().endswith("+05:30")


# ======================================================================= over the API (real clock)


async def _at_pair_limit_now(session: AsyncSession, witness: Member, doer: Member) -> None:
    """``PAIR`` deeds about ``doer`` reported in the last few seconds. The real clock may leave
    fewer than 15 days in this month, so their ``report_day``s are unrelated past days (the
    limit counts by ``reported_at``)."""
    step = timedelta(milliseconds=50)
    await seed_deeds(
        session,
        witness,
        PAIR,
        start=datetime.now(UTC) - (PAIR + 1) * step,
        step=step,
        doer=doer,
        unrelated_days=True,
    )
    await session.commit()


async def _post_scan(client: AsyncClient, witness: Member, body: dict[str, Any]) -> Any:
    return await client.post("/api/scans", json=body, headers=witness.headers)


async def _qr_body(session: AsyncSession, doer: Member) -> dict[str, Any]:
    return {"token": await token_for(session, doer), "location": location()}


async def test_api_scan_at_pair_limit_names_the_doer_and_records_nothing(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, ahmed = await make_member(session), await make_member(session)
    await _at_pair_limit_now(session, witness, ahmed)
    response = await _post_scan(client, witness, await _qr_body(session, ahmed))
    assert response.status_code == 429, response.text
    body = response.json()
    assert set(body) == {"detail", "code", "doer"}
    assert body["code"] == "pair_monthly_limit"
    assert body["doer"] == {
        "handle": ahmed.handle,
        "display_name": ahmed.display_name,
        "avatar": None,
    }
    assert ahmed.display_name not in body["detail"] and ahmed.handle not in body["detail"]
    assert await _scans_of(session, witness, ahmed) == 0

    # Typed ID + code gets the same answer.
    manual = await _post_scan(
        client,
        witness,
        {
            "short_id": ahmed.short_id,
            "code": await code_for(session, ahmed),
            "location": location(),
        },
    )
    assert manual.status_code == 429 and manual.json() == body


async def test_api_already_recognized_today_names_the_doer(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, ahmed = await make_member(session), await make_member(session)
    # A minute ago: an earlier token window (so not ``already_scanned``), the same IST day.
    await scan_and_report(session, witness, ahmed, at=datetime.now(UTC) - timedelta(seconds=61))
    await session.commit()
    response = await _post_scan(client, witness, await _qr_body(session, ahmed))
    assert response.status_code == 409, response.text
    body = response.json()
    assert set(body) == {"detail", "code", "doer"}
    assert body["code"] == "already_recognized_today"
    assert body["doer"] == {
        "handle": ahmed.handle,
        "display_name": ahmed.display_name,
        "avatar": None,
    }


async def test_api_errors_before_the_pair_check_never_name_the_doer(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A witness at the pair limit with Ahmed: a wrong code, an unknown ID, a stale token, or a
    self-scan never returns ``pair_monthly_limit`` or a ``doer`` (no lookup of who owns an ID)."""
    witness, ahmed = await make_member(session), await make_member(session)
    await _at_pair_limit_now(session, witness, ahmed)
    right = await code_for(session, ahmed)
    wrong = f"{(int(right) + 1) % 1_000_000:06d}"
    stale = await token_for(session, ahmed, at=datetime.now(UTC) - timedelta(minutes=10))
    attempts = [
        {"short_id": ahmed.short_id, "code": wrong, "location": location()},
        {"short_id": "GD-ZZZZZZZ", "code": right, "location": location()},
        {"token": stale, "location": location()},
        {"token": await token_for(session, witness), "location": location()},
    ]
    for body in attempts:
        response = await _post_scan(client, witness, body)
        assert response.status_code in (404, 409), response.text
        assert set(response.json()) == {"detail", "code"}, response.text
        assert response.json()["code"] != "pair_monthly_limit"


async def test_api_deed_create_at_pair_limit_is_429_without_doer(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, ahmed = await make_member(session), await make_member(session)
    await seed_deeds(
        session,
        witness,
        PAIR - 1,
        start=datetime.now(UTC) - timedelta(seconds=5),
        step=timedelta(milliseconds=50),
        doer=ahmed,
        unrelated_days=True,
    )
    s = await scan(session, witness, ahmed)  # at 14/15: passes
    assert s.pair_reports_remaining == 1
    # The 15th lands before this scan's deed is sent (another day's report, seeded).
    await seed_deeds(
        session,
        witness,
        1,
        start=datetime.now(UTC) - timedelta(seconds=1),
        doer=ahmed,
        unrelated_days=True,
        day_offset=PAIR,
        status=DeedStatus.WITHDRAWN,
    )
    await session.commit()
    response = await client.post("/api/deeds", json=deed_body(s.scan_id), headers=witness.headers)
    assert response.status_code == 429, response.text
    assert response.json()["code"] == "pair_monthly_limit"
    assert set(response.json()) == {"detail", "code"}
    row = await session.get(QrScan, s.scan_id, populate_existing=True)
    assert row is not None and row.consumed_at is None


async def test_api_scan_and_get_scan_return_the_d20_fields(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, ahmed = await make_member(session), await make_member(session)
    step = timedelta(milliseconds=50)
    await seed_deeds(
        session,
        witness,
        12,
        start=datetime.now(UTC) - 13 * step,
        step=step,
        doer=ahmed,
        unrelated_days=True,
    )
    await session.commit()
    created = await _post_scan(client, witness, await _qr_body(session, ahmed))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["pair_reports_remaining"] == 3
    resets = datetime.fromisoformat(body["month_resets_at"])
    assert body["month_resets_at"].endswith("+05:30")
    local = resets.astimezone(LAUNCH_TZ)
    assert (local.day, local.hour, local.minute) == (1, 0, 0)
    assert datetime.now(UTC) < resets <= datetime.now(UTC) + timedelta(days=31)
    row = await session.get(QrScan, body["scan_id"])
    assert row is not None
    assert datetime.fromisoformat(body["scanned_at"]) == row.scanned_at

    sent = await client.post("/api/deeds", json=deed_body(body["scan_id"]), headers=witness.headers)
    assert sent.status_code == 201, sent.text
    detail = await client.get(f"/api/scans/{body['scan_id']}", headers=witness.headers)
    assert detail.status_code == 200, detail.text
    got = detail.json()
    assert got["consumed"] is True
    assert got["pair_reports_remaining"] == 2  # recomputed after the deed was sent
    assert got["scanned_at"] == body["scanned_at"]
    assert got["month_resets_at"] == body["month_resets_at"]
    assert set(got) == set(body) | {"consumed"}
