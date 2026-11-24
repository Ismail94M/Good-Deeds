"""#12 ``GET /api/scoring/users/{handle}/ranks`` (D76, PO 2026-11-24): a person's rank this month
at each level, read from the boards' current snapshots so it always matches the leaderboard.
Signed-in only; disabled levels (State, Country) come back ``enabled: false``; a person with no
counted deed has no rank; a person on several Area boards shows their best one."""

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import User, UserStatus
from app.modules.scoring import _leaderboard
from tests.deeds.helpers import Member, members
from tests.scoring.board_helpers import CITY, counted, ward


async def _ranks(client: AsyncClient, viewer: Member, handle: str) -> dict[str, dict[str, Any]]:
    res = await client.get(f"/api/scoring/users/{handle}/ranks", headers=viewer.headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [lvl["level"] for lvl in body["levels"]] == ["ward", "city", "state", "country"]
    assert body["final"] is False
    return {lvl["level"]: lvl for lvl in body["levels"]}


async def _board(client: AsyncClient, viewer: Member, code: str) -> dict[str, tuple[int, bool]]:
    res = await client.get(f"/api/leaderboards?region={code}", headers=viewer.headers)
    assert res.status_code == 200, res.text
    return {i["user"]["handle"]: (i["rank"], i["shared"]) for i in res.json()["items"]}


async def _refresh(session: AsyncSession, now: datetime) -> None:
    await _leaderboard.refresh_due(session, now=now + timedelta(hours=1))
    await session.commit()


async def test_ranks_match_the_area_and_city_boards_and_state_and_country_are_closed(
    client: AsyncClient, session: AsyncSession
) -> None:
    """a: 1 deed in UR1 (behind b's 2) and 3 in UR2 (alone): best Area board is UR2 at #1. On the
    City board a's 4 deeds beat b's 2. Every number equals the board's own row."""
    a, b, r1, r2 = await members(session, 4)
    now = datetime.now(UTC)
    ur1, ur2 = await ward(session, "UR1"), await ward(session, "UR2")
    when = now - timedelta(minutes=5)
    await counted(session, a, r1, region_id=ur1, verified_at=when)
    for reporter in (r1, r2):
        await counted(session, b, reporter, region_id=ur1, verified_at=when)
    for reporter in (r1, r2, r1):
        await counted(session, a, reporter, region_id=ur2, verified_at=when)
    await _refresh(session, now)

    ranks = await _ranks(client, b, a.handle)
    assert ranks["ward"]["enabled"] and ranks["city"]["enabled"]
    assert (ranks["ward"]["region"]["code"], ranks["ward"]["rank"]) == (f"{CITY}-UR2", 1)
    assert ranks["ward"]["region"]["kind"] == "ward"
    assert (ranks["city"]["region"]["code"], ranks["city"]["rank"]) == (CITY, 1)
    for closed in ("state", "country"):
        assert ranks[closed] == {
            "level": closed,
            "enabled": False,
            "region": None,
            "rank": None,
            "shared": False,
        }
    assert (await _board(client, b, f"{CITY}-UR2"))[a.handle] == (1, False)
    assert (await _board(client, b, CITY))[a.handle] == (1, False)

    b_ranks = await _ranks(client, a, b.handle)
    assert (b_ranks["ward"]["region"]["code"], b_ranks["ward"]["rank"]) == (f"{CITY}-UR1", 1)
    assert b_ranks["city"]["rank"] == 2
    assert (await _board(client, a, CITY))[b.handle] == (2, False)


async def test_a_shared_place_is_marked_shared_like_the_board(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, b, r = await members(session, 3)
    now = datetime.now(UTC)
    ur3 = await ward(session, "UR3")
    for doer in (a, b):
        await counted(session, doer, r, region_id=ur3, verified_at=now - timedelta(minutes=5))
    await _refresh(session, now)

    ranks = await _ranks(client, r, a.handle)
    assert (ranks["ward"]["rank"], ranks["ward"]["shared"]) == (1, True)
    assert (await _board(client, r, f"{CITY}-UR3"))[a.handle] == (1, True)


async def test_no_counted_deed_this_month_means_no_rank(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, viewer = await members(session, 2)
    ranks = await _ranks(client, viewer, a.handle)
    for level in ("ward", "city"):
        assert ranks[level]["enabled"] is True
        assert (ranks[level]["rank"], ranks[level]["region"]) == (None, None)


async def test_a_deed_from_an_earlier_month_gives_no_rank_this_month(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, r = await members(session, 2)
    now = datetime.now(UTC)
    ur4 = await ward(session, "UR4")
    await counted(session, a, r, region_id=ur4, verified_at=now - timedelta(days=70))
    await _refresh(session, now)
    ranks = await _ranks(client, r, a.handle)
    assert ranks["ward"]["rank"] is None and ranks["city"]["rank"] is None


async def test_signed_in_only_and_unknown_or_inactive_people_are_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, viewer = await members(session, 2)
    res = await client.get(f"/api/scoring/users/{a.handle}/ranks")
    assert res.status_code == 401

    res = await client.get("/api/scoring/users/nobody-here/ranks", headers=viewer.headers)
    assert (res.status_code, res.json()["code"]) == (404, "user_not_found")

    user = await session.get(User, a.user_id)
    assert user is not None
    user.status = UserStatus.SUSPENDED
    await session.commit()
    res = await client.get(f"/api/scoring/users/{a.handle}/ranks", headers=viewer.headers)
    assert (res.status_code, res.json()["code"]) == (404, "user_not_found")
