"""deeds v7 regions

M4 DB7 (docs/M4-DESIGN.md §2.2, §2.6, D64 to D66): the location hierarchy, owned by ``deeds``.

- ``regions`` (``country``, ``state``, ``city``, ``ward``) with a stable ``code`` and
  ``region_closure`` (ancestor, descendant, depth; each region is its own ancestor at depth 0).
- Triggers on ``regions``: ``regions_check_parent`` (a ward's parent is a city, a city's a
  state, a state's a country; ``kind``, ``parent_id`` and ``code`` never change once written,
  so the closure can't go stale) and ``regions_add_closure`` (writes the closure rows of every
  new region). ``region_closure`` rows are never updated (``forbid_modification()`` on UPDATE;
  they go with their region on delete).
- Seed: India ``IN`` → Karnataka ``IN-KA`` → Bangalore ``IN-KA-BLR``, and one ward region per
  ``areas`` row (code ``IN-KA-BLR-<ward_code>``, name = the area's display name, in area-id
  order).
- ``areas.region_id`` (NOT NULL, unique, FK RESTRICT) backfilled from the ward regions. Trigger
  ``areas_assign_region``: an area inserted without ``region_id`` takes the ward region coded
  ``<its city region code>-<ward_code>`` (so a re-import such as 0017's ``reseed_areas`` keeps
  working), and an area's region must be a ward.
- ``deeds`` v7 = the latest values plus ``withdraw_window_hours = 24`` (D64) and
  ``fallback_region_code = "IN-KA-BLR"`` (D66), appended as in 0018 (publish/rollback helpers).

Re-running the seed and backfill is safe (``ON CONFLICT DO NOTHING`` on ``code``; only null
``region_id`` is filled). Downgrade drops the column, triggers and tables and appends a
``deeds`` version without the two keys (config history is never deleted).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-22 00:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "deeds"
V7_REASON = (
    "M4: 24-hour withdrawal window for verified deeds and the fallback region for deeds with "
    "no ward (docs/M4-DESIGN.md D64, D66; PO 2026-11-22)"
)
V7_VALUES: dict[str, Any] = {
    "withdraw_window_hours": 24,
    "fallback_region_code": "IN-KA-BLR",
}
V7_NEW_KEYS = list(V7_VALUES)

# (kind, code, name, parent code), parents first.
SEED_REGIONS: tuple[tuple[str, str, str, str | None], ...] = (
    ("country", "IN", "India", None),
    ("state", "IN-KA", "Karnataka", "IN"),
    ("city", "IN-KA-BLR", "Bangalore", "IN-KA"),
)
CITY_CODE = "IN-KA-BLR"
CITY_NAME = "Bangalore"  # areas.city of the seeded wards

_CHECK_PARENT_FN = """
CREATE FUNCTION regions_check_parent() RETURNS trigger AS $$
DECLARE
    parent_kind text;
    expected text;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.kind IS DISTINCT FROM OLD.kind OR NEW.parent_id IS DISTINCT FROM OLD.parent_id
           OR NEW.code IS DISTINCT FROM OLD.code THEN
            RAISE EXCEPTION 'region kind, parent and code cannot change (region %)', OLD.id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.parent_id IS NULL OR NEW.kind = 'country' THEN
        RETURN NEW;  -- CHECK ck_regions_parent_by_kind: only a country, and it has no parent
    END IF;
    expected := CASE NEW.kind WHEN 'ward' THEN 'city' WHEN 'city' THEN 'state'
                              WHEN 'state' THEN 'country' END;
    SELECT kind INTO parent_kind FROM regions WHERE id = NEW.parent_id;
    IF parent_kind IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'region parent kind: a % must be under a %, not %',
            NEW.kind, coalesce(expected, 'nothing'), coalesce(parent_kind, 'nothing')
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_ADD_CLOSURE_FN = """
CREATE FUNCTION regions_add_closure() RETURNS trigger AS $$
BEGIN
    INSERT INTO region_closure (ancestor_id, descendant_id, depth)
    SELECT NEW.id, NEW.id, 0
    UNION ALL
    SELECT c.ancestor_id, NEW.id, c.depth + 1
    FROM region_closure AS c WHERE c.descendant_id = NEW.parent_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_ASSIGN_REGION_FN = """
CREATE FUNCTION areas_assign_region() RETURNS trigger AS $$
DECLARE
    region_kind text;
BEGIN
    IF NEW.region_id IS NULL THEN
        SELECT w.id INTO NEW.region_id
        FROM regions AS c JOIN regions AS w ON w.parent_id = c.id
        WHERE c.kind = 'city' AND c.name = NEW.city AND w.kind = 'ward'
          AND w.code = c.code || '-' || NEW.ward_code;
    END IF;
    IF NEW.region_id IS NOT NULL THEN
        SELECT kind INTO region_kind FROM regions WHERE id = NEW.region_id;
        IF region_kind IS DISTINCT FROM 'ward' THEN
            RAISE EXCEPTION 'an area''s region must be a ward, not %',
                coalesce(region_kind, 'missing')
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;  -- a null region_id left here fails the NOT NULL constraint
END;
$$ LANGUAGE plpgsql
"""

_FUNCTIONS = ("regions_check_parent", "regions_add_closure", "areas_assign_region")


def _publish_from_latest(new_values_sql: str, condition_sql: str, reason: str) -> None:
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
            "merged": json.dumps(V7_VALUES),
            "keys": V7_NEW_KEYS,
            "reason": reason,
        },
    )


def publish_v7() -> None:
    """Append v7 = the latest values plus the two keys, as max + 1. Skipped if the latest
    already has both, so re-running is safe and a version published by hand in between is built
    on. created_by is NULL (a migration)."""
    _publish_from_latest(
        "(latest.values || CAST(:merged AS jsonb))",
        "NOT (latest.values ?& CAST(:keys AS text[]))",
        V7_REASON,
    )


def rollback_v7() -> None:
    """config_versions is append-only: rollback appends a version = the latest minus the two
    keys, which the pre-0019 DeedLimits accepts (it forbids unknown keys). Never deletes history;
    skipped if already rolled back."""
    _publish_from_latest(
        "(latest.values - CAST(:keys AS text[]))",
        "(latest.values ?| CAST(:keys AS text[]))",
        "Rollback of migration 0019: remove withdraw_window_hours and fallback_region_code",
    )


def seed_regions() -> None:
    """India → Karnataka → Bangalore, one ward region per Bangalore area (in area-id order), and
    ``areas.region_id`` where it is still null. Safe to re-run."""
    bind = op.get_bind()
    for kind, code, name, parent in SEED_REGIONS:
        bind.execute(
            sa.text(
                "INSERT INTO regions (kind, parent_id, name, code) "
                "VALUES (:kind, (SELECT id FROM regions WHERE code = CAST(:parent AS varchar)), "
                ":name, :code) ON CONFLICT (code) DO NOTHING"
            ),
            {"kind": kind, "code": code, "name": name, "parent": parent},
        )
    params = {"city_code": CITY_CODE, "city_name": CITY_NAME}
    bind.execute(
        sa.text(
            "INSERT INTO regions (kind, parent_id, name, code) "
            "SELECT 'ward', c.id, a.name, c.code || '-' || a.ward_code "
            "FROM areas AS a CROSS JOIN regions AS c "
            "WHERE c.code = :city_code AND a.city = :city_name ORDER BY a.id "
            "ON CONFLICT (code) DO NOTHING"
        ),
        params,
    )
    bind.execute(
        sa.text(
            "UPDATE areas AS a SET region_id = w.id FROM regions AS w JOIN regions AS c "
            "ON c.id = w.parent_id WHERE a.region_id IS NULL AND c.code = :city_code "
            "AND a.city = :city_name AND w.kind = 'ward' AND w.code = c.code || '-' || a.ward_code"
        ),
        params,
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "regions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "country",
                "state",
                "city",
                "ward",
                name="region_kind",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(kind = 'country') = (parent_id IS NULL)", name=op.f("ck_regions_parent_by_kind")
        ),
        sa.CheckConstraint(
            "length(btrim(name)) BETWEEN 1 AND 80", name=op.f("ck_regions_name_length")
        ),
        sa.CheckConstraint(
            "code ~ '^[A-Z0-9]+(-[A-Z0-9]+)*$'", name=op.f("ck_regions_code_format")
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["regions.id"],
            name=op.f("fk_regions_parent_id_regions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_regions")),
        sa.UniqueConstraint("code", name="uq_regions_code"),
    )
    op.create_index("ix_regions_parent_id", "regions", ["parent_id"], unique=False)
    op.create_table(
        "region_closure",
        sa.Column("ancestor_id", sa.Integer(), nullable=False),
        sa.Column("descendant_id", sa.Integer(), nullable=False),
        sa.Column("depth", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "(depth = 0) = (ancestor_id = descendant_id)",
            name=op.f("ck_region_closure_depth_valid"),
        ),
        sa.CheckConstraint("depth BETWEEN 0 AND 3", name=op.f("ck_region_closure_depth_range")),
        sa.ForeignKeyConstraint(
            ["ancestor_id"],
            ["regions.id"],
            name=op.f("fk_region_closure_ancestor_id_regions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["descendant_id"],
            ["regions.id"],
            name=op.f("fk_region_closure_descendant_id_regions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ancestor_id", "descendant_id", name=op.f("pk_region_closure")),
    )
    op.create_index(
        "ix_region_closure_descendant_id", "region_closure", ["descendant_id"], unique=False
    )
    op.execute(_CHECK_PARENT_FN)
    op.execute(
        "CREATE TRIGGER regions_check_parent BEFORE INSERT OR UPDATE ON regions "
        "FOR EACH ROW EXECUTE FUNCTION regions_check_parent()"
    )
    op.execute(_ADD_CLOSURE_FN)
    op.execute(
        "CREATE TRIGGER regions_add_closure AFTER INSERT ON regions "
        "FOR EACH ROW EXECUTE FUNCTION regions_add_closure()"
    )
    op.execute(
        "CREATE TRIGGER region_closure_no_update BEFORE UPDATE ON region_closure "
        "FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
    )

    op.add_column("areas", sa.Column("region_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_areas_region_id_regions"),
        "areas",
        "regions",
        ["region_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_areas_region_id", "areas", ["region_id"])
    seed_regions()
    op.alter_column("areas", "region_id", existing_type=sa.Integer(), nullable=False)
    op.execute(_ASSIGN_REGION_FN)
    op.execute(
        "CREATE TRIGGER areas_assign_region BEFORE INSERT OR UPDATE OF region_id ON areas "
        "FOR EACH ROW EXECUTE FUNCTION areas_assign_region()"
    )

    publish_v7()


def downgrade() -> None:
    """Downgrade schema."""
    rollback_v7()
    op.execute("DROP TRIGGER IF EXISTS areas_assign_region ON areas")
    op.drop_constraint("uq_areas_region_id", "areas", type_="unique")
    op.drop_constraint(op.f("fk_areas_region_id_regions"), "areas", type_="foreignkey")
    op.drop_column("areas", "region_id")
    op.execute("DROP TRIGGER IF EXISTS region_closure_no_update ON region_closure")
    op.execute("DROP TRIGGER IF EXISTS regions_add_closure ON regions")
    op.execute("DROP TRIGGER IF EXISTS regions_check_parent ON regions")
    op.drop_table("region_closure")
    op.drop_table("regions")
    for fn in _FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
