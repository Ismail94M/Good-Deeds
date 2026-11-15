"""D16: at most ``reporter_monthly_limit`` (15) reports per reporter per IST calendar month.

Every ``deeds`` row the reporter created in the month counts, across doers and both paths and
whatever its status now. A failed submission creates no row and doesn't count. The locked check
runs on deed create before the scan is consumed; ``POST /api/scans`` pre-checks the same count.
The concurrency test is in ``test_concurrency.py``.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.deeds.models import Deed, QrScan
from tests.deeds.helpers import (
    Member,
    count,
    deed_body,
    location,
    make_member,
    publish_limits,
    report,
    scan,
    scan_and_report,
    token_for,
)
from tests.deeds.invite_helpers import create_invite, withdraw

# 2 October 2026, 11:30 IST.
OCT_2 = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
# The IST month boundary: 00:00 IST on 1 November is 18:30 UTC on 31 October.
NOV_1_IST = datetime(2026, 10, 31, 18, 30, tzinfo=UTC)
_TEN = timedelta(minutes=10)


async def _fill(session: AsyncSession, witness: Member, n: int, start: datetime) -> None:
    """``n`` QR reports by ``witness``, each about a new doer, 10 minutes apart."""
    for i in range(n):
        await scan_and_report(session, witness, await make_member(session), at=start + i * _TEN)


async def _report_new_doer(session: AsyncSession, witness: Member, at: datetime) -> None:
    await scan_and_report(session, witness, await make_member(session), at=at)


async def _assert_limited(session: AsyncSession, witness: Member, at: datetime) -> None:
    with pytest.raises(AppError) as exc:
        await _report_new_doer(session, witness, at)
    assert exc.value.code == "monthly_report_limit"
    assert exc.value.status_code == 429


async def test_fifteenth_report_succeeds_and_sixteenth_fails(session: AsyncSession) -> None:
    witness = await make_member(session)
    await _fill(session, witness, 14, OCT_2)
    await _report_new_doer(session, witness, OCT_2 + 14 * _TEN)  # the 15th
    assert await count(session, Deed, Deed.reporter_user_id == witness.user_id) == 15
    await _assert_limited(session, witness, OCT_2 + 15 * _TEN)
    assert await count(session, Deed, Deed.reporter_user_id == witness.user_id) == 15


async def test_refused_report_leaves_its_scan_unused(session: AsyncSession) -> None:
    """The check runs before the scan is consumed, so a refused 16th doesn't burn the scan."""
    witness, doer = await make_member(session), await make_member(session)
    await _fill(session, witness, 14, OCT_2)
    at = OCT_2 + 14 * _TEN
    early = await scan(session, witness, doer, at=at)  # scanned at 14/15
    await _report_new_doer(session, witness, at + timedelta(minutes=1))  # the 15th
    with pytest.raises(AppError) as exc:
        await report(session, witness, early.scan_id, at=at + timedelta(minutes=2))
    assert exc.value.code == "monthly_report_limit"
    row = await session.get(QrScan, early.scan_id, populate_existing=True)
    assert row is not None and row.consumed_at is None


async def test_reports_to_different_doers_and_both_paths_share_the_limit(
    session: AsyncSession,
) -> None:
    witness = await make_member(session)
    # 5 invite deeds on 5 IST days (5 invites a day is a separate limit), then 10 QR reports.
    for d in range(5):
        await create_invite(session, witness, at=OCT_2 + timedelta(days=d))
    await _fill(session, witness, 10, OCT_2 + timedelta(days=6))
    await _assert_limited(session, witness, OCT_2 + timedelta(days=7))
    # The invite path is refused the same way.
    with pytest.raises(AppError) as exc:
        await create_invite(session, witness, at=OCT_2 + timedelta(days=8))
    assert exc.value.code == "monthly_report_limit"


async def test_withdrawn_deeds_still_count(session: AsyncSession) -> None:
    witness = await make_member(session)
    await _fill(session, witness, 15, OCT_2)
    deeds = list(
        await session.scalars(
            select(Deed.id).where(Deed.reporter_user_id == witness.user_id).limit(3)
        )
    )
    for deed_id in deeds:
        await withdraw(session, witness, deed_id, at=OCT_2 + timedelta(hours=4))
    await _assert_limited(session, witness, OCT_2 + timedelta(hours=5))


async def test_failed_submission_does_not_count(session: AsyncSession) -> None:
    witness, doer = await make_member(session), await make_member(session)
    await _fill(session, witness, 14, OCT_2)
    at = OCT_2 + 14 * _TEN
    stale = await scan(session, witness, doer, at=at)
    # Submitted after the scan expired (15 minutes): refused, and no deed row is created.
    with pytest.raises(AppError) as exc:
        await report(session, witness, stale.scan_id, at=at + timedelta(minutes=16))
    assert exc.value.code == "scan_expired"
    assert await count(session, Deed, Deed.reporter_user_id == witness.user_id) == 14
    await _report_new_doer(session, witness, at + timedelta(minutes=20))  # still the 15th


async def test_limit_resets_at_midnight_ist_on_the_first(session: AsyncSession) -> None:
    """The month boundary is 00:00 IST, not 00:00 UTC (5 h 30 min earlier)."""
    witness = await make_member(session)
    await _fill(session, witness, 15, OCT_2)
    # 23:59 IST on 31 October: still October, refused.
    await _assert_limited(session, witness, NOV_1_IST - timedelta(minutes=1))
    # 00:00 IST on 1 November (still 31 October in UTC): a new month.
    await _report_new_doer(session, witness, NOV_1_IST)


async def test_reports_just_after_midnight_ist_count_for_the_new_month(
    session: AsyncSession,
) -> None:
    """Reports from 00:00 IST on the 1st (still the previous UTC month) fill November."""
    witness = await make_member(session)
    await _report_new_doer(session, witness, NOV_1_IST - timedelta(minutes=1))  # October
    await _fill(session, witness, 15, NOV_1_IST)  # 00:00-02:20 IST on 1 November
    await _assert_limited(session, witness, NOV_1_IST + timedelta(hours=5))


async def test_limit_comes_from_config(session: AsyncSession) -> None:
    witness = await make_member(session)
    await publish_limits(session, reporter_monthly_limit=2)
    await _fill(session, witness, 2, OCT_2)
    await _assert_limited(session, witness, OCT_2 + timedelta(hours=1))
    await publish_limits(session, reporter_monthly_limit=3)
    await _report_new_doer(session, witness, OCT_2 + timedelta(hours=2))
    await publish_limits(session, reporter_monthly_limit=0)
    fresh = await make_member(session)
    await _assert_limited(session, fresh, OCT_2)


async def test_self_logs_do_not_count(client: AsyncClient, session: AsyncSession) -> None:
    witness = await make_member(session)
    await publish_limits(session, reporter_monthly_limit=1)
    response = await client.post(
        "/api/me/self-logs",
        json={
            "category": "animals",
            "description": "Fed the street dogs near the park",
            "occurred_at": datetime.now(UTC).isoformat(),
        },
        headers=witness.headers,
    )
    assert response.status_code == 201, response.text
    await _report_new_doer(session, witness, datetime.now(UTC))


# --------------------------------------------------- over the API (real clock)


async def test_api_refuses_the_sixteenth_with_429(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await make_member(session), await make_member(session)
    now = datetime.now(UTC)
    for i in range(14):
        await _report_new_doer(session, witness, now - timedelta(seconds=30 - i))
    early = await scan(session, witness, doer)  # at 14/15
    await _report_new_doer(session, witness, datetime.now(UTC))  # the 15th
    await session.commit()

    response = await client.post(
        "/api/deeds", json=deed_body(early.scan_id), headers=witness.headers
    )
    assert response.status_code == 429, response.text
    assert response.json()["code"] == "monthly_report_limit"
    row = await session.get(QrScan, early.scan_id, populate_existing=True)
    assert row is not None and row.consumed_at is None


async def test_scan_at_the_limit_is_refused_early(
    client: AsyncClient, session: AsyncSession
) -> None:
    """``POST /api/scans`` tells the witness before a scan is recorded (a recorded scan would
    use up the pair's IST day without ever becoming a deed)."""
    witness, doer = await make_member(session), await make_member(session)
    await publish_limits(session, reporter_monthly_limit=1)
    await _report_new_doer(session, witness, datetime.now(UTC))
    await session.commit()

    response = await client.post(
        "/api/scans",
        json={"token": await token_for(session, doer), "location": location()},
        headers=witness.headers,
    )
    assert response.status_code == 429, response.text
    assert response.json()["code"] == "monthly_report_limit"
    assert await count(session, QrScan, QrScan.doer_user_id == doer.user_id) == 0
