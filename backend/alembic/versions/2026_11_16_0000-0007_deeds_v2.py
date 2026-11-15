"""deeds v2

Business-logic review decisions (docs/M2-DESIGN.md §0.2):

- D15: the profile "Verified Deeds" count counts only counted reports, so its partial index
  ``ix_deeds_doer_verified`` (``status = 'verified'``) is replaced by ``ix_deeds_doer_counted``
  (``status = 'verified' AND counts_for_points``).
- D16 and D17: publishes the next ``deeds`` version (v2 on a fresh database): the latest values plus
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
            "added": json.dumps(V2_ADDED),
            "keys": list(V2_ADDED),
            "reason": reason,
        },
    )


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
    # Append a new version = the *latest* version plus the new keys, as max + 1 (v2 on a fresh
    # database). Skipped if the latest already has both keys, so re-running is safe and a version
    # published by hand in between is built on, not ignored. created_by is NULL (a migration).
    _publish_from_latest(
        "(latest.values || CAST(:added AS jsonb))",
        "NOT (latest.values ?& CAST(:keys AS text[]))",
        V2_REASON,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # config_versions is append-only, so rollback appends a new version = the latest minus the
    # two keys, which the pre-0007 DeedLimits accepts (it forbids unknown keys). This works even
    # if later versions were published after v2, and never deletes history.
    _publish_from_latest(
        "(latest.values - CAST(:keys AS text[]))",
        "(latest.values ?| CAST(:keys AS text[]))",
        "Rollback of migration 0007: remove reporter_monthly_limit and invites_enabled",
    )
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
