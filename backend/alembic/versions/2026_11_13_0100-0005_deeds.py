"""deeds

Deeds module tables (docs/M2-DESIGN.md §2.2 to §2.7): ``qr_scans``, ``same_act_groups``,
``deeds``, ``invites``, ``verification_events``, ``deed_revisions`` and ``self_logs``.
``verification_events`` and ``deed_revisions`` are append-only (``forbid_modification()``).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-13 01:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES = ("verification_events", "deed_revisions")


def _point() -> Geography:
    # GiST indexes are created explicitly below, so the type must not add its own.
    return Geography(geometry_type="POINT", srid=4326, spatial_index=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "qr_scans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("witness_user_id", sa.Uuid(), nullable=False),
        sa.Column("doer_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "scan_method",
            sa.Enum(
                "qr",
                "manual_code",
                name="scan_method",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("token_window", sa.BigInteger(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scan_day", sa.Date(), nullable=False),
        sa.Column("location", _point(), nullable=False),
        sa.Column("accuracy_m", sa.Integer(), nullable=False),
        sa.Column("device_hash", sa.String(length=64), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("limits_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("accuracy_m > 0", name=op.f("ck_qr_scans_accuracy_positive")),
        sa.CheckConstraint("doer_user_id <> witness_user_id", name=op.f("ck_qr_scans_not_self")),
        sa.ForeignKeyConstraint(
            ["doer_user_id"],
            ["users.id"],
            name=op.f("fk_qr_scans_doer_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["witness_user_id"],
            ["users.id"],
            name=op.f("fk_qr_scans_witness_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qr_scans")),
        sa.UniqueConstraint(
            "doer_user_id",
            "token_window",
            "witness_user_id",
            name="uq_qr_scans_doer_window_witness",
        ),
        sa.UniqueConstraint(
            "doer_user_id", "witness_user_id", "scan_day", name="uq_qr_scans_doer_witness_day"
        ),
    )
    op.create_index(
        "ix_qr_scans_doer_scanned_at",
        "qr_scans",
        ["doer_user_id", sa.literal_column("scanned_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_qr_scans_location", "qr_scans", ["location"], unique=False, postgresql_using="gist"
    )
    op.create_index(
        "ix_qr_scans_unconsumed_expires_at",
        "qr_scans",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )
    op.create_index(
        "ix_qr_scans_witness_scanned_at",
        "qr_scans",
        ["witness_user_id", sa.literal_column("scanned_at DESC")],
        unique=False,
    )
    op.create_table(
        "same_act_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("doer_user_id", sa.Uuid(), nullable=False),
        sa.Column("centre", _point(), nullable=True),
        sa.Column("anchor_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("radius_m", sa.Integer(), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False),
        sa.Column("cap", sa.Integer(), nullable=False),
        sa.Column("verified_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("counted_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("distinct_reporters", sa.Integer(), server_default="0", nullable=False),
        sa.Column("limits_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "counted_count <= cap AND counted_count <= verified_count",
            name=op.f("ck_same_act_groups_counted_within_cap"),
        ),
        sa.CheckConstraint(
            "radius_m > 0 AND window_minutes > 0 AND cap > 0",
            name=op.f("ck_same_act_groups_parameters_positive"),
        ),
        sa.CheckConstraint(
            "verified_count >= 0 AND counted_count >= 0 AND distinct_reporters >= 0 AND distinct_reporters <= verified_count",
            name=op.f("ck_same_act_groups_counters_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["doer_user_id"],
            ["users.id"],
            name=op.f("fk_same_act_groups_doer_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_same_act_groups")),
    )
    op.create_index(
        "ix_same_act_groups_centre",
        "same_act_groups",
        ["centre"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_index(
        "ix_same_act_groups_doer_anchor_at",
        "same_act_groups",
        ["doer_user_id", "anchor_at"],
        unique=False,
    )
    op.create_table(
        "self_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location", _point(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_self_logs_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_self_logs")),
    )
    op.create_index(
        "ix_self_logs_user_created_at",
        "self_logs",
        ["user_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "deeds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reporter_user_id", sa.Uuid(), nullable=False),
        sa.Column("doer_user_id", sa.Uuid(), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("beneficiaries_count", sa.Integer(), nullable=True),
        sa.Column("location", _point(), nullable=True),
        sa.Column("location_accuracy_m", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "reported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("report_day", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "verified",
                "unverified",
                "rejected",
                "removed",
                "withdrawn",
                name="deed_status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "verification_method",
            sa.Enum(
                "qr_scan",
                "invite_accept",
                name="verification_method",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scan_id", sa.Uuid(), nullable=True),
        sa.Column("same_act_group_id", sa.Uuid(), nullable=True),
        sa.Column("counts_for_points", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("prize_rank_eligible", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("limits_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'withdrawn') = (withdrawn_at IS NOT NULL)",
            name=op.f("ck_deeds_withdrawn_at"),
        ),
        sa.CheckConstraint(
            "status <> 'pending' OR (verification_method = 'invite_accept' AND doer_user_id IS NULL)",
            name=op.f("ck_deeds_pending_is_invite"),
        ),
        sa.CheckConstraint(
            "status <> 'verified' OR (doer_user_id IS NOT NULL AND verified_at IS NOT NULL AND same_act_group_id IS NOT NULL)",
            name=op.f("ck_deeds_verified_has_doer"),
        ),
        sa.CheckConstraint(
            "verification_method <> 'qr_scan' OR location IS NOT NULL",
            name=op.f("ck_deeds_qr_has_location"),
        ),
        sa.CheckConstraint(
            "verification_method <> 'qr_scan' OR scan_id IS NOT NULL",
            name=op.f("ck_deeds_qr_has_scan"),
        ),
        sa.CheckConstraint(
            "NOT counts_for_points OR same_act_group_id IS NOT NULL",
            name=op.f("ck_deeds_counts_needs_group"),
        ),
        sa.CheckConstraint(
            "beneficiaries_count IS NULL OR beneficiaries_count BETWEEN 1 AND 10000",
            name=op.f("ck_deeds_beneficiaries_range"),
        ),
        sa.CheckConstraint(
            "doer_user_id IS NULL OR doer_user_id <> reporter_user_id",
            name=op.f("ck_deeds_not_self"),
        ),
        sa.CheckConstraint(
            "location_accuracy_m IS NULL OR location_accuracy_m > 0",
            name=op.f("ck_deeds_location_accuracy_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["doer_user_id"],
            ["users.id"],
            name=op.f("fk_deeds_doer_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reporter_user_id"],
            ["users.id"],
            name=op.f("fk_deeds_reporter_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["same_act_group_id"],
            ["same_act_groups.id"],
            name=op.f("fk_deeds_same_act_group_id_same_act_groups"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["qr_scans.id"],
            name=op.f("fk_deeds_scan_id_qr_scans"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deeds")),
        sa.UniqueConstraint("scan_id", name=op.f("uq_deeds_scan_id")),
    )
    op.create_index(
        "ix_deeds_doer_occurred_at", "deeds", ["doer_user_id", "occurred_at"], unique=False
    )
    op.create_index(
        "ix_deeds_doer_verified",
        "deeds",
        ["doer_user_id"],
        unique=False,
        postgresql_where=sa.text("status = 'verified'"),
    )
    op.create_index(
        "ix_deeds_location", "deeds", ["location"], unique=False, postgresql_using="gist"
    )
    op.create_index(
        "ix_deeds_reporter_reported_at",
        "deeds",
        ["reporter_user_id", sa.literal_column("reported_at DESC")],
        unique=False,
    )
    op.create_index("ix_deeds_same_act_group_id", "deeds", ["same_act_group_id"], unique=False)
    op.create_index(
        "uq_deeds_pair_day",
        "deeds",
        ["doer_user_id", "reporter_user_id", "report_day"],
        unique=True,
        postgresql_where=sa.text("doer_user_id IS NOT NULL"),
    )
    op.create_table(
        "deed_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column("editor_user_id", sa.Uuid(), nullable=False),
        sa.Column("old_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("new_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"],
            ["deeds.id"],
            name=op.f("fk_deed_revisions_deed_id_deeds"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["editor_user_id"],
            ["users.id"],
            name=op.f("fk_deed_revisions_editor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deed_revisions")),
    )
    op.create_index(
        "ix_deed_revisions_deed_id_created_at",
        "deed_revisions",
        ["deed_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "invites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column("reporter_user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "sent_via",
            sa.Enum(
                "whatsapp",
                "share_sheet",
                "copy_link",
                "other",
                name="invite_sent_via",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "accepted",
                "expired",
                "cancelled",
                name="invite_status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("shared_device", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_day", sa.Date(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reissued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.Uuid(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_phone_hash", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "(status = 'accepted') = (accepted_by IS NOT NULL AND accepted_at IS NOT NULL)",
            name=op.f("ck_invites_accepted_together"),
        ),
        sa.CheckConstraint(
            "accepted_by IS NULL OR accepted_by <> reporter_user_id",
            name=op.f("ck_invites_not_self"),
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by"],
            ["users.id"],
            name=op.f("fk_invites_accepted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"], ["deeds.id"], name=op.f("fk_invites_deed_id_deeds"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reporter_user_id"],
            ["users.id"],
            name=op.f("fk_invites_reporter_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invites")),
        sa.UniqueConstraint("deed_id", name=op.f("uq_invites_deed_id")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invites_token_hash")),
    )
    op.create_index(
        "ix_invites_pending_expires_at",
        "invites",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_invites_reporter_created_day",
        "invites",
        ["reporter_user_id", "created_day"],
        unique=False,
    )
    op.create_index(
        "ix_invites_shared_device_pair",
        "invites",
        ["reporter_user_id", "accepted_by", "accepted_at"],
        unique=False,
        postgresql_where=sa.text("shared_device"),
    )
    op.create_table(
        "verification_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "qr_verified",
                "invite_accepted",
                "invite_expired",
                "withdrawn",
                "rejected",
                name="verification_event_kind",
                native_enum=False,
                create_constraint=True,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("scan_id", sa.Uuid(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"],
            ["deeds.id"],
            name=op.f("fk_verification_events_deed_id_deeds"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["qr_scans.id"],
            name=op.f("fk_verification_events_scan_id_qr_scans"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_events")),
    )
    op.create_index(
        "ix_verification_events_deed_id", "verification_events", ["deed_id"], unique=False
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.drop_index("ix_verification_events_deed_id", table_name="verification_events")
    op.drop_table("verification_events")
    op.drop_index(
        "ix_invites_shared_device_pair",
        table_name="invites",
        postgresql_where=sa.text("shared_device"),
    )
    op.drop_index("ix_invites_reporter_created_day", table_name="invites")
    op.drop_index(
        "ix_invites_pending_expires_at",
        table_name="invites",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("invites")
    op.drop_index("ix_deed_revisions_deed_id_created_at", table_name="deed_revisions")
    op.drop_table("deed_revisions")
    op.drop_index(
        "uq_deeds_pair_day",
        table_name="deeds",
        postgresql_where=sa.text("doer_user_id IS NOT NULL"),
    )
    op.drop_index("ix_deeds_same_act_group_id", table_name="deeds")
    op.drop_index("ix_deeds_reporter_reported_at", table_name="deeds")
    op.drop_index("ix_deeds_location", table_name="deeds", postgresql_using="gist")
    op.drop_index(
        "ix_deeds_doer_verified",
        table_name="deeds",
        postgresql_where=sa.text("status = 'verified'"),
    )
    op.drop_index("ix_deeds_doer_occurred_at", table_name="deeds")
    op.drop_table("deeds")
    op.drop_index("ix_self_logs_user_created_at", table_name="self_logs")
    op.drop_table("self_logs")
    op.drop_index("ix_same_act_groups_doer_anchor_at", table_name="same_act_groups")
    op.drop_index(
        "ix_same_act_groups_centre", table_name="same_act_groups", postgresql_using="gist"
    )
    op.drop_table("same_act_groups")
    op.drop_index("ix_qr_scans_witness_scanned_at", table_name="qr_scans")
    op.drop_index(
        "ix_qr_scans_unconsumed_expires_at",
        table_name="qr_scans",
        postgresql_where=sa.text("consumed_at IS NULL"),
    )
    op.drop_index("ix_qr_scans_location", table_name="qr_scans", postgresql_using="gist")
    op.drop_index("ix_qr_scans_doer_scanned_at", table_name="qr_scans")
    op.drop_table("qr_scans")
