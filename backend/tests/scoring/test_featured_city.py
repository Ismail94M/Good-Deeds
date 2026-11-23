"""Featured City Top Performers (PO 2026-11-23, M4-DESIGN D72, migration 0023): the top members
of a featured city by registered city, ranked by the board rules over the month's counted deeds;
points shown only when the city's config says so. Uses the seeded config (Area only, City
locked), so this also checks the section works while City boards are off."""

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import User, UserStatus
from app.modules.scoring._limits import load_leaderboard
from tests.deeds.helpers import Member, members
from tests.scoring.board_helpers import city, counted, ist, publish_board, ward

URL = "/api/leaderboards/featured?month=2026-09"


async def _set_city(session: AsyncSession, member: Member, name: str) -> None:
    await session.execute(update(User).where(User.id == member.user_id).values(city=name))


async def _deeds(
    session: AsyncSession, doer: Member, reporters: list[Member], region: int, first_day: int
) -> None:
    for i, reporter in enumerate(reporters):
        await counted(
            session, doer, reporter, region_id=region, verified_at=ist(2026, 9, first_day + i)
        )


async def test_top_five_of_bengaluru_by_registered_city_with_board_ranking(
    client: AsyncClient, session: AsyncSession
) -> None:
    people = await members(session, 10)
    doers, (r1, r2, r3) = people[:7], people[7:]
    kora = await ward(session, "FC1")
    bangalore = await city(session)
    # deeds: d0 4, d1 3, d2 2 (in no ward: still counts), d3 2, d4 1, d5 1, d6 5 but in Mumbai.
    await _deeds(session, doers[0], [r1, r2, r3, r1], kora, 1)
    await _deeds(session, doers[1], [r1, r2, r3], kora, 1)
    await _deeds(session, doers[2], [r1, r2], bangalore, 1)
    await _deeds(session, doers[3], [r1, r1], kora, 10)
    await _deeds(session, doers[4], [r1], kora, 1)
    await _deeds(session, doers[5], [r2], kora, 2)
    await _deeds(session, doers[6], [r1, r2, r3, r1, r2], kora, 1)
    await _set_city(session, doers[1], " bengaluru ")  # trimmed, any case
    await _set_city(session, doers[6], "Mumbai")
    await session.commit()

    res = await client.get(URL, headers=r1.headers)
    assert res.status_code == 200
    body = res.json()
    assert body["city"] == {"key": "bengaluru", "name": "Bengaluru"}
    assert body["cities"] == [{"key": "bengaluru", "name": "Bengaluru"}]
    assert body["month"] == "2026-09"
    items = body["items"]
    handles = [i["user"]["handle"] for i in items]
    assert handles[:4] == [d.handle for d in doers[:4]]
    # d4 and d5 share 5th; a shared place is ordered by id, so either one makes the top 5.
    assert handles[4] in {doers[4].handle, doers[5].handle}
    assert doers[6].handle not in handles  # registered in Mumbai
    # D67: d2 and d3 tie on score; d2 has 2 reporters to d3's 1, so d2 is above.
    assert [(i["rank"], i["shared"]) for i in items] == [
        (1, False),
        (2, False),
        (3, False),
        (4, False),
        (5, True),  # shared with the one cut at 5
    ]
    assert [i["deed_count"] for i in items] == [4, 3, 2, 2, 1]
    assert [i["points"] for i in items] == [40, 30, 20, 20, 10]
    assert set(items[0]) == {"rank", "shared", "user", "tier", "deed_count", "points"}
    assert items[0]["tier"]["slug"]


async def test_inactive_members_are_left_out(client: AsyncClient, session: AsyncSession) -> None:
    a, b, r = await members(session, 3)
    kora = await ward(session, "FC2")
    await _deeds(session, a, [r, r], kora, 1)
    await _deeds(session, b, [r], kora, 1)
    await session.execute(
        update(User).where(User.id == a.user_id).values(status=UserStatus.SUSPENDED)
    )
    await session.commit()
    items = (await client.get(URL, headers=r.headers)).json()["items"]
    assert [(i["user"]["handle"], i["rank"]) for i in items] == [(b.handle, 1)]


async def test_points_hidden_by_config_and_unknown_or_disabled_cities_are_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, r = await members(session, 2)
    await _deeds(session, a, [r], await ward(session, "FC3"), 1)
    _, config = await load_leaderboard(session)
    (bengaluru,) = config.featured_cities
    await publish_board(
        session, featured_cities=(bengaluru.model_copy(update={"show_points": False}),)
    )
    await session.commit()
    items = (await client.get(f"{URL}&city=bengaluru", headers=r.headers)).json()["items"]
    assert items[0]["points"] is None and items[0]["deed_count"] == 1

    res = await client.get(f"{URL}&city=mumbai", headers=r.headers)
    assert (res.status_code, res.json()["code"]) == (404, "board_not_found")
    await publish_board(session, featured_cities=(bengaluru.model_copy(update={"enabled": False}),))
    await session.commit()
    res = await client.get(URL, headers=r.headers)
    assert (res.status_code, res.json()["code"]) == (404, "board_not_found")


async def test_an_empty_month_and_errors(client: AsyncClient, session: AsyncSession) -> None:
    (user,) = await members(session, 1)
    await session.commit()
    res = await client.get(URL, headers=user.headers)
    assert res.status_code == 200 and res.json()["items"] == []
    bad = await client.get("/api/leaderboards/featured?month=2026-13", headers=user.headers)
    assert (bad.status_code, bad.json()["code"]) == (400, "invalid_month")
    assert (await client.get(URL)).status_code == 401
