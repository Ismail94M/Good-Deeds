"""The location hierarchy (docs/M4-DESIGN.md §2.2, D65, migration 0019) and the region CLI.

Seed: India → Karnataka → Bangalore and one ward region per ``areas`` row; closure rows written
by the database; parent kinds, fixed codes and area regions enforced by triggers.
"""

import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.config import get_settings
from app.modules.deeds import cli
from app.modules.deeds.models import Area, Region, RegionClosure, RegionKind
from tests.conftest import BACKEND_DIR

VERSIONS = BACKEND_DIR / "alembic" / "versions"
M19 = VERSIONS / "2026_11_22_0000-0019_deeds_v7_regions.py"
M20 = VERSIONS / "2026_11_22_0100-0020_scoring.py"
SEED_COUNT = 369


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _region(session: AsyncSession, code: str) -> Region:
    region = await session.scalar(select(Region).where(Region.code == code))
    assert region is not None
    return region


async def _add(session: AsyncSession, kind: str, code: str, parent_id: int | None) -> None:
    await session.execute(
        text("INSERT INTO regions (kind, parent_id, name, code) VALUES (:k, :p, 'Test', :c)"),
        {"k": kind, "p": parent_id, "c": code},
    )


# --- seed ---------------------------------------------------------------------------------------


async def test_seeded_hierarchy(session: AsyncSession) -> None:
    india, ka, blr = [await _region(session, c) for c in ("IN", "IN-KA", "IN-KA-BLR")]
    assert (india.kind, india.name, india.parent_id) == (RegionKind.COUNTRY, "India", None)
    assert (ka.kind, ka.name, ka.parent_id) == (RegionKind.STATE, "Karnataka", india.id)
    assert (blr.kind, blr.name, blr.parent_id) == (RegionKind.CITY, "Bangalore", ka.id)
    counts: dict[str, int] = dict(
        (
            await session.execute(
                text("SELECT kind, count(*) FROM regions WHERE code LIKE 'IN%' GROUP BY kind")
            )
        ).all()
    )
    assert counts == {"country": 1, "state": 1, "city": 1, "ward": SEED_COUNT}


async def test_every_area_has_its_own_ward_region(session: AsyncSession) -> None:
    rows = (
        await session.execute(
            select(Area.id, Area.name, Area.ward_code, Region.kind, Region.name, Region.code)
            .join(Region, Region.id == Area.region_id)
            .where(Area.id < 30000)
            .order_by(Area.id)
        )
    ).all()
    assert len(rows) == SEED_COUNT
    for _, area_name, ward_code, kind, region_name, code in rows:
        assert kind == RegionKind.WARD
        assert region_name == area_name
        assert code == f"IN-KA-BLR-{ward_code}"
    # Ward regions follow area-id order (seeded in one ordered insert).
    region_ids = list(
        (
            await session.execute(select(Area.region_id).where(Area.id < 30000).order_by(Area.id))
        ).scalars()
    )
    assert region_ids == sorted(region_ids)
    assert len(set(region_ids)) == SEED_COUNT


async def test_every_ward_counts_in_bangalore_karnataka_and_india(session: AsyncSession) -> None:
    """Each seeded ward's ancestors are exactly itself (0), Bangalore (1), Karnataka (2) and
    India (3), so a deed counts once per level (D66)."""
    rows = (
        await session.execute(
            text(
                "SELECT w.code, array_agg(a.code ORDER BY c.depth), array_agg(c.depth "
                "ORDER BY c.depth) FROM regions AS w "
                "JOIN region_closure AS c ON c.descendant_id = w.id "
                "JOIN regions AS a ON a.id = c.ancestor_id "
                "WHERE w.kind = 'ward' AND w.code LIKE 'IN-KA-BLR-GBA-%' GROUP BY w.code"
            )
        )
    ).all()
    assert len(rows) == SEED_COUNT
    for code, ancestors, depths in rows:
        assert ancestors == [code, "IN-KA-BLR", "IN-KA", "IN"]
        assert depths == [0, 1, 2, 3]
    blr = await _region(session, "IN-KA-BLR")
    below = await session.scalar(
        select(RegionClosure.descendant_id)
        .where(RegionClosure.ancestor_id == blr.id, RegionClosure.depth == 1)
        .order_by(RegionClosure.descendant_id)
        .limit(1)
    )
    assert below is not None
    total = await session.scalar(text("SELECT count(*) FROM region_closure"))
    assert total == 1 + 2 + 3 + 4 * SEED_COUNT


# --- triggers and checks ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "parent_code"),
    [("ward", "IN-KA"), ("ward", "IN"), ("city", "IN"), ("city", "IN-KA-BLR"), ("state", "IN-KA")],
)
async def test_parent_kind_is_enforced(session: AsyncSession, kind: str, parent_code: str) -> None:
    parent = await _region(session, parent_code)
    with pytest.raises(IntegrityError, match="region parent kind"):
        async with session.begin_nested():
            await _add(session, kind, "IN-TEST-X", parent.id)
    ward = await session.scalar(select(Region.id).where(Region.kind == RegionKind.WARD).limit(1))
    with pytest.raises(IntegrityError, match="region parent kind"):
        async with session.begin_nested():
            await _add(session, "ward", "IN-TEST-Y", ward)  # nothing goes under a ward


@pytest.mark.parametrize(("kind", "with_parent"), [("country", True), ("state", False)])
async def test_only_a_country_has_no_parent(
    session: AsyncSession, kind: str, with_parent: bool
) -> None:
    india = await _region(session, "IN")
    with pytest.raises(IntegrityError, match="ck_regions_parent_by_kind"):
        async with session.begin_nested():
            await _add(session, kind, "XX", india.id if with_parent else None)


@pytest.mark.parametrize("code", ["in-ka", "IN KA", "IN--KA", "-IN", "IN-", ""])
async def test_code_format(session: AsyncSession, code: str) -> None:
    with pytest.raises(IntegrityError, match="ck_regions_code_format"):
        async with session.begin_nested():
            await _add(session, "country", code, None)


async def test_codes_are_unique(session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="uq_regions_code"):
        async with session.begin_nested():
            await _add(session, "country", "IN", None)


@pytest.mark.parametrize(
    "change", ["kind = 'state'", "parent_id = NULL", "code = 'IN-KA-BLR2'", "parent_id = id"]
)
async def test_kind_parent_and_code_never_change(session: AsyncSession, change: str) -> None:
    blr = await _region(session, "IN-KA-BLR")
    with pytest.raises(IntegrityError, match="cannot change"):
        async with session.begin_nested():
            await session.execute(
                text(f"UPDATE regions SET {change} WHERE id = :id"), {"id": blr.id}
            )


async def test_name_can_change(session: AsyncSession) -> None:
    blr = await _region(session, "IN-KA-BLR")
    await session.execute(
        text("UPDATE regions SET name = 'Bengaluru' WHERE id = :id"), {"id": blr.id}
    )
    assert await session.scalar(
        text("SELECT name FROM regions WHERE id = :id"), {"id": blr.id}
    ) == ("Bengaluru")


async def test_closure_rows_are_never_updated(session: AsyncSession) -> None:
    with pytest.raises(DBAPIError, match="append-only"):
        async with session.begin_nested():
            await session.execute(text("UPDATE region_closure SET depth = depth WHERE depth = 3"))


async def test_a_region_in_use_cannot_be_deleted(session: AsyncSession) -> None:
    blr = await _region(session, "IN-KA-BLR")
    with pytest.raises(IntegrityError, match="fk_regions_parent_id_regions"):
        async with session.begin_nested():
            await session.execute(text("DELETE FROM regions WHERE id = :id"), {"id": blr.id})
    area_region = await session.scalar(select(Area.region_id).limit(1))
    with pytest.raises(IntegrityError, match="fk_areas_region_id_regions"):
        async with session.begin_nested():
            await session.execute(text("DELETE FROM regions WHERE id = :id"), {"id": area_region})


async def test_new_regions_get_their_closure_and_a_leaf_can_be_deleted(
    session: AsyncSession,
) -> None:
    ka = await _region(session, "IN-KA")
    await _add(session, "city", "IN-KA-MYS", ka.id)
    mys = await _region(session, "IN-KA-MYS")
    await _add(session, "ward", "IN-KA-MYS-W1", mys.id)
    ward = await _region(session, "IN-KA-MYS-W1")
    closure = (
        await session.execute(
            text(
                "SELECT a.code, c.depth FROM region_closure AS c JOIN regions AS a "
                "ON a.id = c.ancestor_id WHERE c.descendant_id = :id ORDER BY c.depth"
            ),
            {"id": ward.id},
        )
    ).all()
    assert [tuple(r) for r in closure] == [
        ("IN-KA-MYS-W1", 0),
        ("IN-KA-MYS", 1),
        ("IN-KA", 2),
        ("IN", 3),
    ]
    await session.execute(text("DELETE FROM regions WHERE id = :id"), {"id": ward.id})
    left = await session.scalar(
        text("SELECT count(*) FROM region_closure WHERE descendant_id = :id"), {"id": ward.id}
    )
    assert left == 0


def _area_values(area_id: int, ward_code: str, region_id: int | None = None) -> dict[str, object]:
    return {
        "id": area_id,
        "ward_code": ward_code,
        "region_id": region_id,
        "b": "SRID=4326;MULTIPOLYGON(((10 10, 10.01 10, 10.01 10.01, 10 10.01, 10 10)))",
    }


_INSERT_AREA = text(
    "INSERT INTO areas (id, name, city, boundary, source, ward_code, region_id) "
    "VALUES (:id, 'Test', 'Bangalore', CAST(:b AS geography), 'test', :ward_code, :region_id)"
)


async def test_an_area_takes_the_ward_region_of_its_code(session: AsyncSession) -> None:
    blr = await _region(session, "IN-KA-BLR")
    await _add(session, "ward", "IN-KA-BLR-TEST-R1", blr.id)
    ward = await _region(session, "IN-KA-BLR-TEST-R1")
    await session.execute(_INSERT_AREA, _area_values(30500, "TEST-R1"))
    region_id = await session.scalar(text("SELECT region_id FROM areas WHERE id = 30500"))
    assert region_id == ward.id
    # A second area can't share it.
    with pytest.raises(IntegrityError, match="uq_areas_region_id"):
        async with session.begin_nested():
            await session.execute(_INSERT_AREA, _area_values(30501, "TEST-R2", ward.id))


async def test_an_area_without_a_ward_region_is_refused(session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="region_id"):
        async with session.begin_nested():
            await session.execute(_INSERT_AREA, _area_values(30502, "TEST-NONE"))


@pytest.mark.parametrize("code", ["IN", "IN-KA", "IN-KA-BLR"])
async def test_an_area_region_must_be_a_ward(session: AsyncSession, code: str) -> None:
    region = await _region(session, code)
    with pytest.raises(IntegrityError, match="must be a ward"):
        async with session.begin_nested():
            await session.execute(_INSERT_AREA, _area_values(30503, "TEST-W", region.id))
    some_area = await session.scalar(select(Area.id).where(Area.id < 30000).limit(1))
    with pytest.raises(IntegrityError, match="must be a ward"):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE areas SET region_id = :r WHERE id = :id"),
                {"r": region.id, "id": some_area},
            )


# --- CLI ----------------------------------------------------------------------------------------


async def test_cli_adds_a_city_and_a_ward_with_audit(session: AsyncSession) -> None:
    city = await cli.add_region(
        session,
        kind=RegionKind.CITY,
        code="IN-KA-MYS",
        name=" Mysuru ",
        parent_code="IN-KA",
        reason="Launch in Mysuru",
        operator=" Test Operator ",
    )
    ward = await cli.add_region(
        session,
        kind=RegionKind.WARD,
        code="IN-KA-MYS-W1",
        name="Chamundi Hill",
        parent_code="IN-KA-MYS",
        reason="Launch in Mysuru",
        operator="Test Operator",
    )
    assert (city.name, city.kind) == ("Mysuru", RegionKind.CITY)
    ancestors = (
        await session.execute(
            select(RegionClosure.ancestor_id)
            .where(RegionClosure.descendant_id == ward.id)
            .order_by(RegionClosure.depth)
        )
    ).scalars()
    assert list(ancestors) == [
        ward.id,
        city.id,
        (await _region(session, "IN-KA")).id,
        (await _region(session, "IN")).id,
    ]
    audit = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.action == "region.added", AuditLog.entity_id == str(city.id)
            )
        )
    ).scalar_one()
    assert audit.entity_type == "region" and audit.actor_id is None
    assert audit.reason == "Launch in Mysuru"
    assert audit.new_value == {
        "kind": "city",
        "code": "IN-KA-MYS",
        "name": "Mysuru",
        "parent_code": "IN-KA",
        "operator": "Test Operator",
    }


async def test_cli_adds_a_country_and_a_state(session: AsyncSession) -> None:
    country = await cli.add_region(
        session,
        kind=RegionKind.COUNTRY,
        code="LK",
        name="Sri Lanka",
        parent_code=None,
        reason="test",
        operator="Op",
    )
    state = await cli.add_region(
        session,
        kind=RegionKind.STATE,
        code="LK-1",
        name="Western",
        parent_code="LK",
        reason="test",
        operator="Op",
    )
    assert country.parent_id is None and state.parent_id == country.id


@pytest.mark.parametrize(
    ("kind", "code", "name", "parent", "reason", "operator", "message"),
    [
        (RegionKind.WARD, "IN-KA-W1", "X", "IN-KA", "r", "o", "must be under a city"),
        (RegionKind.CITY, "IN-KA-BLR-X", "X", "IN-KA-BLR", "r", "o", "must be under a state"),
        (RegionKind.CITY, "IN-KA-X", "X", None, "r", "o", "needs --parent"),
        (RegionKind.COUNTRY, "LK", "X", "IN", "r", "o", "has no parent"),
        (RegionKind.CITY, "IN-KA-X", "X", "IN-ZZ", "r", "o", "no region with code"),
        (RegionKind.CITY, "IN-TN-CHE", "X", "IN-KA", "r", "o", "must start with 'IN-KA-'"),
        (RegionKind.CITY, "in-ka-x", "X", "IN-KA", "r", "o", "upper-case"),
        (RegionKind.CITY, "IN-KA-" + "X" * 40, "X", "IN-KA", "r", "o", "at most 40"),
        (RegionKind.CITY, "IN-KA-X", " ", "IN-KA", "r", "o", "name must be"),
        (RegionKind.CITY, "IN-KA-X", "X" * 81, "IN-KA", "r", "o", "name must be"),
        (RegionKind.CITY, "IN-KA-BLR", "X", "IN-KA", "r", "o", "already exists"),
        (RegionKind.CITY, "IN-KA-X", "X", "IN-KA", " ", "o", "reason"),
        (RegionKind.CITY, "IN-KA-X", "X", "IN-KA", "r", " ", "operator"),
    ],
)
async def test_cli_rejects_bad_input_and_writes_nothing(
    session: AsyncSession,
    kind: RegionKind,
    code: str,
    name: str,
    parent: str | None,
    reason: str,
    operator: str,
    message: str,
) -> None:
    before = await session.scalar(text("SELECT count(*) FROM regions"))
    with pytest.raises(cli.RegionError, match=message):
        await cli.add_region(
            session,
            kind=kind,
            code=code,
            name=name,
            parent_code=parent,
            reason=reason,
            operator=operator,
        )
    assert await session.scalar(text("SELECT count(*) FROM regions")) == before


async def test_cli_main_reports_an_error_and_validates_arguments(
    capsys: pytest.CaptureFixture[str], migrated_db: None
) -> None:
    # A rule failure writes nothing (no commit) and exits 1.
    code = f"IN-ZZ-{uuid.uuid4().hex[:6].upper()}"
    assert await cli._add_region(RegionKind.CITY, code, "Nowhere", "IN-ZZ", "testing", "Op") == 1
    assert "no region with code" in capsys.readouterr().err
    for argv in (
        ["add-region", "village", "IN-X", "X", "--reason", "r", "--operator", "o"],
        ["add-region", "city", "IN-KA-X", "X", "--parent", "IN-KA", "--operator", "o"],
        ["add-region", "city", "IN-KA-X", "X", "--parent", "IN-KA", "--reason", "r"],
        ["add-region", "city", "IN-KA-X", "X", "--reason", " ", "--operator", "o"],
        ["add-region", "city", "IN-KA-X", "X", "--reason", "r", "--operator", " "],
    ):
        with pytest.raises(SystemExit):
            cli.main(argv)


# --- migration ----------------------------------------------------------------------------------


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


def _snapshot(conn: Connection) -> list[tuple[object, ...]]:
    return [
        tuple(r)
        for r in conn.execute(
            text(
                "SELECT r.kind, r.code, r.name, p.code, a.ward_code FROM regions AS r "
                "LEFT JOIN regions AS p ON p.id = r.parent_id "
                "LEFT JOIN areas AS a ON a.region_id = r.id ORDER BY r.code"
            )
        )
    ]


def test_seed_is_safe_to_rerun(sync_conn: Connection) -> None:
    m = _load(M19)
    before = _snapshot(sync_conn)
    closure = sync_conn.execute(text("SELECT count(*) FROM region_closure")).scalar_one()
    with Operations.context(MigrationContext.configure(sync_conn)):
        m.seed_regions()
        m.seed_regions()
    assert _snapshot(sync_conn) == before
    assert sync_conn.execute(text("SELECT count(*) FROM region_closure")).scalar_one() == closure


def test_reseeding_areas_keeps_their_ward_regions(sync_conn: Connection) -> None:
    """0017's ``reseed_areas`` deletes and re-inserts every area; the trigger gives each one its
    ward region again (by code), so a ward re-import keeps working after 0019."""
    before = sync_conn.execute(
        text("SELECT id, region_id FROM areas WHERE id < 30000 ORDER BY id")
    ).all()
    m17 = _load(VERSIONS / "2026_11_21_0100-0017_real_ward_areas.py")
    m17.reseed_areas(sync_conn)
    after = sync_conn.execute(
        text("SELECT id, region_id FROM areas WHERE id < 30000 ORDER BY id")
    ).all()
    assert after == before


def test_downgrade_and_upgrade_round_trip(sync_conn: Connection) -> None:
    """0020 then 0019 down (in a transaction that is rolled back), then up again: the same
    hierarchy and area links, and ``deeds`` config appended, never deleted."""
    m19, m20 = _load(M19), _load(M20)
    before = _snapshot(sync_conn)
    versions = sync_conn.execute(
        text("SELECT count(*) FROM config_versions WHERE namespace = 'deeds'")
    ).scalar_one()
    with Operations.context(MigrationContext.configure(sync_conn)):
        m20.downgrade()
        m19.downgrade()
        tables = sync_conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('regions', 'region_closure', 'points_ledger')"
            )
        ).all()
        assert tables == []
        column = sync_conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'areas' AND column_name = 'region_id'"
            )
        ).all()
        assert column == []
        latest = sync_conn.execute(
            text(
                "SELECT values FROM config_versions WHERE namespace = 'deeds' "
                "ORDER BY version DESC LIMIT 1"
            )
        ).scalar_one()
        assert "withdraw_window_hours" not in latest and "fallback_region_code" not in latest
        m19.upgrade()
        m20.upgrade()
    assert _snapshot(sync_conn) == before
    after = sync_conn.execute(
        text("SELECT count(*) FROM config_versions WHERE namespace = 'deeds'")
    ).scalar_one()
    assert after == versions + 2  # the rollback and the re-publish
    latest = sync_conn.execute(
        text(
            "SELECT values FROM config_versions WHERE namespace = 'deeds' "
            "ORDER BY version DESC LIMIT 1"
        )
    ).scalar_one()
    assert latest["withdraw_window_hours"] == 24
    assert latest["fallback_region_code"] == "IN-KA-BLR"


def test_deeds_v7_rollback_and_republish_append(sync_conn: Connection) -> None:
    """Downgrade of 0019's config appends a version without the two keys (never deletes);
    re-running either step is a no-op; a value published by hand in between survives."""
    import json

    from app.modules.deeds._limits import DeedLimits

    m = _load(M19)

    def latest() -> tuple[int, dict[str, object]]:
        row = sync_conn.execute(
            text(
                "SELECT version, values FROM config_versions WHERE namespace = 'deeds' "
                "ORDER BY version DESC LIMIT 1"
            )
        ).one()
        return int(row[0]), dict(row[1])

    version, v7 = latest()
    DeedLimits.model_validate(v7)
    keys = ("withdraw_window_hours", "fallback_region_code")
    with Operations.context(MigrationContext.configure(sync_conn)):
        m.rollback_v7()
        m.rollback_v7()
    down_version, down = latest()
    assert down_version == version + 1
    assert down == {k: v for k, v in v7.items() if k not in keys}
    sync_conn.execute(
        text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "VALUES ('deeds', :v, CAST(:values AS jsonb), 'by hand', NULL)"
        ),
        {"v": down_version + 1, "values": json.dumps({**down, "same_act_cap": 7})},
    )
    with Operations.context(MigrationContext.configure(sync_conn)):
        m.publish_v7()
        m.publish_v7()
    up_version, up = latest()
    assert up_version == down_version + 2
    assert up == {**v7, "same_act_cap": 7}
    assert DeedLimits.model_validate(up).withdraw_window_hours == 24
