"""identity password reset limits

Security review of forgot password (commit 1a9fd6d), findings M1 and L2.

- ``rate_sms_reset_global``: password-reset SMS get their own global budget, charged only
  when a code is actually sent, so resets and onboarding codes (``rate_sms_global``) cannot
  starve each other.
- ``rate_password_forgot_phone_daily`` / ``rate_password_reset_phone_daily``: per-phone daily
  caps on top of the hourly ones.

Publishes the next ``identity`` version = the latest values plus these keys. Config only, no
schema change.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-17 04:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "identity"
NEW_REASON = "Forgot password security review: reset SMS budget and daily per-phone limits"
NEW_VALUES: dict[str, Any] = {
    "rate_sms_reset_global": "200/3600",
    "rate_password_forgot_phone_daily": "10/86400",
    "rate_password_reset_phone_daily": "15/86400",
}
NEW_KEYS = list(NEW_VALUES)


def _publish_from_latest(new_values_sql: str, condition_sql: str, reason: str) -> None:
    """Insert the next ``identity`` version computed from the latest one, if ``condition_sql``
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
            "merged": json.dumps(NEW_VALUES),
            "keys": NEW_KEYS,
            "reason": reason,
        },
    )


def upgrade() -> None:
    """Upgrade schema."""
    # Append a new version = the latest values plus the new keys, as max + 1. Skipped if the
    # latest already has them, so re-running is safe. created_by is NULL (a migration).
    _publish_from_latest(
        "(latest.values || CAST(:merged AS jsonb))",
        "NOT (latest.values ?& CAST(:keys AS text[]))",
        NEW_REASON,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # config_versions is append-only: rollback appends a version = the latest minus the keys,
    # which the pre-0012 IdentityLimits accepts (it forbids unknown keys).
    _publish_from_latest(
        "(latest.values - CAST(:keys AS text[]))",
        "(latest.values ?| CAST(:keys AS text[]))",
        "Rollback of migration 0012: remove reset SMS budget and daily per-phone limits",
    )
