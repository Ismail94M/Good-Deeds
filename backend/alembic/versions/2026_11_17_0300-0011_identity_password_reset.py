"""identity password reset

Forgot password through the verified phone (PO decision 2026-11-17, added to M2).

- ``phone_otps.purpose`` (``phone_verify`` | ``password_reset``): a code only works for the
  purpose it was issued for. Existing rows are phone-verify codes (server default).
- Publishes the next ``identity`` version (v2 on a fresh database) = the latest values plus
  the forgot/reset rate limits. Code TTL and attempt limits reuse the existing ``otp_*`` keys.

Revision ID: 0011
Revises: 0009
Create Date: 2026-11-17 03:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "identity"
NEW_REASON = "Forgot password via verified phone: rate limits (PO decision 2026-11-17)"
NEW_VALUES: dict[str, Any] = {
    "rate_password_forgot_ip": "10/3600",
    "rate_password_forgot_phone": "5/3600",
    "rate_password_reset_ip": "20/3600",
    "rate_password_reset_phone": "10/3600",
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
    op.add_column(
        "phone_otps",
        sa.Column(
            "purpose",
            sa.String(length=20),
            server_default="phone_verify",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "otp_purpose", "phone_otps", "purpose IN ('phone_verify', 'password_reset')"
    )
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
    # which the pre-0011 IdentityLimits accepts (it forbids unknown keys).
    _publish_from_latest(
        "(latest.values - CAST(:keys AS text[]))",
        "(latest.values ?| CAST(:keys AS text[]))",
        "Rollback of migration 0011: remove password reset rate limits",
    )
    # Reset codes are meaningless to the old code, which would treat them as phone-verify
    # codes; drop them before removing the column.
    op.execute("DELETE FROM phone_otps WHERE purpose <> 'phone_verify'")
    op.drop_constraint(op.f("ck_phone_otps_otp_purpose"), "phone_otps", type_="check")
    op.drop_column("phone_otps", "purpose")
