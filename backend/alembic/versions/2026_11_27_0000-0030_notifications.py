"""notifications

M6A DB12 (docs/M6-DESIGN.md §2.1, §2.5, D106 to D115; revision 2, PO approved 2026-11-27): the
``notifications`` module tables and the ``notifications`` v1 config.

Tables and the invariants the database enforces:

- ``notifications``: ``kind`` is a catalogue kind (§4.1) and ``category`` is the one that kind
  belongs to (CHECK ``kind_category``); exactly the grouped kinds (network, like, comment, reply,
  follow) carry a ``group_key`` (CHECK ``group_key_iff_grouped``, D108); ``actor_ids`` never
  holds more ids than ``actor_count``; ``data`` is a JSON object. ``uq_notifications_user_dedupe_key
  (user_id, dedupe_key)`` makes a handler re-run harmless; the partial unique
  ``uq_notifications_unread_group (user_id, group_key) WHERE read_at IS NULL AND group_key IS
  NOT NULL`` keeps one unread row per group. Indexes: the list ``(user_id, updated_at DESC,
  id)`` (keyset), the unread count ``(user_id) WHERE read_at IS NULL``, and ``(updated_at)`` for
  the nightly purge.
- ``notification_views``: one row per user (bell opened), upserted.
- ``notification_preferences``: PK ``(user_id, category)``; a missing row = the config default.
- ``notification_mutes``: PK ``(user_id, target_type, target_id)``; ``target_type`` person/deed.
- ``notification_dedupe``: PK ``(user_id, key)``; ``(created_at)`` for the purge.
- ``notification_fanouts``: PK ``deed_id``; ``finished_at`` is set exactly when the status is not
  ``running``; partial index on running fan-outs (resume after a restart).
- ``notification_daily_counts``: PK ``(user_id, day, category)``; ``(day)`` for the purge.

No table is append-only (rows are grouped, read and purged). Foreign keys to ``users`` and
``deeds`` use RESTRICT.

Downgrade drops the tables and the ``notifications`` config namespace.

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-27 00:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# In creation order (downgrade drops them in reverse).
TABLES = (
    "notifications",
    "notification_views",
    "notification_preferences",
    "notification_mutes",
    "notification_dedupe",
    "notification_fanouts",
    "notification_daily_counts",
)

CATEGORIES = ("about_you", "network", "activity", "account", "reviews")
# Kind -> category (§4.1). Kept in step with ``notifications/models.py:KIND_CATEGORY``.
KIND_CATEGORY: dict[str, str] = {
    "recognised": "about_you",
    "deed_verified": "about_you",
    "network": "network",
    "like": "activity",
    "comment": "activity",
    "reply": "activity",
    "follow": "activity",
    "badge_awarded": "account",
    "title_awarded": "account",
    "photo_removed": "account",
    "photo_kept": "account",
    "photo_removed_by_doer": "account",
    "comment_removed": "account",
    "avatar_removed": "account",
    "password_changed": "account",
    "review_update": "reviews",
}
GROUPED_KINDS = ("comment", "follow", "like", "network", "reply")

NAMESPACE = "notifications"
SEED_REASON = (
    "Initial M6A values (docs/M6-DESIGN.md §2.5, revision 2; PO answers Q86 to Q96 and the "
    "architect defaults, 2026-11-27)"
)
NOTIFICATIONS_V1: dict[str, Any] = {
    "timezone": "Asia/Kolkata",
    # D107, D113: Account & safety and Reviews are always on.
    "categories": {
        "about_you": {"default": True, "locked": False},
        "network": {"default": True, "locked": False},
        "activity": {"default": True, "locked": False},
        "account": {"default": True, "locked": True},
        "reviews": {"default": True, "locked": True},
    },
    # Q91: all five review updates notify.
    "review_kinds": [
        "explanation_requested",
        "explanation_reminder",
        "decision_notice",
        "appeal_decided",
        "review_closed",
    ],
    # D110, Q89: followers of the reporter or the doer, one row a day, with caps.
    "network": {
        "audience": "followers_of_actor",
        "roles": ["reporter", "doer"],
        "fanout_batch": 1000,
        "max_recipients_per_event": 5000,
        "max_fanouts_per_actor_day": 3,
    },
    # D111, Q90: "also commented" for earlier commenters of the last 30 days.
    "replies": {"enabled": True, "lookback_days": 30},
    # D115.
    "daily_caps": {"activity": 30, "network": 1},
    "actors_kept": 3,
    "dedupe_days": 30,
    "retention_days": 90,
    "max_mutes": 500,
    "page_size": 30,
    "unread_poll_seconds": 60,
    "rate_read": "300/60",
    "rate_write": "60/60",
}


def _enum(name: str, *values: str, length: int = 20) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=length)


def _users_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["user_id"], ["users.id"], name=op.f(f"fk_{table}_user_id_users"), ondelete="RESTRICT"
    )


def _deeds_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["deed_id"], ["deeds.id"], name=op.f(f"fk_{table}_deed_id_deeds"), ondelete="RESTRICT"
    )


def _now(name: str) -> sa.Column[Any]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _sql_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _kind_category_sql() -> str:
    by_category: dict[str, list[str]] = {}
    for kind, category in KIND_CATEGORY.items():
        by_category.setdefault(category, []).append(kind)
    return " OR ".join(
        f"(category = '{c}' AND kind IN ({_sql_list(kinds)}))" for c, kinds in by_category.items()
    )


def seed_config() -> None:
    """Insert ``notifications`` v1 only if the namespace has no rows yet (safe to re-run).
    created_by is NULL (a migration). The values are exactly docs/M6-DESIGN.md §2.5."""
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason) "
            "SELECT CAST(:namespace AS varchar), 1, CAST(:values AS jsonb), :reason "
            "WHERE NOT EXISTS "
            "(SELECT 1 FROM config_versions WHERE namespace = CAST(:namespace AS varchar))"
        ),
        {"namespace": NAMESPACE, "values": json.dumps(NOTIFICATIONS_V1), "reason": SEED_REASON},
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", _enum("notification_kind", *KIND_CATEGORY, length=40), nullable=False),
        sa.Column("category", _enum("notification_category", *CATEGORIES), nullable=False),
        sa.Column(
            "actor_ids",
            postgresql.ARRAY(sa.Uuid()),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column("actor_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=True),
        sa.Column("ref_id", sa.Uuid(), nullable=True),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        sa.Column("group_key", sa.String(length=200), nullable=True),
        _now("created_at"),
        _now("updated_at"),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_kind_category_sql(), name=op.f("ck_notifications_kind_category")),
        sa.CheckConstraint(
            f"(group_key IS NOT NULL) = (kind IN ({_sql_list(GROUPED_KINDS)}))",
            name=op.f("ck_notifications_group_key_iff_grouped"),
        ),
        sa.CheckConstraint(
            "actor_count >= 0 AND cardinality(actor_ids) <= actor_count",
            name=op.f("ck_notifications_actor_count_valid"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(data) = 'object'", name=op.f("ck_notifications_data_object")
        ),
        sa.CheckConstraint(
            "updated_at >= created_at", name=op.f("ck_notifications_updated_after_created")
        ),
        sa.CheckConstraint(
            "read_at IS NULL OR read_at >= created_at",
            name=op.f("ck_notifications_read_after_created"),
        ),
        _users_fk("notifications"),
        _deeds_fk("notifications"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe_key"),
    )
    op.create_index(
        "uq_notifications_unread_group",
        "notifications",
        ["user_id", "group_key"],
        unique=True,
        postgresql_where=sa.text("read_at IS NULL AND group_key IS NOT NULL"),
    )
    op.create_index(
        "ix_notifications_unread",
        "notifications",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index(
        "ix_notifications_user_updated_at",
        "notifications",
        ["user_id", sa.literal_column("updated_at DESC"), "id"],
        unique=False,
    )
    op.create_index("ix_notifications_updated_at", "notifications", ["updated_at"], unique=False)

    op.create_table(
        "notification_views",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        _users_fk("notification_views"),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_notification_views")),
    )

    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("category", _enum("notification_category", *CATEGORIES), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        _now("updated_at"),
        _users_fk("notification_preferences"),
        sa.PrimaryKeyConstraint("user_id", "category", name="pk_notification_preferences"),
    )

    op.create_table(
        "notification_mutes",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", _enum("mute_target", "person", "deed"), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        _now("created_at"),
        _users_fk("notification_mutes"),
        sa.PrimaryKeyConstraint(
            "user_id", "target_type", "target_id", name="pk_notification_mutes"
        ),
    )

    op.create_table(
        "notification_dedupe",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        _now("created_at"),
        _users_fk("notification_dedupe"),
        sa.PrimaryKeyConstraint("user_id", "key", name="pk_notification_dedupe"),
    )
    op.create_index(
        "ix_notification_dedupe_created_at", "notification_dedupe", ["created_at"], unique=False
    )

    op.create_table(
        "notification_fanouts",
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column("actor_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("cursor", sa.Uuid(), nullable=True),
        sa.Column("recipients", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "status",
            _enum("fanout_status", "running", "done", "capped"),
            server_default="running",
            nullable=False,
        ),
        _now("created_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "recipients >= 0", name=op.f("ck_notification_fanouts_recipients_non_negative")
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name=op.f("ck_notification_fanouts_finished_fields"),
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= created_at",
            name=op.f("ck_notification_fanouts_finished_after_created"),
        ),
        _deeds_fk("notification_fanouts"),
        sa.PrimaryKeyConstraint("deed_id", name=op.f("pk_notification_fanouts")),
    )
    op.create_index(
        "ix_notification_fanouts_running",
        "notification_fanouts",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'running'"),
    )

    op.create_table(
        "notification_daily_counts",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("category", _enum("notification_category", *CATEGORIES), nullable=False),
        sa.Column("count", sa.SmallInteger(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "count >= 0", name=op.f("ck_notification_daily_counts_count_non_negative")
        ),
        _users_fk("notification_daily_counts"),
        sa.PrimaryKeyConstraint("user_id", "day", "category", name="pk_notification_daily_counts"),
    )
    op.create_index(
        "ix_notification_daily_counts_day", "notification_daily_counts", ["day"], unique=False
    )

    seed_config()


def downgrade() -> None:
    """Downgrade schema."""
    # config_versions is append-only, so its trigger is bypassed for this namespace. Any later
    # published versions go too: before 0030 the namespace did not exist.
    op.execute("ALTER TABLE config_versions DISABLE TRIGGER config_versions_append_only")
    op.execute(f"DELETE FROM config_versions WHERE namespace = '{NAMESPACE}'")
    op.execute("ALTER TABLE config_versions ENABLE TRIGGER config_versions_append_only")
    # Indexes go with their tables.
    for table in reversed(TABLES):
        op.drop_table(table)
