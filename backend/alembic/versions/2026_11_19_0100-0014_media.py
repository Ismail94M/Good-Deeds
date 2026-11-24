"""media

M3 media module tables (docs/M3-DESIGN.md §2.2, D29 to D35, D42): ``photos``,
``photo_report_cases``, ``photo_reports`` and ``photo_decisions``. ``photo_decisions`` is
append-only (``forbid_modification()``). Seeds ``media`` v1 (§2.5); ``photos_enabled`` moves here
from the ``deeds`` namespace (D41, migration 0013).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-19 01:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES = ("photo_decisions",)
TABLES = ("photos", "photo_report_cases", "photo_reports", "photo_decisions")

NAMESPACE = "media"
SEED_REASON = "Initial M3 values (docs/M3-DESIGN.md §2.5; PO decisions Q32, Q39)"
MEDIA_V1: dict[str, Any] = {
    "photos_enabled": True,
    "photos_per_owner": 4,
    "photo_edit_window_hours": 24,
    "upload_allowed_types": ["image/jpeg", "image/png", "image/webp"],
    "upload_max_bytes": 10485760,
    "upload_url_ttl_seconds": 600,
    "pending_upload_ttl_hours": 24,
    "max_pending_uploads_per_user": 12,
    "max_image_pixels": 40000000,
    "min_image_side_px": 200,
    "variants": [
        {"name": "thumb", "max_side_px": 320},
        {"name": "display", "max_side_px": 1280},
    ],
    "webp_quality": 80,
    "blur_max_regions": 8,
    "blur_min_region": 0.02,
    "processing_max_attempts": 3,
    "processing_stuck_minutes": 10,
    "url_ttl_minutes": 60,
    "url_step_minutes": 30,
    "report_details_max": 500,
    "decision_note_max": 1000,
    "report_outcome_display_days": 30,
    "removed_photo_retention_days": 90,
    "rate_upload_user": "40/3600",
    "rate_upload_ip": "200/3600",
    "rate_photo_write_user": "60/3600",
    "rate_report_user": "10/3600",
    "rate_report_ip": "10/3600",
    "rate_report_ip_daily": "30/86400",
    "rate_admin_decision_user": "300/3600",
}


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "photos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("uploader_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "deed",
                "self_log",
                name="photo_purpose",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("deed_id", sa.Uuid(), nullable=True),
        sa.Column("self_log_id", sa.Uuid(), nullable=True),
        sa.Column("position", sa.SmallInteger(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending_upload",
                "uploaded",
                "queued",
                "processing",
                "ready",
                "rejected",
                "failed",
                "expired",
                "deleted",
                name="photo_status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "takedown",
            sa.Enum(
                "none",
                "admin_removed",
                "doer_removed",
                name="photo_takedown",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            server_default="none",
            nullable=False,
        ),
        sa.Column(
            "declared_type",
            sa.Enum(
                "image/jpeg",
                "image/png",
                "image/webp",
                name="photo_declared_type",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("declared_bytes", sa.Integer(), nullable=False),
        sa.Column("staging_key", sa.String(length=200), nullable=False),
        sa.Column("staging_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("variant_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column(
            "blur_regions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "reject_reason",
            sa.Enum(
                "unsupported_type",
                "too_large",
                "too_many_pixels",
                "animated",
                "corrupt",
                "size_mismatch",
                "removed_by_doer",
                name="photo_reject_reason",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=True,
        ),
        sa.Column("attempts", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("upload_ip_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("objects_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "object_move",
            sa.Enum(
                "hold",
                "release",
                name="photo_object_move",
                native_enum=False,
                create_constraint=True,
                length=10,
            ),
            nullable=True,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(deed_id IS NULL OR purpose = 'deed') AND (self_log_id IS NULL OR purpose = 'self_log')",
            name=op.f("ck_photos_owner_matches_purpose"),
        ),
        sa.CheckConstraint(
            "(status = 'deleted') = (deleted_at IS NOT NULL)", name=op.f("ck_photos_deleted_at")
        ),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_photos_content_sha256_hex"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(blur_regions) = 'array'", name=op.f("ck_photos_blur_regions_array")
        ),
        sa.CheckConstraint(
            "status <> 'ready' OR (variant_keys IS NOT NULL AND width IS NOT NULL AND height IS NOT NULL)",
            name=op.f("ck_photos_ready_has_variants"),
        ),
        sa.CheckConstraint(
            "takedown <> 'admin_removed' OR removed_at IS NOT NULL",
            name=op.f("ck_photos_removed_has_time"),
        ),
        sa.CheckConstraint(
            "takedown = 'none' OR purpose = 'deed'", name=op.f("ck_photos_takedown_deed_only")
        ),
        sa.CheckConstraint(
            "(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)",
            name=op.f("ck_photos_dimensions_positive"),
        ),
        sa.CheckConstraint(
            "NOT (deed_id IS NOT NULL AND self_log_id IS NOT NULL)",
            name=op.f("ck_photos_one_owner"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_photos_attempts_non_negative")),
        sa.CheckConstraint("declared_bytes > 0", name=op.f("ck_photos_declared_bytes_positive")),
        sa.CheckConstraint(
            "position IS NULL OR position >= 1", name=op.f("ck_photos_position_positive")
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"], ["deeds.id"], name=op.f("fk_photos_deed_id_deeds"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["self_log_id"],
            ["self_logs.id"],
            name=op.f("fk_photos_self_log_id_self_logs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["uploader_user_id"],
            ["users.id"],
            name=op.f("fk_photos_uploader_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_photos")),
    )
    op.create_index(
        "ix_photos_deed_position",
        "photos",
        ["deed_id", "position"],
        unique=False,
        postgresql_where=sa.text("deed_id IS NOT NULL"),
    )
    op.create_index(
        "ix_photos_object_move",
        "photos",
        ["object_move"],
        unique=False,
        postgresql_where=sa.text("object_move IS NOT NULL"),
    )
    op.create_index(
        "ix_photos_self_log_id",
        "photos",
        ["self_log_id"],
        unique=False,
        postgresql_where=sa.text("self_log_id IS NOT NULL"),
    )
    op.create_index(
        "ix_photos_unattached_status_created_at",
        "photos",
        ["status", "created_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending_upload', 'uploaded')"),
    )
    op.create_index(
        "ix_photos_unpurged_deleted_at",
        "photos",
        ["deleted_at"],
        unique=False,
        postgresql_where=sa.text("objects_purged_at IS NULL AND status = 'deleted'"),
    )
    op.create_index(
        "ix_photos_uploader_created_at",
        "photos",
        ["uploader_user_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_photos_work_status_started_at",
        "photos",
        ["status", "processing_started_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'processing')"),
    )
    op.create_index(
        "uq_photos_deed_position",
        "photos",
        ["deed_id", "position"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'processing', 'ready') AND takedown = 'none'"
        ),
    )
    op.create_index(
        "uq_photos_self_log_position",
        "photos",
        ["self_log_id", "position"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'processing', 'ready') AND takedown = 'none'"
        ),
    )
    op.create_table(
        "photo_report_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("photo_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "resolved",
                "closed",
                name="photo_report_case_status",
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
                name="photo_report_case_decision",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=True,
        ),
        sa.Column(
            "removal_reason",
            sa.Enum(
                "not_consented",
                "child_no_guardian_consent",
                "private_details",
                "inappropriate",
                "other",
                name="photo_report_case_removal_reason",
                native_enum=False,
                create_constraint=True,
                length=30,
            ),
            nullable=True,
        ),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(decision IS NOT DISTINCT FROM 'removed') = (removal_reason IS NOT NULL)",
            name=op.f("ck_photo_report_cases_removal_reason_iff_removed"),
        ),
        sa.CheckConstraint(
            "(status = 'resolved') = (decision IS NOT NULL) AND (decision IS NULL) = (decided_by IS NULL) AND (status <> 'resolved' OR resolved_at IS NOT NULL)",
            name=op.f("ck_photo_report_cases_resolved_has_decision"),
        ),
        sa.CheckConstraint(
            "priority BETWEEN 1 AND 3", name=op.f("ck_photo_report_cases_priority_range")
        ),
        sa.CheckConstraint(
            "report_count >= 0", name=op.f("ck_photo_report_cases_report_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_photo_report_cases_decided_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["photo_id"],
            ["photos.id"],
            name=op.f("fk_photo_report_cases_photo_id_photos"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_photo_report_cases")),
    )
    op.create_index(
        "ix_photo_report_cases_photo_resolved_at",
        "photo_report_cases",
        ["photo_id", sa.literal_column("resolved_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_photo_report_cases_queue",
        "photo_report_cases",
        ["status", "priority", "opened_at"],
        unique=False,
    )
    op.create_index(
        "uq_photo_report_cases_open",
        "photo_report_cases",
        ["photo_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "photo_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("photo_id", sa.Uuid(), nullable=False),
        sa.Column("reporter_user_id", sa.Uuid(), nullable=True),
        sa.Column("reporter_ip_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "not_consented",
                "child_no_guardian_consent",
                "private_details",
                "inappropriate",
                "other",
                name="photo_report_reason",
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
            ["photo_report_cases.id"],
            name=op.f("fk_photo_reports_case_id_photo_report_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["photo_id"],
            ["photos.id"],
            name=op.f("fk_photo_reports_photo_id_photos"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reporter_user_id"],
            ["users.id"],
            name=op.f("fk_photo_reports_reporter_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_photo_reports")),
    )
    op.create_index(
        "ix_photo_reports_case_created_at", "photo_reports", ["case_id", "created_at"], unique=False
    )
    op.create_index(
        "uq_photo_reports_case_member",
        "photo_reports",
        ["case_id", "reporter_user_id"],
        unique=True,
        postgresql_where=sa.text("reporter_user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_photo_reports_case_visitor",
        "photo_reports",
        ["case_id", "reporter_ip_hash"],
        unique=True,
        postgresql_where=sa.text("reporter_user_id IS NULL"),
    )
    op.create_table(
        "photo_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("photo_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column(
            "action",
            sa.Enum(
                "keep",
                "remove",
                "restore",
                name="photo_decision_action",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "removal_reason",
            sa.Enum(
                "not_consented",
                "child_no_guardian_consent",
                "private_details",
                "inappropriate",
                "other",
                name="photo_decision_removal_reason",
                native_enum=False,
                create_constraint=True,
                length=30,
            ),
            nullable=True,
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
            "(action = 'remove') = (removal_reason IS NOT NULL)",
            name=op.f("ck_photo_decisions_removal_reason_iff_remove"),
        ),
        sa.CheckConstraint(
            "length(btrim(note)) >= 5", name=op.f("ck_photo_decisions_note_min_length")
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_photo_decisions_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["photo_report_cases.id"],
            name=op.f("fk_photo_decisions_case_id_photo_report_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["photo_id"],
            ["photos.id"],
            name=op.f("fk_photo_decisions_photo_id_photos"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_photo_decisions")),
    )
    op.create_index(
        "ix_photo_decisions_photo_created_at",
        "photo_decisions",
        ["photo_id", "created_at"],
        unique=False,
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
        )
    # Safe to re-run: v1 is inserted only if the namespace has no rows yet.
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason) "
            "SELECT CAST(:namespace AS varchar), 1, CAST(:values AS jsonb), :reason "
            "WHERE NOT EXISTS "
            "(SELECT 1 FROM config_versions WHERE namespace = CAST(:namespace AS varchar))"
        ),
        {"namespace": NAMESPACE, "values": json.dumps(MEDIA_V1), "reason": SEED_REASON},
    )


def downgrade() -> None:
    """Downgrade schema."""
    # config_versions is append-only, so its trigger is bypassed for this one namespace. Any
    # later published media versions go too: before 0014 the namespace did not exist.
    op.execute("ALTER TABLE config_versions DISABLE TRIGGER config_versions_append_only")
    op.execute(f"DELETE FROM config_versions WHERE namespace = '{NAMESPACE}'")
    op.execute("ALTER TABLE config_versions ENABLE TRIGGER config_versions_append_only")
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    # Indexes go with their tables.
    for table in reversed(TABLES):
        op.drop_table(table)
