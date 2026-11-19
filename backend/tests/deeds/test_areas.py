"""``areas`` and ``deeds.area_id`` (docs/M3-DESIGN.md §2.1, D39, migration 0013).

The seed tests compare the table with whatever ``bangalore_areas.geojson`` holds, so they keep
passing when the placeholder file is replaced by the official ward dataset (DATA1). The lookup
tests use their own areas far from Bangalore (around 10°N 10°E), so the seed never interferes.
"""

import importlib.util
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.deeds.models import Area, DeedStatus, VerificationMethod
from tests.conftest import BACKEND_DIR

AREAS_FILE = BACKEND_DIR / "app" / "modules" / "deeds" / "data" / "bangalore_areas.geojson"
MIGRATION = BACKEND_DIR / "alembic" / "versions" / "2026_11_19_0000-0013_deeds_v5.py"


def _square(lng: float, lat: float, half: float) -> str:
    """A square MultiPolygon (EWKT) centred on ``lng, lat``."""
    x0, x1, y0, y1 = lng - half, lng + half, lat - half, lat + half
    ring = f"{x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0}"
    return f"SRID=4326;MULTIPOLYGON((({ring})))"


def _area(area_id: int, lng: float, lat: float, half: float = 0.01, **overrides: Any) -> Area:
    values: dict[str, Any] = {
        "id": area_id,
        "name": f"Test Area {area_id}",
        "city": "Bangalore",
        "boundary": _square(lng, lat, half),
        "source": "test",
        "ward_code": f"TEST-{area_id}",
    }
    values.update(overrides)
    return Area(**values)


# --- seed ---------------------------------------------------------------------------------------


async def test_seed_matches_the_geojson_file(session: AsyncSession) -> None:
    features = json.loads(AREAS_FILE.read_text(encoding="utf-8"))["features"]
    rows = (await session.execute(select(Area).where(Area.id < 30000).order_by(Area.id))).scalars()
    seeded = [(a.id, a.name, a.ward_code, a.source, a.city) for a in rows]
    expected = [
        (
            i,
            f["properties"]["name"],
            f["properties"]["ward_code"],
            f["properties"]["source"],
            "Bangalore",
        )
        for i, f in enumerate(features, start=1)
    ]
    assert seeded == expected
    invalid = await session.scalar(
        text("SELECT count(*) FROM areas WHERE NOT ST_IsValid(boundary::geometry)")
    )
    assert invalid == 0


async def test_area_names_follow_the_clean_up_rule(session: AsyncSession) -> None:
    """Q28: at most 40 characters, no ward numbers or codes in the display name."""
    for area in (await session.execute(select(Area))).scalars():
        assert 1 <= len(area.name) <= 40
        assert not any(ch.isdigit() for ch in area.name), area.name


# --- constraints --------------------------------------------------------------------------------


async def test_ward_code_is_unique(session: AsyncSession) -> None:
    session.add(_area(30001, 10, 10, ward_code="TEST-DUP"))
    await session.flush()
    with pytest.raises(IntegrityError, match="uq_areas_ward_code"):
        async with session.begin_nested():
            session.add(_area(30002, 10.5, 10, ward_code="TEST-DUP"))
            await session.flush()


@pytest.mark.parametrize("name", ["", "   ", "A" * 41])
async def test_area_name_length_is_checked(session: AsyncSession, name: str) -> None:
    with pytest.raises(IntegrityError, match="ck_areas_name_length"):
        async with session.begin_nested():
            session.add(_area(30003, 10, 10, name=name))
            await session.flush()


async def test_deed_area_must_exist_and_cannot_be_deleted_while_used(
    session: AsyncSession,
) -> None:
    from tests.deeds.test_db_constraints import _pending_deed, _users

    (reporter,) = await _users(session, 1)
    with pytest.raises(IntegrityError, match="fk_deeds_area_id_areas"):
        async with session.begin_nested():
            session.add(_pending_deed(reporter, area_id=32000))
            await session.flush()
    session.add(_area(30004, 10, 10))
    await session.flush()
    session.add(_pending_deed(reporter, area_id=30004))
    await session.flush()
    with pytest.raises(IntegrityError, match="fk_deeds_area_id_areas"):
        async with session.begin_nested():
            await session.execute(text("DELETE FROM areas WHERE id = 30004"))


# --- backfill (the §2.1 lookup rule) ------------------------------------------------------------


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0013", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sync_conn(migrated_db: None) -> Iterator[Connection]:
    """A sync connection in a transaction that is always rolled back."""
    engine = create_engine(get_settings().database_url)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                yield conn
            finally:
                trans.rollback()
    finally:
        engine.dispose()


def _insert_area(conn: Connection, area_id: int, lng: float, lat: float, half: float) -> None:
    conn.execute(
        text(
            "INSERT INTO areas (id, name, city, boundary, source, ward_code) "
            "VALUES (:id, 'Test', 'Bangalore', CAST(:b AS geography), 'test', :code)"
        ),
        {"id": area_id, "b": _square(lng, lat, half), "code": f"TEST-{area_id}"},
    )


def _insert_deed(conn: Connection, reporter: uuid.UUID, point: str | None) -> uuid.UUID:
    deed_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO deeds (id, reporter_user_id, category, description, location, "
            "occurred_at, report_day, status, verification_method, limits_version) VALUES "
            "(:id, :reporter, 'animals', 'Fed a stray dog', CAST(:loc AS geography), :at, "
            ":day, :status, :method, 1)"
        ),
        {
            "id": deed_id,
            "reporter": reporter,
            "loc": point,
            "at": datetime.now(UTC),
            "day": date(2026, 11, 19),
            "status": DeedStatus.PENDING.value,
            "method": VerificationMethod.INVITE_ACCEPT.value,
        },
    )
    return deed_id


def test_backfill_applies_the_lookup_rule(sync_conn: Connection) -> None:
    reporter = uuid.uuid4()
    sync_conn.execute(
        text(
            "INSERT INTO users (id, display_name, handle, city, role, status) "
            "VALUES (:id, 'Area Test', :h, 'Bangalore', 'member', 'active')"
        ),
        {"id": reporter, "h": f"ar{reporter.hex[:12]}"},
    )
    # Two overlapping squares (ids 30010 < 30011) and one 0.5° away.
    _insert_area(sync_conn, 30011, 10.00, 10.00, 0.01)
    _insert_area(sync_conn, 30010, 10.01, 10.00, 0.01)
    _insert_area(sync_conn, 30012, 10.50, 10.00, 0.01)
    inside_one = _insert_deed(sync_conn, reporter, "SRID=4326;POINT(9.995 10)")
    in_overlap = _insert_deed(sync_conn, reporter, "SRID=4326;POINT(10.005 10)")
    # About 1.1 km east of area 30012's edge (10.51°E): within 1500 m, so the nearest area.
    near = _insert_deed(sync_conn, reporter, "SRID=4326;POINT(10.52 10)")
    # About 2.2 km from area 30012: too far, stays null ("Bangalore").
    far = _insert_deed(sync_conn, reporter, "SRID=4326;POINT(10.53 10)")
    no_location = _insert_deed(sync_conn, reporter, None)

    m = _load_migration()
    with Operations.context(MigrationContext.configure(sync_conn)):
        m.backfill_area_ids()
        m.backfill_area_ids()  # safe to re-run

    def area_of(deed_id: uuid.UUID) -> int | None:
        value = sync_conn.execute(
            text("SELECT area_id FROM deeds WHERE id = :id"), {"id": deed_id}
        ).scalar_one()
        return None if value is None else int(value)

    assert area_of(inside_one) == 30011
    assert area_of(in_overlap) == 30010  # a shared point always gets the lowest id
    assert area_of(near) == 30012
    assert area_of(far) is None
    assert area_of(no_location) is None
    assert m.AREA_FALLBACK_M == 1500


def test_seed_loader_rejects_a_bad_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    m = _load_migration()
    good = json.loads(AREAS_FILE.read_text(encoding="utf-8"))
    rows = m._load_areas()
    assert [r["id"] for r in rows] == list(range(1, len(good["features"]) + 1))

    def write(features: list[dict[str, Any]]) -> None:
        path = tmp_path / "areas.geojson"
        path.write_text(
            json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
        )
        monkeypatch.setattr(m, "AREAS_FILE", path)

    feature = good["features"][0]
    for bad in (
        [],
        [{**feature, "properties": {**feature["properties"], "name": "X" * 41}}],
        [{**feature, "properties": {**feature["properties"], "ward_code": ""}}],
        [{**feature, "properties": {**feature["properties"], "source": ""}}],
        [{**feature, "geometry": {"type": "Point", "coordinates": [77.6, 12.9]}}],
    ):
        write(bad)
        with pytest.raises(ValueError):
            m._load_areas()


# --- deeds v5 config round trip ------------------------------------------------------------------


def _latest_deeds(conn: Connection) -> tuple[int, dict[str, Any]]:
    row = conn.execute(
        text(
            "SELECT version, values FROM config_versions WHERE namespace = 'deeds' "
            "ORDER BY version DESC LIMIT 1"
        )
    ).one()
    return int(row[0]), dict(row[1])


def test_deeds_v5_rollback_and_republish_append(sync_conn: Connection) -> None:
    """Downgrade appends a version (never deletes); re-running either step is a no-op; a value
    published by hand in between survives the re-publish."""
    from app.modules.deeds._limits import DeedLimits

    m = _load_migration()
    version, v5 = _latest_deeds(sync_conn)
    DeedLimits.model_validate(v5)
    with Operations.context(MigrationContext.configure(sync_conn)):
        m.rollback_v5()
        m.rollback_v5()
    down_version, down = _latest_deeds(sync_conn)
    assert down_version == version + 1
    assert down == {
        **{k: v for k, v in v5.items() if k != "area_fallback_m"},
        "photos_enabled": False,
    }

    sync_conn.execute(
        text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "VALUES ('deeds', :v, CAST(:values AS jsonb), 'by hand', NULL)"
        ),
        {"v": down_version + 1, "values": json.dumps({**down, "same_act_cap": 7})},
    )
    with Operations.context(MigrationContext.configure(sync_conn)):
        m.publish_v5()
        m.publish_v5()
    up_version, up = _latest_deeds(sync_conn)
    assert up_version == down_version + 2
    assert up == {**v5, "same_act_cap": 7}
    assert DeedLimits.model_validate(up).same_act_cap == 7
    total = sync_conn.execute(
        text("SELECT count(*) FROM config_versions WHERE namespace = 'deeds'")
    ).scalar_one()
    assert total == up_version
