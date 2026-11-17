"""deeds v3

Revision 5 decisions (docs/M2-DESIGN.md §0.3, §2.1). No schema change: publishes the next
``deeds`` version (v3 on a fresh database) = the latest values plus

- D19: ``reporter_doer_monthly_limit = 15`` (new, per reporter-doer pair) and
  ``reporter_monthly_limit = 150`` (raised from 15; the total across all doers);
- D23: ``photos_enabled = false`` (photo storage isn't chosen yet).

Both monthly counts use the existing ``ix_deeds_reporter_reported_at (reporter_user_id,
reported_at DESC)``; the pair count filters ``doer_user_id`` within that range.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-17 00:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "deeds"
V3_REASON = (
    "Revision 5 (PO 2026-11-16/17): per reporter-doer monthly limit 15 and total monthly limit "
    "150 (D19); photos hidden in M2 (D23); docs/M2-DESIGN.md §0.3"
)
# Merged into the latest values on upgrade.
V3_VALUES: dict[str, Any] = {
    "reporter_doer_monthly_limit": 15,
    "reporter_monthly_limit": 150,
    "photos_enabled": False,
}
# Keys that only exist from v3 on (removed again by the downgrade).
V3_NEW_KEYS = ["reporter_doer_monthly_limit", "photos_enabled"]
# The downgrade restores the v2 value of the key that v3 changed.
V2_RESTORED: dict[str, Any] = {"reporter_monthly_limit": 15}


def _publish_from_latest(
    new_values_sql: str, condition_sql: str, reason: str, merged: dict[str, Any]
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
            "keys": V3_NEW_KEYS,
            "reason": reason,
        },
    )


def upgrade() -> None:
    """Upgrade schema."""
    # Append a new version = the *latest* version merged with the v3 values, as max + 1. Skipped
    # if the latest already has ``reporter_doer_monthly_limit``, so re-running is safe and a
    # version published by hand in between is built on. created_by is NULL (a migration).
    _publish_from_latest(
        "(latest.values || CAST(:merged AS jsonb))",
        "NOT (latest.values ? 'reporter_doer_monthly_limit')",
        V3_REASON,
        V3_VALUES,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # config_versions is append-only, so rollback appends a new version = the latest minus the
    # v3-only keys, with ``reporter_monthly_limit`` back at 15. The pre-0008 DeedLimits accepts
    # it (it forbids unknown keys). Never deletes history.
    _publish_from_latest(
        "((latest.values - CAST(:keys AS text[])) || CAST(:merged AS jsonb))",
        "(latest.values ?| CAST(:keys AS text[]))",
        "Rollback of migration 0008: remove reporter_doer_monthly_limit and photos_enabled, "
        "reporter_monthly_limit back to 15",
        V2_RESTORED,
    )
