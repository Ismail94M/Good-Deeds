"""deeds v5

M3 area names (docs/M3-DESIGN.md §2.1, D39, D41):

- ``areas``: reference table of Bangalore areas (one row per ward, a familiar display name, the
  dataset's ward code and source), GiST index on ``boundary``. Seeded from
  ``app/modules/deeds/data/bangalore_areas.geojson``: one ``Feature`` per area with a
  ``Polygon`` or ``MultiPolygon`` geometry (WGS 84) and the properties ``name``, ``ward_code``
  and ``source``. Ids are 1..n in file order (on a shared border the lowest id wins), so
  replacing the file is the only change needed to load another dataset.
- ``deeds.area_id`` (FK ``areas``, RESTRICT), backfilled with the lookup rule of §2.1: the
  lowest-id area covering the deed's point, else the nearest area within ``area_fallback_m``
  (1500 m), else null ("Bangalore").
- Publishes the next ``deeds`` version (v5 on a fresh database) = the latest values minus
  ``photos_enabled`` (moved to the ``media`` namespace, migration 0014) plus
  ``area_fallback_m = 1500``.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-19 00:00:00

"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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

NAMESPACE = "deeds"
AREA_FALLBACK_M = 1500
V5_REASON = (
    "M3 (PO 2026-11-19): area names from ward boundaries, area_fallback_m 1500 (D39); "
    "photos_enabled moved to the media namespace (D41); docs/M3-DESIGN.md §2.1"
)
V5_ADDED: dict[str, Any] = {"area_fallback_m": AREA_FALLBACK_M}
V5_REMOVED = ["photos_enabled"]
# The downgrade restores the v4 value of the removed key.
V4_RESTORED: dict[str, Any] = {"photos_enabled": False}


def _publish_from_latest(
    new_values_sql: str,
    condition_sql: str,
    reason: str,
    merged: dict[str, Any],
    removed: list[str],
) -> None:
    """Insert the next ``deeds`` version computed from the latest one, if ``condition_sql``
    holds for the latest. Both SQL fragments are constants from this file."""
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            f"SELECT latest.namespace, latest.version + 1, {new_values_sql}, :reason, NULL "
            "FROM config_versions AS latest "
            "WHERE latest.namespace = CAST(:namespace AS varchar) "
            "AND latest.version = (SELECT max(version) FROM config_versions "
            "WHERE namespace = CAST(:namespace AS varchar)) "
            f"AND {condition_sql}"
        ),
        {
            "namespace": NAMESPACE,
            "merged": json.dumps(merged),
            "removed": removed,
            "reason": reason,
        },
    )


def _load_areas() -> list[dict[str, Any]]:
    """The seed rows from the GeoJSON file, checked before anything is inserted."""
    with AREAS_FILE.open(encoding="utf-8") as f:
        collection = json.load(f)
    if collection.get("type") != "FeatureCollection" or not collection.get("features"):
        raise ValueError(f"{AREAS_FILE.name}: expected a non-empty FeatureCollection")
    rows: list[dict[str, Any]] = []
    for area_id, feature in enumerate(collection["features"], start=1):
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        name = str(props.get("name", "")).strip()
        ward_code = str(props.get("ward_code", "")).strip()
        source = str(props.get("source", "")).strip()
        if not 1 <= len(name) <= NAME_MAX:
            raise ValueError(f"{AREAS_FILE.name} feature {area_id}: name must be 1-{NAME_MAX}")
        if not 1 <= len(ward_code) <= 20:
            raise ValueError(f"{AREAS_FILE.name} feature {area_id}: ward_code must be 1-20")
        if not 1 <= len(source) <= 120:
            raise ValueError(f"{AREAS_FILE.name} feature {area_id}: source must be 1-120")
        if geometry.get("type") not in ("Polygon", "MultiPolygon"):
            raise ValueError(f"{AREAS_FILE.name} feature {area_id}: geometry must be a polygon")
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


def backfill_area_ids() -> None:
    """Set ``deeds.area_id`` with the §2.1 lookup rule: the lowest-id area covering the deed's
    point, else the nearest area within ``AREA_FALLBACK_M``, else null. Only deeds with a
    location and no area yet, so re-running is safe. ``updated_at`` is left alone: this is not
    an edit of the deed."""
    op.get_bind().execute(
        sa.text(
            "UPDATE deeds AS d SET area_id = COALESCE("
            "(SELECT a.id FROM areas AS a WHERE ST_Covers(a.boundary, d.location) "
            "ORDER BY a.id LIMIT 1), "
            "(SELECT a.id FROM areas AS a WHERE ST_DWithin(a.boundary, d.location, :fallback_m) "
            "ORDER BY ST_Distance(a.boundary, d.location), a.id LIMIT 1)) "
            "WHERE d.location IS NOT NULL AND d.area_id IS NULL"
        ),
        {"fallback_m": AREA_FALLBACK_M},
    )


def publish_v5() -> None:
    """Append v5 = (latest minus photos_enabled) plus area_fallback_m, as max + 1. Skipped if the
    latest already has area_fallback_m, so re-running is safe and a version published by hand in
    between is built on. created_by is NULL (a migration)."""
    _publish_from_latest(
        "((latest.values - CAST(:removed AS text[])) || CAST(:merged AS jsonb))",
        "NOT (latest.values ? 'area_fallback_m')",
        V5_REASON,
        V5_ADDED,
        V5_REMOVED,
    )


def rollback_v5() -> None:
    """config_versions is append-only, so rollback appends a new version = the latest minus
    area_fallback_m, with photos_enabled back (false, its v4 value). The pre-0013 DeedLimits
    accepts it (it forbids unknown keys). Never deletes history; skipped if already rolled back."""
    _publish_from_latest(
        "((latest.values - CAST(:removed AS text[])) || CAST(:merged AS jsonb))",
        "(latest.values ? 'area_fallback_m')",
        "Rollback of migration 0013: remove area_fallback_m, photos_enabled back to false",
        V4_RESTORED,
        list(V5_ADDED),
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "areas",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("city", sa.String(length=40), nullable=False),
        sa.Column(
            "boundary",
            Geography(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("ward_code", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "length(btrim(name)) BETWEEN 1 AND 40", name=op.f("ck_areas_name_length")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_areas")),
        sa.UniqueConstraint("ward_code", name="uq_areas_ward_code"),
    )
    op.create_index(
        "ix_areas_boundary", "areas", ["boundary"], unique=False, postgresql_using="gist"
    )

    # Seed. The table is new in this revision, so the insert runs exactly once. Invalid rings
    # from the source dataset are repaired (ST_MakeValid) and only polygon parts are kept.
    insert = sa.text(
        "INSERT INTO areas (id, name, city, boundary, source, ward_code) "
        "VALUES (:id, :name, :city, "
        "CAST(ST_Multi(ST_CollectionExtract(ST_MakeValid("
        "ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326)), 3)) AS geography), "
        ":source, :ward_code)"
    )
    bind = op.get_bind()
    for row in _load_areas():
        bind.execute(insert, row)

    op.add_column("deeds", sa.Column("area_id", sa.SmallInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_deeds_area_id_areas"), "deeds", "areas", ["area_id"], ["id"], ondelete="RESTRICT"
    )
    backfill_area_ids()

    publish_v5()


def downgrade() -> None:
    """Downgrade schema."""
    rollback_v5()
    op.drop_constraint(op.f("fk_deeds_area_id_areas"), "deeds", type_="foreignkey")
    op.drop_column("deeds", "area_id")
    op.drop_index("ix_areas_boundary", table_name="areas", postgresql_using="gist")
    op.drop_table("areas")
