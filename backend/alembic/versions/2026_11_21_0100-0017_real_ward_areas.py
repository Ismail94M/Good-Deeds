"""real ward areas

M3 DB4 follow-up (Q28, PO decisions 2026-11-19 and 2026-11-21): ``areas`` is re-seeded from
``app/modules/deeds/data/bangalore_areas.geojson``, which now holds the 369 GBA wards
(``data/ward_candidate/AREAS_SOURCE.md``) with the display names approved as proposed in
``names.csv``. Migration 0013 seeded whatever that file held at the time (the placeholder on
existing databases), and its insert runs only once, so this revision replaces the rows.

Re-seed, in one transaction:

1. Every ``deeds.area_id`` is cleared, so the FK (RESTRICT) never points at a removed area.
2. All ``areas`` rows are deleted and the file is inserted with ids 1..n in file order (on a
   shared border the lowest id wins), ``ward_code`` per row (official names can be joined on it
   later). The ``official_name`` property in the file is not stored (PO 2026-11-21).
3. Geometry: each feature is checked with ``ST_IsValid``. Invalid ones are repaired with
   ``ST_MakeValid``, polygon parts kept (``ST_CollectionExtract(.., 3)``) as a MultiPolygon.
   The migration fails if a repaired geometry is still invalid or empty. Counts are logged,
   plus every pair of wards overlapping by more than ``OVERLAP_REPORT_M2`` (slivers below that
   are expected along digitised borders and handled by the lowest-id rule).
4. ``deeds.area_id`` is recomputed from each deed's own point with the §2.1 lookup rule (the
   latest ``deeds.area_fallback_m``), so old deeds keep a meaningful area; deeds outside every
   ward stay null ("Bangalore"). ``updated_at`` is left alone.

Running it again gives the same rows (idempotent). Downgrade does nothing: the placeholder data
is gone, and the real wards are valid for the schema of 0013.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-21 01:00:00

"""

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration.0017")

AREAS_FILE = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "modules"
    / "deeds"
    / "data"
    / "bangalore_areas.geojson"
)
CITY = "Bangalore"
NAME_MAX = 40  # Q28 name clean-up rule (the column allows 80)
DEFAULT_FALLBACK_M = 1500  # used only if no deeds config row has area_fallback_m
OVERLAP_REPORT_M2 = 100.0


def load_areas(path: Path = AREAS_FILE) -> list[dict[str, Any]]:
    """The seed rows from the GeoJSON file, checked before anything is changed."""
    with path.open(encoding="utf-8") as f:
        collection = json.load(f)
    if collection.get("type") != "FeatureCollection" or not collection.get("features"):
        raise ValueError(f"{path.name}: expected a non-empty FeatureCollection")
    rows: list[dict[str, Any]] = []
    codes: set[str] = set()
    for area_id, feature in enumerate(collection["features"], start=1):
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        name = str(props.get("name", "")).strip()
        ward_code = str(props.get("ward_code", "")).strip()
        source = str(props.get("source", "")).strip()
        if not 1 <= len(name) <= NAME_MAX:
            raise ValueError(f"{path.name} feature {area_id}: name must be 1-{NAME_MAX}")
        if not 1 <= len(ward_code) <= 20:
            raise ValueError(f"{path.name} feature {area_id}: ward_code must be 1-20")
        if ward_code in codes:
            raise ValueError(f"{path.name} feature {area_id}: duplicate ward_code {ward_code}")
        codes.add(ward_code)
        if not 1 <= len(source) <= 120:
            raise ValueError(f"{path.name} feature {area_id}: source must be 1-120")
        if geometry.get("type") not in ("Polygon", "MultiPolygon"):
            raise ValueError(f"{path.name} feature {area_id}: geometry must be a polygon")
        rows.append(
            {
                "id": area_id,
                "name": name,
                "city": CITY,
                "geometry": json.dumps(geometry),
                "source": source,
                "ward_code": ward_code,
            }
        )
    return rows


_INSERT = sa.text(
    "INSERT INTO areas (id, name, city, boundary, source, ward_code) "
    "SELECT :id, :name, :city, CAST(CASE WHEN ST_IsValid(g) THEN ST_Multi(g) "
    "ELSE ST_Multi(ST_CollectionExtract(ST_MakeValid(g), 3)) END AS geography), "
    ":source, :ward_code "
    "FROM (SELECT ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326) AS g) AS src "
    "RETURNING NOT ST_IsValid(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326))"
)

_BACKFILL = sa.text(
    "UPDATE deeds AS d SET area_id = COALESCE("
    "(SELECT a.id FROM areas AS a WHERE ST_Covers(a.boundary, d.location) "
    "ORDER BY a.id LIMIT 1), "
    "(SELECT a.id FROM areas AS a WHERE ST_DWithin(a.boundary, d.location, :fallback_m) "
    "ORDER BY ST_Distance(a.boundary, d.location), a.id LIMIT 1)) "
    "WHERE d.location IS NOT NULL"
)


def _fallback_m(bind: sa.Connection) -> int:
    value = bind.execute(
        sa.text(
            "SELECT CAST(values ->> 'area_fallback_m' AS integer) FROM config_versions "
            "WHERE namespace = 'deeds' AND values ? 'area_fallback_m' "
            "ORDER BY version DESC LIMIT 1"
        )
    ).scalar()
    return DEFAULT_FALLBACK_M if value is None else int(value)


def reseed_areas(bind: sa.Connection, path: Path = AREAS_FILE) -> dict[str, Any]:
    """Replace ``areas`` with the file and re-resolve ``deeds.area_id``. Returns the counts that
    are also logged."""
    rows = load_areas(path)
    bind.execute(sa.text("UPDATE deeds SET area_id = NULL WHERE area_id IS NOT NULL"))
    bind.execute(sa.text("DELETE FROM areas"))
    repaired = sum(1 for row in rows if bind.execute(_INSERT, row).scalar_one())
    bad = bind.execute(
        sa.text(
            "SELECT ward_code FROM areas WHERE NOT ST_IsValid(boundary::geometry) "
            "OR ST_IsEmpty(boundary::geometry) "
            "OR GeometryType(boundary::geometry) <> 'MULTIPOLYGON' ORDER BY id"
        )
    ).scalars()
    still_bad = list(bad)
    if still_bad:
        raise ValueError(f"{path.name}: geometry still invalid after repair: {still_bad}")
    overlaps = [
        (str(r[0]), str(r[1]), float(r[2]))
        for r in bind.execute(
            sa.text(
                # Planar geometry: geography ST_Intersects on 369 detailed wards is far slower.
                "WITH g AS (SELECT id, ward_code, boundary::geometry AS g FROM areas) "
                "SELECT a.ward_code, b.ward_code, ov FROM g AS a JOIN g AS b "
                "ON a.id < b.id AND a.g && b.g AND ST_Overlaps(a.g, b.g) "
                "CROSS JOIN LATERAL (SELECT ST_Area(CAST(ST_Intersection(a.g, b.g) "
                "AS geography)) AS ov) AS x "
                "WHERE ov > :min_m2 ORDER BY ov DESC"
            ),
            {"min_m2": OVERLAP_REPORT_M2},
        )
    ]
    bind.execute(_BACKFILL, {"fallback_m": _fallback_m(bind)})
    stats: dict[str, Any] = {
        "areas": len(rows),
        "repaired": repaired,
        "overlaps": overlaps,
        "deeds_with_area": bind.execute(
            sa.text("SELECT count(*) FROM deeds WHERE area_id IS NOT NULL")
        ).scalar_one(),
    }
    logger.info(
        "areas re-seeded: %d areas, %d invalid geometries repaired (ST_MakeValid), "
        "%d overlaps over %.0f m2 %s, %d deeds with an area",
        stats["areas"],
        repaired,
        len(overlaps),
        OVERLAP_REPORT_M2,
        overlaps,
        stats["deeds_with_area"],
    )
    return stats


def upgrade() -> None:
    """Upgrade schema."""
    reseed_areas(op.get_bind())


def downgrade() -> None:
    """Downgrade schema. Data-only revision: the previous rows are not kept, nothing to undo."""
