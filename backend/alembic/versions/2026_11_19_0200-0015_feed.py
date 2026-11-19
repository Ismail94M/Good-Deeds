"""feed

M3 feed module tables (docs/M3-DESIGN.md §2.3, D36 to D38, D40, D45): the ``feed_posts``
projection (generated ``listed``), ``feed_likes``, ``feed_comments``, ``comment_report_cases``,
``comment_reports``, ``comment_decisions`` (append-only, ``forbid_modification()``, Q38),
``feed_saves``, ``feed_shares``, ``follows``, ``feed_notices_seen`` (Q41), ``feed_top_snapshots``
(per period, Q26) and ``feed_top_entries``. Seeds ``feed`` v1 (§2.5) and backfills
``feed_posts`` from verified deeds.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-19 02:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES = ("comment_decisions",)
TABLES = (
    "feed_posts",
    "feed_likes",
    "feed_comments",
    "comment_report_cases",
    "comment_reports",
    "comment_decisions",
    "feed_saves",
    "feed_shares",
    "follows",
    "feed_notices_seen",
    "feed_top_snapshots",
    "feed_top_entries",
)

NAMESPACE = "feed"
SEED_REASON = (
    "Initial M3 values (docs/M3-DESIGN.md §2.5; PO decisions Q26, Q35, Q37, Q38, Q43). "
    "The English blocked_terms list is a starter list for PO review before launch"
)
# Starter list (Q35): short, whole-token or phrase matches; words that are common in
# descriptions of good deeds ("trash", "useless", "jerk", "show off") are left out on purpose.
EN_BLOCKED_TERMS = [
    "idiot",
    "stupid",
    "moron",
    "dumb",
    "dumbass",
    "loser",
    "shut up",
    "ugly",
    "pathetic",
    "worthless",
    "disgusting",
    "retard",
    "bastard",
    "bitch",
    "asshole",
    "fuck",
    "fucking",
    "shit",
    "bullshit",
    "scum",
    "kill yourself",
    "kys",
    "go die",
    "hate you",
    "nobody cares",
]
FEED_V1: dict[str, Any] = {
    "comment_max_chars": 300,
    "comment_block_links": True,
    "comment_block_phone_numbers": True,
    "comment_filter_enabled_languages": ["en"],
    "comment_filter_languages": {
        "en": {
            "blocked_terms": EN_BLOCKED_TERMS,
            "substitutions": {
                "0": "o",
                "1": "i",
                "3": "e",
                "4": "a",
                "5": "s",
                "7": "t",
                "@": "a",
                "$": "s",
            },
            "keep_marks": [],
            "squashed_min_letters": 5,
        }
    },
    "deleted_comment_retention_days": 30,
    "top_week_days": 7,
    "top_month_days": 30,
    "top_year_days": 365,
    "top_like_weight": 1,
    "top_comment_weight": 3,
    "top_size": 50,
    "top_badge_ranks": 3,
    "top_week_refresh_minutes": 10,
    "top_month_refresh_minutes": 30,
    "top_year_refresh_minutes": 60,
    "max_following": 2000,
    "rate_comment_user": "5/60",
    "rate_comment_user_daily": "100/86400",
    "rate_comment_ip": "60/3600",
    "rate_comment_report_user": "10/3600",
    "rate_like_user": "120/3600",
    "rate_like_ip": "600/3600",
    "rate_save_user": "120/3600",
    "rate_follow_user": "60/3600",
    "rate_follow_ip": "300/3600",
    "rate_hide_user": "30/86400",
    "rate_share_user": "60/3600",
    "rate_public_deed_ip": "120/900",
    "rate_feed_read_user": "600/900",
}


def backfill_feed_posts() -> None:
    """One published post per verified deed (M2 data is dev-only, but this keeps local
    databases consistent). Safe to re-run: existing posts are left alone."""
    op.get_bind().execute(
        sa.text(
            "INSERT INTO feed_posts (deed_id, doer_id, reporter_id, published_at, state, synced_at) "
            "SELECT id, doer_user_id, reporter_user_id, verified_at, 'published', now() "
            "FROM deeds WHERE status = 'verified' "
            "ON CONFLICT (deed_id) DO NOTHING"
        )
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "feed_posts",
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column("doer_id", sa.Uuid(), nullable=False),
        sa.Column("reporter_id", sa.Uuid(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "published",
                "withdrawn",
                "rejected",
                "removed",
                "held",
                name="post_state",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("hidden_by_doer_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hidden_by_reporter_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "listed",
            sa.Boolean(),
            sa.Computed(
                "state = 'published' AND hidden_by_doer_at IS NULL AND hidden_by_reporter_at IS NULL",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("like_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("top_like_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("comment_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "comment_count >= 0", name=op.f("ck_feed_posts_comment_count_non_negative")
        ),
        sa.CheckConstraint("doer_id <> reporter_id", name=op.f("ck_feed_posts_not_self")),
        sa.CheckConstraint("like_count >= 0", name=op.f("ck_feed_posts_like_count_non_negative")),
        sa.CheckConstraint(
            "top_like_count >= 0 AND top_like_count <= like_count",
            name=op.f("ck_feed_posts_top_like_count_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"], ["deeds.id"], name=op.f("fk_feed_posts_deed_id_deeds"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["doer_id"], ["users.id"], name=op.f("fk_feed_posts_doer_id_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reporter_id"],
            ["users.id"],
            name=op.f("fk_feed_posts_reporter_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("deed_id", name=op.f("pk_feed_posts")),
    )
    op.create_index(
        "ix_feed_posts_doer_listed",
        "feed_posts",
        ["doer_id", sa.literal_column("published_at DESC"), sa.literal_column("deed_id DESC")],
        unique=False,
        postgresql_where=sa.text("listed"),
    )
    op.create_index(
        "ix_feed_posts_listed",
        "feed_posts",
        [sa.literal_column("published_at DESC"), sa.literal_column("deed_id DESC")],
        unique=False,
        postgresql_where=sa.text("listed"),
    )
    op.create_index(
        "ix_feed_posts_reporter_listed",
        "feed_posts",
        ["reporter_id", sa.literal_column("published_at DESC"), sa.literal_column("deed_id DESC")],
        unique=False,
        postgresql_where=sa.text("listed"),
    )
    op.create_table(
        "feed_likes",
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"],
            ["feed_posts.deed_id"],
            name=op.f("fk_feed_likes_deed_id_feed_posts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_feed_likes_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("deed_id", "user_id", name="pk_feed_likes"),
    )
    op.create_index(
        "ix_feed_likes_user_created_at",
        "feed_likes",
        ["user_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "feed_comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column(
            "deleted_role",
            sa.Enum(
                "author",
                "doer",
                "reporter",
                "moderation",
                name="comment_deleted_role",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=True,
        ),
        sa.Column("removal_notice_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "deleted_role IS DISTINCT FROM 'author' OR body IS NULL",
            name=op.f("ck_feed_comments_author_delete_erases"),
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL) = (deleted_by IS NULL)",
            name=op.f("ck_feed_comments_deleted_by_set"),
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL) = (deleted_role IS NULL)",
            name=op.f("ck_feed_comments_deleted_role_set"),
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_feed_comments_author_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"],
            ["feed_posts.deed_id"],
            name=op.f("fk_feed_comments_deed_id_feed_posts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feed_comments")),
    )
    op.create_index(
        "ix_feed_comments_author_created_at",
        "feed_comments",
        ["author_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_feed_comments_deed_live",
        "feed_comments",
        ["deed_id", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_feed_comments_erasable_deleted_at",
        "feed_comments",
        ["deleted_at"],
        unique=False,
        postgresql_where=sa.text("body IS NOT NULL AND deleted_at IS NOT NULL"),
    )
    op.create_table(
        "comment_report_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "resolved",
                "closed",
                name="comment_report_case_status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("report_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "decision",
            sa.Enum(
                "kept",
                "removed",
                name="comment_report_case_decision",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=True,
        ),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(status = 'resolved') = (decision IS NOT NULL) AND (decision IS NULL) = (decided_by IS NULL) AND (status <> 'resolved' OR resolved_at IS NOT NULL)",
            name=op.f("ck_comment_report_cases_resolved_has_decision"),
        ),
        sa.CheckConstraint(
            "priority BETWEEN 1 AND 3", name=op.f("ck_comment_report_cases_priority_range")
        ),
        sa.CheckConstraint(
            "report_count >= 0", name=op.f("ck_comment_report_cases_report_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["feed_comments.id"],
            name=op.f("fk_comment_report_cases_comment_id_feed_comments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_comment_report_cases_decided_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"],
            ["feed_posts.deed_id"],
            name=op.f("fk_comment_report_cases_deed_id_feed_posts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comment_report_cases")),
    )
    op.create_index(
        "ix_comment_report_cases_comment_resolved_at",
        "comment_report_cases",
        ["comment_id", sa.literal_column("resolved_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_comment_report_cases_queue",
        "comment_report_cases",
        ["status", "priority", "opened_at"],
        unique=False,
    )
    op.create_index(
        "uq_comment_report_cases_open",
        "comment_report_cases",
        ["comment_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "comment_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.Uuid(), nullable=False),
        sa.Column("reporter_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "threat_or_harassment",
                "hate",
                "personal_info",
                "spam",
                "other",
                name="comment_report_reason",
                native_enum=False,
                create_constraint=True,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("details", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["comment_report_cases.id"],
            name=op.f("fk_comment_reports_case_id_comment_report_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["feed_comments.id"],
            name=op.f("fk_comment_reports_comment_id_feed_comments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reporter_user_id"],
            ["users.id"],
            name=op.f("fk_comment_reports_reporter_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comment_reports")),
        sa.UniqueConstraint("case_id", "reporter_user_id", name="uq_comment_reports_case_reporter"),
    )
    op.create_index(
        "ix_comment_reports_case_created_at",
        "comment_reports",
        ["case_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "comment_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column(
            "action",
            sa.Enum(
                "keep",
                "remove",
                "restore",
                name="comment_decision_action",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(note)) >= 5", name=op.f("ck_comment_decisions_note_min_length")
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_comment_decisions_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["comment_report_cases.id"],
            name=op.f("fk_comment_decisions_case_id_comment_report_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["feed_comments.id"],
            name=op.f("fk_comment_decisions_comment_id_feed_comments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comment_decisions")),
    )
    op.create_index(
        "ix_comment_decisions_comment_created_at",
        "comment_decisions",
        ["comment_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "feed_saves",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"],
            ["feed_posts.deed_id"],
            name=op.f("fk_feed_saves_deed_id_feed_posts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_feed_saves_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("user_id", "deed_id", name="pk_feed_saves"),
    )
    op.create_index(
        "ix_feed_saves_user_created_at",
        "feed_saves",
        ["user_id", sa.literal_column("created_at DESC"), sa.literal_column("deed_id DESC")],
        unique=False,
    )
    op.create_table(
        "feed_shares",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "target",
            sa.Enum(
                "whatsapp",
                "copy_link",
                "web_share",
                name="share_target",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"],
            ["feed_posts.deed_id"],
            name=op.f("fk_feed_shares_deed_id_feed_posts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_feed_shares_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feed_shares")),
    )
    op.create_index("ix_feed_shares_deed_id", "feed_shares", ["deed_id"], unique=False)
    op.create_table(
        "follows",
        sa.Column("follower_id", sa.Uuid(), nullable=False),
        sa.Column("followed_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("follower_id <> followed_id", name=op.f("ck_follows_not_self")),
        sa.ForeignKeyConstraint(
            ["followed_id"],
            ["users.id"],
            name=op.f("fk_follows_followed_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["follower_id"],
            ["users.id"],
            name=op.f("fk_follows_follower_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("follower_id", "followed_id", name="pk_follows"),
    )
    op.create_index(
        "ix_follows_followed_created_at",
        "follows",
        ["followed_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_follows_follower_created_at",
        "follows",
        ["follower_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "feed_notices_seen",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("notice_key", sa.String(length=40), nullable=False),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_feed_notices_seen_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", "notice_key", name="pk_feed_notices_seen"),
    )
    op.create_table(
        "feed_top_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "period",
            sa.Enum(
                "week",
                "month",
                "year",
                name="top_period",
                native_enum=False,
                create_constraint=True,
                length=10,
            ),
            nullable=False,
        ),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("weights", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feed_top_snapshots")),
    )
    op.create_index(
        "ix_feed_top_snapshots_period_computed_at",
        "feed_top_snapshots",
        ["period", sa.literal_column("computed_at DESC")],
        unique=False,
    )
    op.create_table(
        "feed_top_entries",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.CheckConstraint("rank >= 1", name=op.f("ck_feed_top_entries_rank_positive")),
        sa.ForeignKeyConstraint(
            ["deed_id"],
            ["feed_posts.deed_id"],
            name=op.f("fk_feed_top_entries_deed_id_feed_posts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["feed_top_snapshots.id"],
            name=op.f("fk_feed_top_entries_snapshot_id_feed_top_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "rank", name="pk_feed_top_entries"),
        sa.UniqueConstraint("snapshot_id", "deed_id", name="uq_feed_top_entries_snapshot_deed"),
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
        )
    bind = op.get_bind()
    # Safe to re-run: v1 is inserted only if the namespace has no rows yet.
    bind.execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason) "
            "SELECT CAST(:namespace AS varchar), 1, CAST(:values AS jsonb), :reason "
            "WHERE NOT EXISTS "
            "(SELECT 1 FROM config_versions WHERE namespace = CAST(:namespace AS varchar))"
        ),
        {"namespace": NAMESPACE, "values": json.dumps(FEED_V1), "reason": SEED_REASON},
    )
    backfill_feed_posts()


def downgrade() -> None:
    """Downgrade schema."""
    # config_versions is append-only, so its trigger is bypassed for this one namespace. Any
    # later published feed versions go too: before 0015 the namespace did not exist.
    op.execute("ALTER TABLE config_versions DISABLE TRIGGER config_versions_append_only")
    op.execute(f"DELETE FROM config_versions WHERE namespace = '{NAMESPACE}'")
    op.execute("ALTER TABLE config_versions ENABLE TRIGGER config_versions_append_only")
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    # Indexes go with their tables.
    for table in reversed(TABLES):
        op.drop_table(table)
