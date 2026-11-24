"""feed: Good Deeds Stories

PO 2026-11-23 (M3-DESIGN D73): Instagram-style stories at the top of the home feed. A story is a
listed feed post verified in the last ``story_window_hours``, shown in the reporter's ring (blue,
"reported") and the doer's ring (green, "verified"); no deed or post is copied. Adds
``story_views`` (who watched which story, so watched rings turn grey on every device; pruned
nightly) and publishes the next ``feed`` version = the latest plus the story keys.

Downgrade drops the table and appends a version without the story keys (config_versions is
append-only).

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-23 02:00:00

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "feed"
STORY_VALUES = {
    "story_window_hours": 24,
    "story_max_people": 50,
    "story_max_per_person": 20,
    "story_seconds": 5,
    "story_seen_retention_hours": 48,
    "rate_story_seen_user": "600/900",
}

_LATEST = (
    "FROM config_versions AS latest "
    "WHERE latest.namespace = CAST(:namespace AS varchar) "
    "AND latest.version = (SELECT max(version) FROM config_versions "
    "WHERE namespace = CAST(:namespace AS varchar)) "
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "story_views",
        sa.Column("viewer_id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "reported",
                "verified",
                name="story_role",
                native_enum=False,
                create_constraint=True,
                length=10,
            ),
            nullable=False,
        ),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["viewer_id"],
            ["users.id"],
            name=op.f("fk_story_views_viewer_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"],
            ["feed_posts.deed_id"],
            name=op.f("fk_story_views_deed_id_feed_posts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("viewer_id", "deed_id", "role", name="pk_story_views"),
    )
    op.create_index("ix_story_views_seen_at", "story_views", ["seen_at"])
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "SELECT latest.namespace, latest.version + 1, "
            "latest.values || CAST(:story AS jsonb), :reason, NULL "
            + _LATEST
            + "AND NOT (latest.values ? 'story_window_hours')"
        ),
        {
            "namespace": NAMESPACE,
            "story": json.dumps(STORY_VALUES),
            "reason": "PO 2026-11-23: Good Deeds Stories, 24-hour window (D73)",
        },
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "SELECT latest.namespace, latest.version + 1, "
            "latest.values - CAST(:keys AS text[]), :reason, NULL "
            + _LATEST
            + "AND latest.values ? 'story_window_hours'"
        ),
        {
            "namespace": NAMESPACE,
            "keys": list(STORY_VALUES),
            "reason": "Rollback of 0024: story keys removed",
        },
    )
    op.drop_index("ix_story_views_seen_at", table_name="story_views")
    op.drop_table("story_views")
