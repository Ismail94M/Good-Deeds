"""deeds v6

Report-limit warning thresholds move from frontend constants to config (CLAUDE.md: every tunable
limit lives in config rows). No schema change: publishes the next ``deeds`` version (v6 on a fresh
database) = the latest values plus

- ``reporter_monthly_warn_at = 10``: the report hub warns when this many or fewer reports are
  left this IST month in total (M2-DESIGN D19, D20, §5.5);
- ``reporter_doer_monthly_warn_at = 3``: the deed form warns when this many or fewer reports
  about the scanned doer are left this IST month.

Both are returned by ``GET /api/deeds/options``. Values are unchanged from the old constants.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-21 02:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "deeds"
V6_REASON = (
    "M3 follow-up: report-limit warning thresholds moved from frontend constants to config "
    "(total 10, per doer 3; M2-DESIGN D19, D20, §5.5)"
)
V6_VALUES: dict[str, Any] = {
    "reporter_monthly_warn_at": 10,
    "reporter_doer_monthly_warn_at": 3,
}
V6_NEW_KEYS = list(V6_VALUES)


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
            "merged": json.dumps(V6_VALUES),
            "keys": V6_NEW_KEYS,
            "reason": reason,
        },
    )


def publish_v6() -> None:
    """Append v6 = the latest values plus the two thresholds, as max + 1. Skipped if the latest
    already has both, so re-running is safe and a version published by hand in between is built
    on. created_by is NULL (a migration)."""
    _publish_from_latest(
        "(latest.values || CAST(:merged AS jsonb))",
        "NOT (latest.values ?& CAST(:keys AS text[]))",
        V6_REASON,
    )


def rollback_v6() -> None:
    """config_versions is append-only: rollback appends a version = the latest minus the two
    keys, which the pre-0018 DeedLimits accepts (it forbids unknown keys). Never deletes history;
    skipped if already rolled back."""
    _publish_from_latest(
        "(latest.values - CAST(:keys AS text[]))",
        "(latest.values ?| CAST(:keys AS text[]))",
        "Rollback of migration 0018: remove the report-limit warning thresholds",
    )


def upgrade() -> None:
    """Upgrade schema."""
    publish_v6()


def downgrade() -> None:
    """Downgrade schema."""
    rollback_v6()
