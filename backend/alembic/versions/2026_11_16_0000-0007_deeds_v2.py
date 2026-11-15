"""deeds v2

Business-logic review decisions (docs/M2-DESIGN.md §0.2):

- D15: the profile "Verified Deeds" count counts only counted reports, so its partial index
  ``ix_deeds_doer_verified`` (``status = 'verified'``) is replaced by ``ix_deeds_doer_counted``
  (``status = 'verified' AND counts_for_points``).
- D16 and D17: publishes ``deeds`` v2 in ``config_versions``: v1's values plus
  ``reporter_monthly_limit = 15`` and ``invites_enabled = false``. The monthly count uses the
  existing ``ix_deeds_reporter_reported_at (reporter_user_id, reported_at DESC)``.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-16 00:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "deeds"
V2_REASON = (
    "PO business-logic review 2026-11-16: monthly reporting limit (D16) and invites "
    "switched off (D17); docs/M2-DESIGN.md §0.2"
)
V2_ADDED: dict[str, Any] = {"reporter_monthly_limit": 15, "invites_enabled": False}


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(
        "ix_deeds_doer_verified",
        table_name="deeds",
        postgresql_where=sa.text("status = 'verified'"),
    )
    op.create_index(
        "ix_deeds_doer_counted",
        "deeds",
        ["doer_user_id"],
        unique=False,
        postgresql_where=sa.text("status = 'verified' AND counts_for_points"),
    )
    # v2 = v1's stored values plus the new keys. Safe to re-run: inserted only if v2 doesn't
    # exist yet. created_by is NULL (a migration, not a person).
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "SELECT v1.namespace, 2, v1.values || CAST(:added AS jsonb), :reason, NULL "
            "FROM config_versions AS v1 "
            "WHERE v1.namespace = CAST(:namespace AS varchar) AND v1.version = 1 "
            "AND NOT EXISTS (SELECT 1 FROM config_versions "
            "WHERE namespace = CAST(:namespace AS varchar) AND version = 2)"
        ),
        {"namespace": NAMESPACE, "added": json.dumps(V2_ADDED), "reason": V2_REASON},
    )


def downgrade() -> None:
    """Downgrade schema."""
    # config_versions is append-only, so its trigger is bypassed to remove exactly deeds v2
    # (v1 stays the active version again).
    op.execute("ALTER TABLE config_versions DISABLE TRIGGER config_versions_append_only")
    op.execute(f"DELETE FROM config_versions WHERE namespace = '{NAMESPACE}' AND version = 2")
    op.execute("ALTER TABLE config_versions ENABLE TRIGGER config_versions_append_only")
    op.drop_index(
        "ix_deeds_doer_counted",
        table_name="deeds",
        postgresql_where=sa.text("status = 'verified' AND counts_for_points"),
    )
    op.create_index(
        "ix_deeds_doer_verified",
        "deeds",
        ["doer_user_id"],
        unique=False,
        postgresql_where=sa.text("status = 'verified'"),
    )
