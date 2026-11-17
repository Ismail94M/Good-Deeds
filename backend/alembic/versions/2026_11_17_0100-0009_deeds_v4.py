"""deeds v4

Security review of Revision 5, finding L4: a daily per-target limit on typed ID + code, next to
the 15-minute ``rate_manual_target`` (20/900). No schema change: publishes the next ``deeds``
version (v4 on a fresh database) = the latest values plus
``rate_manual_target_daily = "60/86400"``.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-17 01:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "deeds"
V4_REASON = (
    "Security review of Revision 5 (L4): daily per-target limit on typed ID + code, "
    "60 per 24 h; docs/M2-DESIGN.md §3.1"
)
V4_VALUES: dict[str, Any] = {"rate_manual_target_daily": "60/86400"}
V4_NEW_KEYS = list(V4_VALUES)


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
            "merged": json.dumps(V4_VALUES),
            "keys": V4_NEW_KEYS,
            "reason": reason,
        },
    )


def upgrade() -> None:
    """Upgrade schema."""
    # Append a new version = the latest values plus the new key, as max + 1. Skipped if the
    # latest already has it, so re-running is safe. created_by is NULL (a migration).
    _publish_from_latest(
        "(latest.values || CAST(:merged AS jsonb))",
        "NOT (latest.values ?& CAST(:keys AS text[]))",
        V4_REASON,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # config_versions is append-only: rollback appends a version = the latest minus the key,
    # which the pre-0009 DeedLimits accepts (it forbids unknown keys).
    _publish_from_latest(
        "(latest.values - CAST(:keys AS text[]))",
        "(latest.values ?| CAST(:keys AS text[]))",
        "Rollback of migration 0009: remove rate_manual_target_daily",
    )
