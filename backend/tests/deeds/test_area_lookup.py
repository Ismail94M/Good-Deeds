"""Area lookup at deed create / accept and the ``deeds.service`` additions (docs/M3-DESIGN.md
§2.1, §3.1, D39; test plan §8 "Area name").

The seeded areas are moved far away inside each test (rolled back afterwards), and test areas
are laid out around the service-area centre, so these tests don't depend on the ward dataset:

    A (30101): square of 1 km centred on CENTRE      B (30100): square of 1 km, 1 km east
    |-------- A --------|-------- B --------|   ... 1.2 km ...  near   ... 0.5 km ... far
                       500 m east (shared border)
"""

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import _areas
from app.modules.deeds import service as deeds_service
from app.modules.deeds.models import Area, SelfLog
from tests.deeds.helpers import (
    CENTRE,
    add_ward_regions,
    members,
    offset,
    publish_limits,
    report,
    scan,
    scan_and_report,
)
from tests.deeds.invite_helpers import accept, create_invite

A, B = 30101, 30100


def _square_m(east_m: float, half_m: float) -> str:
    """A square MultiPolygon (EWKT) centred ``east_m`` east of CENTRE, ``half_m`` each way."""
    (s, w), (n, e) = offset(-half_m, east_m - half_m), offset(half_m, east_m + half_m)
    ring = f"{w!r} {s!r}, {e!r} {s!r}, {e!r} {n!r}, {w!r} {n!r}, {w!r} {s!r}"
    return f"SRID=4326;MULTIPOLYGON((({ring})))"


@pytest.fixture
async def test_areas(session: AsyncSession) -> AsyncIterator[None]:
    # Seeded areas go to the far south Pacific for this test only (rolled back with it).
    await session.execute(
        text(
            "UPDATE areas SET boundary = CAST(:b AS geography) WHERE id < 30000",
        ),
        {
            "b": "SRID=4326;MULTIPOLYGON(((-150 -60, -149.99 -60, -149.99 -59.99, -150 -59.99, "
            "-150 -60)))"
        },
    )
    await add_ward_regions(session, "SECRET-WARD-A", "SECRET-WARD-B")
    session.add_all(
        [
            Area(
                id=A,
                name="Test Alpha",
                city="Bangalore",
                boundary=_square_m(0, 500),
                source="test",
                ward_code="SECRET-WARD-A",
            ),
            Area(
                id=B,
                name="Test Beta",
                city="Bangalore",
                boundary=_square_m(1000, 500),
                source="test",
                ward_code="SECRET-WARD-B",
            ),
        ]
    )
    await session.commit()
    yield


async def _area_id(session: AsyncSession, deed_id: uuid.UUID) -> int | None:
    value = await session.scalar(text("SELECT area_id FROM deeds WHERE id = :id"), {"id": deed_id})
    return None if value is None else int(value)


# --------------------------------------------------- the rule


@pytest.mark.parametrize(
    ("east_m", "expected_id", "expected_name"),
    [
        (0, A, "Test Alpha"),  # inside A
        (1000, B, "Test Beta"),  # inside B
        (500, B, "Test Beta"),  # on the shared border: the lowest id (B = 30100)
        (2700, B, "Test Beta"),  # 1.2 km outside B: within 1500 m, the nearest
        (3200, None, "Bangalore"),  # 1.7 km from every area
    ],
)
@pytest.mark.usefixtures("test_areas")
async def test_qr_deed_gets_the_area_of_its_scan(
    session: AsyncSession, east_m: float, expected_id: int | None, expected_name: str
) -> None:
    witness, doer = await members(session, 2)
    s = await scan(session, witness, doer, east_m=east_m)
    assert s.area_name == expected_name
    deed = await report(session, witness, s.scan_id)
    assert deed.area_name == expected_name
    assert await _area_id(session, deed.id) == expected_id
    view = await deeds_service.get_deed(session, deed.id)
    assert view is not None and deeds_service.area_name(view) == expected_name
    assert view.area_name == (None if expected_id is None else expected_name)


@pytest.mark.usefixtures("test_areas")
async def test_border_point_always_gets_the_same_area(session: AsyncSession) -> None:
    for _ in range(3):
        witness, doer = await members(session, 2)
        deed = await scan_and_report(session, witness, doer, east_m=500)
        assert await _area_id(session, deed.id) == B


@pytest.mark.usefixtures("test_areas")
async def test_fallback_distance_follows_config(session: AsyncSession) -> None:
    await publish_limits(session, area_fallback_m=2000)
    witness, doer = await members(session, 2)
    deed = await scan_and_report(session, witness, doer, east_m=3200)
    assert deed.area_name == "Test Beta"
    await publish_limits(session, area_fallback_m=100)
    witness, doer = await members(session, 2)
    deed = await scan_and_report(session, witness, doer, east_m=2700)
    assert deed.area_name == "Bangalore"
    assert await _area_id(session, deed.id) is None


async def test_resolve_without_a_location_is_null(session: AsyncSession) -> None:
    assert await _areas.resolve_area_id(session, None, 1500) is None


# --------------------------------------------------- invites (area set at acceptance)


@pytest.mark.usefixtures("test_areas", "invites_on")
async def test_invite_deed_gets_its_area_when_accepted(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    out, token = await create_invite(session, reporter, north_m=0, east_m=1000)
    assert out.area_name == "Bangalore"  # pending: not resolved yet
    assert await _area_id(session, out.id) is None
    accepted = await accept(session, token, doer)
    assert accepted.area_name == "Test Beta"
    assert await _area_id(session, out.id) == B


@pytest.mark.usefixtures("test_areas", "invites_on")
async def test_invite_without_a_location_shows_bangalore(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    out, token = await create_invite(session, reporter)
    accepted = await accept(session, token, doer)
    assert accepted.area_name == "Bangalore"
    assert await _area_id(session, out.id) is None


# --------------------------------------------------- a failing lookup


@pytest.mark.usefixtures("test_areas")
async def test_failed_lookup_still_creates_the_deed_and_logs_no_coordinates(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        _areas, "_LOOKUP", "SELECT no_such_function(CAST(:loc AS text), :fallback_m)"
    )
    # Alembic's logging config (run by the migrated_db fixture) disables loggers that existed
    # before it; the app never runs migrations in-process, so re-enable this one for the test.
    monkeypatch.setattr(_areas.logger, "disabled", False)
    witness, doer = await members(session, 2)
    with caplog.at_level(logging.WARNING, logger=_areas.__name__):
        s = await scan(session, witness, doer)
        deed = await report(session, witness, s.scan_id)
    assert s.area_name == "Bangalore"
    assert deed.area_name == "Bangalore"
    assert await _area_id(session, deed.id) is None
    assert caplog.records, "the failure is logged"
    lat, lng = CENTRE
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "Area lookup failed" in logged
    for coordinate in (lat, lng, f"{lat:.2f}", f"{lng:.2f}", "POINT"):
        assert str(coordinate) not in logged


# --------------------------------------------------- the API


@pytest.mark.usefixtures("test_areas")
async def test_api_responses_show_the_area_name_never_the_ward_code(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await members(session, 2)
    deed = await scan_and_report(session, witness, doer, east_m=0)
    await session.commit()
    for path, who in (
        (f"/api/deeds/{deed.id}", witness),
        (f"/api/deeds/{deed.id}", doer),
        ("/api/me/deeds?role=reporter", witness),
        ("/api/me/deeds?role=doer", doer),
    ):
        response = await client.get(path, headers=who.headers)
        assert response.status_code == 200, response.text
        assert "Test Alpha" in response.text
        assert "SECRET-WARD" not in response.text
        body: Any = response.json()
        item = body["items"][0] if "items" in body else body
        assert item["area_name"] == "Test Alpha"


# --------------------------------------------------- service additions


@pytest.mark.usefixtures("test_areas")
async def test_get_deeds_returns_views_by_id_with_area(session: AsyncSession) -> None:
    w1, w2, doer = await members(session, 3)
    d1 = await scan_and_report(session, w1, doer, east_m=0)
    d2 = await scan_and_report(
        session, w2, doer, east_m=3200, at=datetime.now(UTC) + timedelta(hours=1)
    )
    views = await deeds_service.get_deeds(session, [d1.id, d2.id, uuid.uuid4()])
    assert set(views) == {d1.id, d2.id}
    assert views[d1.id].area_name == "Test Alpha"
    assert views[d2.id].area_name is None
    assert deeds_service.area_name(views[d2.id]) == "Bangalore"
    assert views[d1.id].doer_id == doer.user_id
    assert await deeds_service.get_deeds(session, []) == {}
    listed = await deeds_service.deeds_for_doer(session, doer.user_id)
    assert {v.area_name for v in listed} == {"Test Alpha", None}


async def test_self_log_owner(session: AsyncSession) -> None:
    (owner,) = await members(session, 1)
    log = SelfLog(
        user_id=owner.user_id,
        category="animals",
        description="Fed the street cats",
        occurred_at=datetime.now(UTC),
    )
    session.add(log)
    await session.flush()
    assert await deeds_service.self_log_owner(session, log.id) == owner.user_id
    assert await deeds_service.self_log_owner(session, uuid.uuid4()) is None
