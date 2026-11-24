"""Open and locked leaderboard levels. Migration 0022 (D71) opened only the ward ("Area") level;
migration 0025 (D76, PO 2026-11-24) opens City beside it, so the seeded config has Area and City
open and State and Country listed as disabled, shown locked ("Coming soon"). A disabled level
gets no boards or titles, its reads are 404, and "my boards" leaves it out; the tests for that
close City again with one config version (``_area_only``)."""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scoring import _leaderboard
from app.modules.scoring._finalize import finalize_month
from app.modules.scoring._limits import load_leaderboard
from app.modules.scoring.models import HeroTitle, LeaderboardSnapshot, TitleLevel
from tests.deeds.helpers import Member, members
from tests.scoring.board_helpers import CITY, city, counted, ist, month, publish_board, ward


async def _area_only(session: AsyncSession) -> None:
    """Close City again (the D71 config), as one new config version."""
    _, current = await load_leaderboard(session)
    await publish_board(
        session,
        levels=tuple(
            lvl.model_copy(update={"enabled": lvl.kind == "ward"}) for lvl in current.levels
        ),
    )


async def _september(session: AsyncSession) -> tuple[Member, Member, int, int]:
    """a: 3 ward deeds from 2 reporters (enough for the Area Hero); b: 1 deed with no ward."""
    a, b, r1, r2 = await members(session, 4)
    kora = await ward(session, "AO1")
    bangalore = await city(session)
    for day, reporter in ((3, r1), (4, r2), (5, r1)):
        await counted(session, a, reporter, region_id=kora, verified_at=ist(2026, 9, day))
    await counted(session, b, r1, region_id=bangalore, verified_at=ist(2026, 9, 6))
    return a, b, kora, bangalore


async def test_area_and_city_are_open_and_state_and_country_are_listed_locked(
    client: AsyncClient, session: AsyncSession
) -> None:
    (user,) = await members(session, 1)
    res = await client.get("/api/scoring/options", headers=user.headers)
    assert res.status_code == 200
    levels = {lvl["kind"]: lvl["enabled"] for lvl in res.json()["levels"]}
    assert levels == {"ward": True, "city": True, "state": False, "country": False}


async def test_the_open_city_ranks_everyone_in_its_wards_and_awards_its_titles(
    client: AsyncClient, session: AsyncSession
) -> None:
    """D76: the City board counts deeds in every ward plus deeds with no ward, with the board
    rules, and month end awards City Kindness Hero (#1) and Top 3 (#2, #3) from the config."""
    a, b, kora, bangalore = await _september(session)
    await finalize_month(session, month(2026, 9), now=ist(2026, 10, 3))
    await session.commit()

    board = await client.get(f"/api/leaderboards?region={CITY}&month=2026-09", headers=b.headers)
    assert board.status_code == 200
    assert [(i["rank"], i["user"]["handle"]) for i in board.json()["items"]] == [
        (1, a.handle),
        (2, b.handle),
    ]
    titles = {
        (t.level_kind, t.region_id, t.place, t.user_id)
        for t in await session.scalars(select(HeroTitle))
    }
    assert titles == {
        (TitleLevel.WARD, kora, 1, a.user_id),
        (TitleLevel.CITY, bangalore, 1, a.user_id),
        (TitleLevel.CITY, bangalore, 2, b.user_id),
    }


async def test_a_disabled_city_gets_no_board_and_no_titles(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _area_only(session)
    a, b, kora, bangalore = await _september(session)
    await finalize_month(session, month(2026, 9), now=ist(2026, 10, 3))
    await session.commit()

    titles = list(await session.scalars(select(HeroTitle)))
    assert [(t.level_kind, t.region_id, t.user_id) for t in titles] == [
        (TitleLevel.WARD, kora, a.user_id)
    ]
    city_snapshots = await session.scalars(
        select(LeaderboardSnapshot).where(LeaderboardSnapshot.region_id == bangalore)
    )
    assert list(city_snapshots) == []

    area = await client.get(f"/api/leaderboards?region={CITY}-AO1&month=2026-09", headers=b.headers)
    assert area.status_code == 200
    assert [i["user"]["handle"] for i in area.json()["items"]] == [a.handle]
    for url in (
        f"/api/leaderboards?region={CITY}&month=2026-09",
        "/api/leaderboards?month=2026-09",  # the old default board was the city
        f"/api/leaderboards/heroes?region={CITY}&year=2026",
    ):
        res = await client.get(url, headers=b.headers)
        assert (res.status_code, res.json()["code"]) == (404, "board_not_found"), url


async def test_my_boards_leave_out_a_level_that_was_closed(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A city board computed while City was open (an old snapshot) is not offered once it is
    closed again; reopening it is one config version and brings it back."""
    _, with_city = await load_leaderboard(session)
    a, r = await members(session, 2)
    now = datetime.now(UTC)
    kora = await ward(session, "AO2")
    await counted(session, a, r, region_id=kora, verified_at=now - timedelta(minutes=5))
    await _leaderboard.refresh_due(session, now=now + timedelta(hours=1))
    await _area_only(session)
    await session.commit()

    me = await client.get("/api/scoring/me", headers=a.headers)
    assert me.status_code == 200
    assert {b["region"]["kind"] for b in me.json()["boards"]} == {"ward"}

    await publish_board(session, levels=with_city.levels)
    await session.commit()
    me = await client.get("/api/scoring/me", headers=a.headers)
    assert {b["region"]["kind"] for b in me.json()["boards"]} == {"ward", "city"}
