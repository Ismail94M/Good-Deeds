"""M4a security review fixes (SEC3, 2026-11-22): M1 finalization barrier, L1 month range, L5
photo evidence time, L6 inactive accounts on final boards. L3 is in ``test_cap_order.py``
(re-read after the locks); L4 (withdraw clock read after the locks) has no timing test."""

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit
from app.modules.deeds.events import DeedWithdrawn
from app.modules.identity.models import User, UserStatus
from app.modules.media.events import DeedPhotoReady
from app.modules.scoring._finalize import Outcome, finalize_month
from app.modules.scoring.models import LedgerRole
from tests.deeds.helpers import members, scan_and_report
from tests.scoring.board_helpers import city, counted, ist, month, ward
from tests.scoring.helpers import points, run_scoring

SEP = month(2026, 9)
AFTER = ist(2026, 10, 2, 1, 1)  # just after September's cutoff


# --------------------------------------------------- M1: the finalization barrier


async def test_an_old_unprocessed_event_does_not_block_a_later_month(
    session: AsyncSession,
) -> None:
    """An event from before the previous month (a pre-M4 event, or one whose job gave up long
    ago) can't touch September's ledger rows, so it doesn't hold September back."""
    a, r = await members(session, 2)
    deed_id = await counted(
        session, a, r, region_id=await city(session), verified_at=ist(2026, 9, 3)
    )
    old = emit(
        session,
        DeedWithdrawn(
            deed_id=deed_id,
            reporter_id=r.user_id,
            doer_id=a.user_id,
            previous_status="verified",
            same_act_group_id=None,
        ),
    )
    old.occurred_at = ist(2026, 7, 31, 23, 59)  # before 1 August, the previous month's start
    await session.flush()
    assert (await finalize_month(session, SEP, now=AFTER)).outcome == Outcome.FINALIZED


async def test_an_event_dispatched_before_scoring_existed_does_not_block(
    session: AsyncSession,
) -> None:
    a, r = await members(session, 2)
    deed_id = await counted(
        session, a, r, region_id=await city(session), verified_at=ist(2026, 9, 3)
    )
    row = emit(
        session,
        DeedWithdrawn(
            deed_id=deed_id,
            reporter_id=r.user_id,
            doer_id=a.user_id,
            previous_status="verified",
            same_act_group_id=None,
        ),
    )
    row.occurred_at = ist(2026, 9, 10)
    row.dispatched_at = ist(2026, 9, 10)
    row.dispatched_handlers = ["app.modules.feed.handlers.some_feed_handler"]
    await session.flush()
    assert (await finalize_month(session, SEP, now=AFTER)).outcome == Outcome.FINALIZED


# --------------------------------------------------- L1: month and year range


async def test_months_far_out_of_range_are_invalid_not_server_errors(
    client: AsyncClient, session: AsyncSession
) -> None:
    (user,) = await members(session, 1)
    await session.commit()
    for value in ("9999-12", "2101-01", "1999-12"):
        res = await client.get(f"/api/leaderboards?month={value}", headers=user.headers)
        assert res.status_code == 400, value
        assert res.json()["code"] == "invalid_month"
    res = await client.get("/api/leaderboards?month=2100-12", headers=user.headers)
    assert res.status_code == 200
    res = await client.get("/api/leaderboards/heroes?year=9999", headers=user.headers)
    assert res.status_code == 422
    res = await client.get("/api/leaderboards/heroes?year=2100", headers=user.headers)
    assert res.status_code == 200


# --------------------------------------------------- L5: photo evidence time


async def test_photo_bonus_uses_when_the_photo_was_ready_not_when_it_was_handled(
    session: AsyncSession,
) -> None:
    """Ready 1 hour after verification, but the worker only handles the event after the 24 h
    window (a backlog): the +2 still counts."""
    reporter, doer = await members(session, 2)
    out = await scan_and_report(session, reporter, doer, at=datetime.now(UTC) - timedelta(hours=30))
    await run_scoring(session, out.id)
    assert out.verified_at is not None
    emit(
        session,
        DeedPhotoReady(
            photo_id=uuid.uuid4(),
            deed_id=out.id,
            uploader_id=reporter.user_id,
            attached_at=out.reported_at + timedelta(minutes=30),
            ready_at=out.verified_at + timedelta(hours=1),
        ),
    )
    await session.flush()
    await run_scoring(session, out.id)  # handled now: 30 h after verification
    assert await points(session, out.id, LedgerRole.DOER) == 12


# --------------------------------------------------- L6: inactive accounts on final boards


async def _suspend(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(User).where(User.id == user_id).values(status=UserStatus.SUSPENDED)
    )
    await session.commit()


async def test_an_account_suspended_after_finalization_keeps_its_place_without_its_name(
    client: AsyncClient, session: AsyncSession
) -> None:
    """PO QA2: the final result never changes. SEC3 L6: the name and handle are hidden."""
    a, b, r1, r2 = await members(session, 4)
    kora = await ward(session, "AP1")
    for day, reporter in ((3, r1), (4, r2), (5, r1)):
        await counted(session, a, reporter, region_id=kora, verified_at=ist(2026, 9, day))
    await counted(session, b, r1, region_id=kora, verified_at=ist(2026, 9, 6))
    assert (await finalize_month(session, SEP, now=AFTER)).outcome == Outcome.FINALIZED
    await session.commit()
    await _suspend(session, a.user_id)

    board = (
        await client.get("/api/leaderboards?region=IN-KA-BLR-AP1&month=2026-09", headers=r1.headers)
    ).json()
    assert board["final"] is True
    first = board["items"][0]
    assert first["rank"] == 1
    assert first["user"] == {"handle": None, "display_name": "Unavailable account"}
    assert board["items"][1]["user"]["handle"] is not None  # b is still shown by name
    hero = board["titles"][0]
    assert hero["place"] == 1
    assert hero["users"] == [{"handle": None, "display_name": "Unavailable account"}]

    heroes = (
        await client.get(
            "/api/leaderboards/heroes?region=IN-KA-BLR-AP1&year=2026", headers=r1.headers
        )
    ).json()
    users = [u for item in heroes["items"] for t in item["titles"] for u in t["users"]]
    assert {"handle": None, "display_name": "Unavailable account"} in users
    assert all(u["handle"] != a.handle for u in users)
