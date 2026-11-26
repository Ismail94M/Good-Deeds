"""Scoring API (docs/M4-DESIGN.md §3.3 #1 to #10, §3.4; §8 "API and privacy"): no points or rank
scores in public shapes, points to their owner only, #4 public with a per-IP limit, admin-only
#9 and #10 with the conflict rule."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import publish
from app.modules.identity.models import Role, User
from app.modules.scoring import _leaderboard
from app.modules.scoring._finalize import cutoff_for, finalize_month
from app.modules.scoring._limits import load_scoring
from tests.deeds.helpers import Member, members, scan_and_report
from tests.scoring.board_helpers import city, counted, ist, month, publish_board, ward
from tests.scoring.helpers import run_scoring

# The level hierarchy is tested with City on (it is off in the seeded config, migration 0022).
pytestmark = pytest.mark.usefixtures("city_board_on")

PRIVATE_KEYS = {"points", "doer_points", "reporter_points", "rank_score", "total_points"}


def _keys(value: Any) -> set[str]:
    """Every key anywhere in a JSON value."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


async def _role(session: AsyncSession, member: Member, role: Role) -> None:
    await session.execute(update(User).where(User.id == member.user_id).values(role=role))
    await session.commit()


async def _final_september(session: AsyncSession) -> tuple[Member, Member, Member, Member, int]:
    """a: 3 ward deeds from 2 reporters (ward and city Hero); b: 1 city deed (Top 3)."""
    a, b, r1, r2 = await members(session, 4)
    kora = await ward(session, "AP1")
    bangalore = await city(session)
    for day, reporter in ((3, r1), (4, r2), (5, r1)):
        await counted(session, a, reporter, region_id=kora, verified_at=ist(2026, 9, day))
    await counted(session, b, r1, region_id=bangalore, verified_at=ist(2026, 9, 6))
    await finalize_month(session, month(2026, 9), now=ist(2026, 10, 3))
    await session.commit()
    return a, b, r1, r2, kora


# --------------------------------------------------- #1 options


async def test_options_come_from_config(client: AsyncClient, session: AsyncSession) -> None:
    (user,) = await members(session, 1)
    res = await client.get("/api/scoring/options", headers=user.headers)
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {
        "tiers",
        "badges",
        "levels",
        "withdraw_window_hours",
        "doer_daily_cap",
        "reporter_daily_cap",
        "reason_labels",
    }
    assert body["tiers"][1] == {"slug": "sprout", "label": "Sprout", "min_points": 50}
    assert body["levels"][:2] == [
        {
            "kind": "ward",
            "enabled": True,
            "hero_min_deeds": 3,
            "hero_min_reporters": 2,
            "titled_places": [1],
        },
        {
            "kind": "city",
            "enabled": True,
            "hero_min_deeds": 0,
            "hero_min_reporters": 0,
            "titled_places": [1, 2, 3],
        },
    ]
    assert (body["withdraw_window_hours"], body["doer_daily_cap"]) == (24, 10)
    assert body["badges"][1]["description"] == "Recognised for 5 verified deeds"
    assert body["reason_labels"]["withdrawn"] == "Report withdrawn"
    assert (await client.get("/api/scoring/options")).status_code == 401


# --------------------------------------------------- #4 public, #6 and #8 boards


async def test_public_scoring_is_public_and_has_no_points(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, *_ = await _final_september(session)
    res = await client.get(f"/api/scoring/users/{a.handle}")  # no login (§3.4 #4)
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"tier", "badges_total", "titles_total", "badges", "titles"}
    assert not _keys(body) & PRIVATE_KEYS
    assert body["titles_total"] == 2
    labels = sorted(t["label"] for t in body["titles"])
    assert all("September 2026" in label for label in labels)
    assert {t["period_month"] for t in body["titles"]} == {"2026-09"}
    # D75: each title says which level's board it is from, matching its wording.
    for t in body["titles"]:
        assert t["level"] == ("city" if "Bangalore" in t["label"] else "ward"), t
    assert {t["level"] for t in body["titles"]} == {"ward", "city"}
    assert (await client.get("/api/scoring/users/nobody-here")).status_code == 404


async def test_public_scoring_has_a_per_ip_limit(
    client: AsyncClient, session: AsyncSession
) -> None:
    (user,) = await members(session, 1)
    _, config = await load_scoring(session)
    await publish(
        session, config.model_copy(update={"rate_public_ip": "1/60"}), actor_id=None, reason="test"
    )
    await session.commit()
    assert (await client.get(f"/api/scoring/users/{user.handle}")).status_code == 200
    res = await client.get(f"/api/scoring/users/{user.handle}")
    assert (res.status_code, res.json()["code"]) == (429, "rate_limited")


async def test_final_leaderboard_rows_titles_and_privacy(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, b, _, _, _ = await _final_september(session)
    res = await client.get("/api/leaderboards?month=2026-09", headers=b.headers)
    assert res.status_code == 200
    body = res.json()
    assert not _keys(body) & PRIVATE_KEYS
    assert (body["region"]["kind"], body["month"], body["final"]) == ("city", "2026-09", True)
    assert body["finalizes_at"] is None  # final
    assert [
        (i["user"]["handle"], i["rank"], i["shared"], i["deed_count"]) for i in body["items"]
    ] == [
        (a.handle, 1, False, 3),
        (b.handle, 2, False, 1),
    ]
    assert set(body["items"][0]) == {"rank", "shared", "user", "tier", "deed_count"}
    assert body["me"]["user"]["handle"] == b.handle
    assert [(t["place"], [u["handle"] for u in t["users"]]) for t in body["titles"]] == [
        (1, [a.handle]),
        (2, [b.handle]),
    ]
    assert body["next_cursor"] is None


async def test_ward_board_and_heroes(client: AsyncClient, session: AsyncSession) -> None:
    a, b, *_ = await _final_september(session)
    res = await client.get(
        "/api/leaderboards?region=IN-KA-BLR-AP1&month=2026-09", headers=b.headers
    )
    body = res.json()
    assert body["region"] == {"code": "IN-KA-BLR-AP1", "name": "Test AP1", "kind": "ward"}
    assert [i["user"]["handle"] for i in body["items"]] == [a.handle]
    assert body["me"] is None
    assert body["titles"][0]["label"].startswith("Kindness Hero · Test AP1")

    heroes = await client.get(
        "/api/leaderboards/heroes?region=IN-KA-BLR-AP1&year=2026", headers=b.headers
    )
    assert heroes.status_code == 200
    items = heroes.json()["items"]
    assert items[0]["period_month"] == "2026-09"
    assert [u["handle"] for u in items[0]["titles"][0]["users"]] == [a.handle]
    assert not _keys(heroes.json()) & PRIVATE_KEYS

    found = await client.get("/api/leaderboards/regions?kind=ward&q=AP1", headers=b.headers)
    assert found.json() == [{"code": "IN-KA-BLR-AP1", "name": "Test AP1"}]


async def test_leaderboard_errors(client: AsyncClient, session: AsyncSession) -> None:
    (user,) = await members(session, 1)
    h = user.headers
    disabled = await client.get("/api/leaderboards?region=IN-KA", headers=h)  # state: disabled
    assert (disabled.status_code, disabled.json()["code"]) == (404, "board_not_found")
    assert (await client.get("/api/leaderboards?region=NOPE", headers=h)).status_code == 404
    bad = await client.get("/api/leaderboards?month=2026-13", headers=h)
    assert (bad.status_code, bad.json()["code"]) == (400, "invalid_month")
    empty = await client.get("/api/leaderboards?month=2020-01", headers=h)
    assert empty.status_code == 200 and empty.json()["items"] == []
    # Not final yet: the earliest finalization time, from the same cutoff as finalize_month.
    nov = (await client.get("/api/leaderboards?month=2026-11", headers=h)).json()
    assert nov["final"] is False
    assert datetime.fromisoformat(nov["finalizes_at"]) == await cutoff_for(session, month(2026, 11))
    assert datetime.fromisoformat(nov["finalizes_at"]) == ist(2026, 12, 2, 1, 0)
    cursor = await client.get("/api/leaderboards?cursor=junk", headers=h)
    assert cursor.json()["code"] == "invalid_cursor"
    assert (await client.get("/api/leaderboards")).status_code == 401


async def test_leaderboard_pages_with_a_cursor(client: AsyncClient, session: AsyncSession) -> None:
    doers = await members(session, 3)
    (r,) = await members(session, 1)
    bangalore = await city(session)
    for i, doer in enumerate(doers):
        for day in range(i + 1):
            await counted(session, doer, r, region_id=bangalore, verified_at=ist(2026, 9, 1 + day))
    await publish_board(session, page_size=2)
    await finalize_month(session, month(2026, 9), now=ist(2026, 10, 3))
    await session.commit()
    first = (await client.get("/api/leaderboards?month=2026-09", headers=r.headers)).json()
    assert [i["rank"] for i in first["items"]] == [1, 2]
    second = (
        await client.get(
            f"/api/leaderboards?month=2026-09&cursor={first['next_cursor']}", headers=r.headers
        )
    ).json()
    assert [i["rank"] for i in second["items"]] == [3] and second["next_cursor"] is None


# --------------------------------------------------- #2, #3, #5 the owner's points


async def test_my_scoring_ledger_and_deed_points(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, stranger = await members(session, 3)
    out = await scan_and_report(session, reporter, doer)
    await run_scoring(session, out.id)
    await _leaderboard.refresh_due(session, now=datetime.now(UTC) + timedelta(hours=1))
    await session.commit()

    me = (await client.get("/api/scoring/me", headers=doer.headers)).json()
    assert (me["doer_points"], me["reporter_points"], me["total_points"]) == (10, 0, 10)
    assert me["provisional_points"] == 10
    assert me["tier"]["slug"] == "seed"
    kinds = {b["region"]["kind"] for b in me["boards"]}
    assert "city" in kinds
    assert all(set(b["region"]) == {"id", "code", "name", "kind"} for b in me["boards"])
    assert me["boards"][0]["rank"] == 1 and me["boards"][0]["final"] is False
    rep = (await client.get("/api/scoring/me", headers=reporter.headers)).json()
    assert rep["reporter_today"] == {"used": 1, "cap": 10}
    assert rep["total_points"] == 2

    page = (await client.get("/api/scoring/me/ledger", headers=doer.headers)).json()
    (item,) = page["items"]
    assert (item["points"], item["reason"], item["reason_label"]) == (
        10,
        "verified",
        "Deed verified",
    )
    assert item["provisional"] is True and item["provisional_until"] is not None
    assert item["reporter"]["handle"] == reporter.handle
    assert item["doer"]["handle"] == doer.handle
    assert "description" not in item
    bad = await client.get("/api/scoring/me/ledger?cursor=nope", headers=doer.headers)
    assert (bad.status_code, bad.json()["code"]) == (400, "invalid_cursor")
    only_reporter = await client.get("/api/scoring/me/ledger?role=reporter", headers=doer.headers)
    assert only_reporter.json()["items"] == []

    own = (await client.get(f"/api/scoring/deeds/{out.id}", headers=doer.headers)).json()
    assert (own["points"], own["capped"]) == (10, False) and own["provisional_until"]
    assert (own["reason"], own["reason_label"]) == ("verified", "Deed verified")
    theirs = (await client.get(f"/api/scoring/deeds/{out.id}", headers=reporter.headers)).json()
    assert theirs["points"] == 2
    other = await client.get(f"/api/scoring/deeds/{out.id}", headers=stranger.headers)
    assert (other.status_code, other.json()["code"]) == (404, "deed_not_found")


# --------------------------------------------------- #9, #10 admin


async def test_admin_endpoints_are_admin_only_with_the_conflict_rule(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, moderator, admin = await members(session, 4)
    out = await scan_and_report(session, reporter, doer)
    await run_scoring(session, out.id)
    await _role(session, moderator, Role.MODERATOR)
    await _role(session, admin, Role.ADMIN)
    path = f"/api/admin/scoring/deeds/{out.id}"

    assert (await client.get(path, headers=doer.headers)).status_code == 403
    assert (await client.get(path, headers=moderator.headers)).status_code == 403
    res = await client.get(path, headers=admin.headers)
    assert res.status_code == 200
    body = res.json()
    assert {(e["role"], e["points"]) for e in body["ledger"]} == {("doer", 10), ("reporter", 2)}
    assert body["ledger"][0]["breakdown"]["pair_percent"] == 100
    assert body["ledger"][0]["config_version"] >= 1
    assert [e["method"] for e in body["evaluations"]] == ["rule_based"]

    # An admin who reported the deed gets 404 (nobody reviews their own case).
    await _role(session, reporter, Role.ADMIN)
    conflicted = await client.get(path, headers=reporter.headers)
    assert (conflicted.status_code, conflicted.json()["code"]) == (404, "deed_not_found")

    status = await client.get("/api/admin/scoring/months/2026-09", headers=admin.headers)
    assert status.json()["period_month"] == "2026-09"
    assert set(status.json()) == {
        "period_month",
        "cutoff_at",
        "pending_scoring_events",
        "pending_fraud_events",
        "pending_hold_events",
        "finalized_at",
        "final_snapshots",
    }
    assert status.json()["finalized_at"] is None
    assert (
        await client.get("/api/admin/scoring/months/2026-09", headers=moderator.headers)
    ).status_code == 403
    bad = await client.get("/api/admin/scoring/months/sept", headers=admin.headers)
    assert bad.json()["code"] == "invalid_month"
