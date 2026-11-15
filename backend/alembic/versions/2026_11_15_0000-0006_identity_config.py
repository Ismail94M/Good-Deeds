"""identity config

Seeds ``identity`` v1 in ``config_versions``: M1's OTP and auth rate limits, moved out of env
settings (docs/M2-DESIGN.md §6, task B10). The values are the old ``Settings`` defaults, so
behaviour doesn't change. No schema change.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-15 00:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "identity"
SEED_REASON = "M1 limits moved from env settings (docs/M2-DESIGN.md B10)"

IDENTITY_V1: dict[str, Any] = {
    "otp_ttl_minutes": 10,
    "otp_max_attempts": 5,
    "otp_resend_seconds": 30,
    "otp_max_per_hour": 5,
    "rate_login_ip": "20/900",
    "rate_login_email": "10/900",
    "rate_signup_ip": "10/3600",
    "rate_phone_start_ip": "10/3600",
    "rate_sms_global": "500/3600",
    "rate_phone_verify_user": "20/3600",
    "rate_refresh_ip": "120/900",
    "rate_oauth_ip": "30/900",
}


def upgrade() -> None:
    """Upgrade schema."""
    # Safe to re-run: v1 is inserted only if the namespace has no rows yet.
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason) "
            "SELECT CAST(:namespace AS varchar), 1, CAST(:values AS jsonb), :reason "
            "WHERE NOT EXISTS "
            "(SELECT 1 FROM config_versions WHERE namespace = CAST(:namespace AS varchar))"
        ),
        {"namespace": NAMESPACE, "values": json.dumps(IDENTITY_V1), "reason": SEED_REASON},
    )


def downgrade() -> None:
    """Downgrade schema."""
    # config_versions is append-only, so its trigger is bypassed for this one namespace. Any
    # later published identity versions go too: before 0006 the namespace did not exist.
    op.execute("ALTER TABLE config_versions DISABLE TRIGGER config_versions_append_only")
    op.execute(f"DELETE FROM config_versions WHERE namespace = '{NAMESPACE}'")
    op.execute("ALTER TABLE config_versions ENABLE TRIGGER config_versions_append_only")
