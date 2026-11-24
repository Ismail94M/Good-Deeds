"""runtime config

Versioned config rows (``config_versions``), append-only, seeded with ``qr`` v1 and
``deeds`` v1 (docs/M2-DESIGN.md §2.1).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-13 00:00:00

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_REASON = "Initial M2 values (docs/M2-DESIGN.md §2.1)"

QR_V1: dict[str, Any] = {"window_seconds": 30, "grace_windows": 1}

DEEDS_V1: dict[str, Any] = {
    "invites_per_day": 5,
    "invite_expiry_hours": 168,
    "same_act_radius_m": 200,
    "same_act_window_minutes": 120,
    "same_act_cap": 5,
    "scan_submit_minutes": 15,
    "qr_occurred_max_age_minutes": 120,
    "invite_occurred_max_age_days": 7,
    "shared_device_pair_monthly_limit": 15,
    "max_location_accuracy_m": 500,
    "max_travel_speed_kmh": 80,
    "travel_slack_m": 1000,
    "service_area": {"lat": 12.9716, "lng": 77.5946, "radius_km": 40},
    "description_min": 10,
    "description_max": 1000,
    "beneficiaries_max": 10000,
    "categories": [
        {"slug": "helping_elderly", "label": "Helping an elderly person"},
        {"slug": "animals", "label": "Animals"},
        {"slug": "environment", "label": "Environment & cleanliness"},
        {"slug": "food_sharing", "label": "Sharing food"},
        {"slug": "road_safety", "label": "Road & traffic help"},
        {"slug": "medical_help", "label": "Medical / emergency help"},
        {"slug": "community", "label": "Community help"},
        {"slug": "other", "label": "Other"},
    ],
    "rate_scan_witness": "30/3600",
    "rate_scan_ip": "60/3600",
    "rate_manual_witness": "10/900",
    "rate_manual_ip": "20/900",
    "rate_manual_target": "20/900",
    "rate_invite_create_ip": "30/86400",
    "rate_invite_preview_ip": "60/900",
    "rate_invite_accept_user": "10/3600",
    "rate_invite_accept_ip": "30/3600",
    "rate_invite_reissue_user": "10/86400",
    "rate_invite_reissue_ip": "30/86400",
    "rate_deed_edit_user": "30/86400",
    "rate_deed_edit_ip": "100/86400",
    "rate_self_log_user": "20/86400",
    "rate_self_log_ip": "100/86400",
    "rate_profile_counts_ip": "300/900",
    "unconsumed_scan_retention_days": 7,
}


def upgrade() -> None:
    """Upgrade schema."""
    config_versions = op.create_table(
        "config_versions",
        sa.Column("namespace", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_config_versions_version_positive")),
        sa.PrimaryKeyConstraint("namespace", "version", name=op.f("pk_config_versions")),
    )
    op.execute(
        "CREATE TRIGGER config_versions_append_only BEFORE UPDATE OR DELETE ON config_versions "
        "FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
    )
    # Seed rows. The table is new in this revision, so the insert runs exactly once.
    op.bulk_insert(
        config_versions,
        [
            {"namespace": "qr", "version": 1, "values": QR_V1, "reason": SEED_REASON},
            {"namespace": "deeds", "version": 1, "values": DEEDS_V1, "reason": SEED_REASON},
        ],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS config_versions_append_only ON config_versions")
    op.drop_table("config_versions")
