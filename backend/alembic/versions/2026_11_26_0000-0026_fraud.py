"""fraud

M5 DB10 (docs/M5-DESIGN.md §2.1, §2.4, D77 to D102; revision 4, PO approved 2026-11-26): the
``fraud`` module tables and the ``fraud`` v1 config.

Tables and the invariants the database enforces:

- ``flag_cases``: partial unique ``uq_flag_cases_live (type, subject_key) WHERE status IN
  ('open', 'parked')`` (D81: one live case per type and subject set); CHECK ``resolved_fields``
  (``status = 'resolved'`` exactly when outcome, resolved_at and resolved_by are set). Trigger
  ``flag_cases_guard``: ``id``, ``type``, ``subject_key``, ``opened_at`` and
  ``detector_config_version`` never change, a resolved case never changes again (D88: never
  reopened), rows are never deleted. Indexes: the queue ``(status, review_by, opened_at)``
  (ascending b-tree order already puts NULL ``review_by`` last), ``(type, status)``,
  ``(type, subject_key)`` (D81 step 2, earlier dismissed cases) and the resolved tab.
- ``flag_case_users``, ``flag_case_deeds``: insert-only (``forbid_modification()``).
- ``prize_holds``: unique per (deed, case); release set once with a reason
  (``released_on_confirm``, ``appeal_overturned``); partial index on unreleased holds for
  ``is_held``.
- ``flag_case_decisions`` (actions ``park``, ``unpark``, ``request_explanation``, ``confirm``,
  ``dismiss``; note required) and ``flag_case_deed_outcomes`` (``exclude``, ``reject``,
  ``release``; one per case and deed, only for a deed of the case): append-only.
- ``explanation_requests``: one per (case, subject); the reply is set once and not after
  ``reply_due_at`` (D99).
- ``decision_notices``: one per person per confirmed case or per override; ``final_at`` set once
  (D100). A case notice is for a subject of the case.
- ``appeals``: one per notice, by the person the notice is for; decided once.
- ``fraud_detector_runs``: one row per sweep or CLI run (D82); updated while it runs.
- ``review_views``: when each member last opened *Profile -> Reviews* (the unread dot,
  ``POST /api/reviews/seen``); one row per user, upserted, no append-only trigger.

``prize_holds``, ``explanation_requests``, ``decision_notices`` and ``appeals`` share the trigger
function ``forbid_except_set_once(marker, other columns...)``: no DELETE, and an UPDATE must set
the ``marker`` column from NULL and may change only the listed columns.

Downgrade drops the triggers, tables and functions and the ``fraud`` config namespace.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-26 00:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# In creation order (downgrade drops them in reverse).
TABLES = (
    "fraud_detector_runs",
    "flag_cases",
    "flag_case_users",
    "flag_case_deeds",
    "prize_holds",
    "flag_case_decisions",
    "flag_case_deed_outcomes",
    "explanation_requests",
    "decision_notices",
    "appeals",
    "review_views",
)
APPEND_ONLY_TABLES = (
    "flag_case_users",
    "flag_case_deeds",
    "flag_case_decisions",
    "flag_case_deed_outcomes",
)

NAMESPACE = "fraud"
FRAUD_REASON = (
    "Initial M5 values (docs/M5-DESIGN.md §2.4, revision 4; PO answers Q59 to Q78, 2026-11-26)"
)
FRAUD_V1: dict[str, Any] = {
    "timezone": "Asia/Kolkata",
    "detectors_enabled": {
        "reciprocal": True,
        "ring": True,
        "concentration": True,
        "invite_anomaly": False,
        "content": True,
    },
    # PO principle 1 (D80, Q60, Q73): opening a case never holds anything.
    "hold_on_open": {
        "reciprocal": False,
        "ring": False,
        "concentration": False,
        "invite_anomaly": False,
        "content": False,
    },
    "explanation": {"reply_days": 14, "reply_max_chars": 2000},
    "appeal": {"window_days": 30, "reason_min_chars": 10, "reason_max_chars": 2000},
    "member_reason_labels": {
        "case": (
            "These recognitions didn't meet our rule that recognitions should come from "
            "independent witnesses."
        ),
        "fake_photo": "The photo didn't show this deed.",
        "rule_misfire": "Points corrected.",
        "fraud_confirmed": (
            "These recognitions didn't meet our rule that recognitions should come from "
            "independent witnesses."
        ),
        "other": "Points changed after review.",
    },
    "reciprocal": {"window_days": 30, "min_deeds_each_way": 1},
    "ring": {
        "window_days": 30,
        "min_size": 3,
        "max_size": 5,
        "min_edge_deeds": 1,
        "min_internal_share_pct": 50,
        "max_search_edges": 5000,
    },
    "concentration": {"max_reporter_share_pct": 30, "min_doer_points": 50},
    # Typed now for the deferred invite-anomaly detector (D87); nothing reads them in M5.
    "invite_anomaly": {
        "shared_device_accepts_per_pair_month": 3,
        "new_account_hours": 24,
        "new_account_accepts_per_reporter_30d": 3,
        "accept_within_minutes": 5,
    },
    "coi_lookback_days": 90,
    "note_min_chars": 10,
    "note_max_chars": 2000,
    "override_max_points": 50,
    "override_second_admin": {"max_cut_points": 10, "cut_to_zero": True, "expire_days": 14},
    "override_reason_codes": ["fake_photo", "rule_misfire", "fraud_confirmed", "other"],
    "page_size": 50,
    "rate_admin_read": "600/60",
    "rate_admin_write": "60/60",
}

_FUNCTIONS: dict[str, str] = {
    "flag_cases_guard": """
CREATE FUNCTION flag_cases_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'flag_cases rows are never deleted';
    END IF;
    IF OLD.status = 'resolved' THEN
        RAISE EXCEPTION 'a resolved flag case never changes';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.type IS DISTINCT FROM OLD.type
       OR NEW.subject_key IS DISTINCT FROM OLD.subject_key
       OR NEW.opened_at IS DISTINCT FROM OLD.opened_at
       OR NEW.detector_config_version IS DISTINCT FROM OLD.detector_config_version THEN
        RAISE EXCEPTION 'flag_cases: type, subject_key, opened_at and the config version never change';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    "forbid_except_set_once": """
CREATE FUNCTION forbid_except_set_once() RETURNS trigger AS $$
DECLARE
    -- TG_ARGV[0] is the marker column; every argument is a column the update may change.
    allowed text[] := TG_ARGV;
    marker text := TG_ARGV[0];
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '% rows are never deleted', TG_TABLE_NAME;
    END IF;
    IF (to_jsonb(OLD) ->> marker) IS NOT NULL OR (to_jsonb(NEW) ->> marker) IS NULL
       OR (to_jsonb(NEW) - allowed) IS DISTINCT FROM (to_jsonb(OLD) - allowed) THEN
        RAISE EXCEPTION '% rows may only set % once', TG_TABLE_NAME,
            array_to_string(allowed, ', ');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
}

# (trigger name, timing and events, table, function call)
_TRIGGERS: tuple[tuple[str, str, str, str], ...] = (
    *(
        (f"{t}_append_only", "BEFORE UPDATE OR DELETE", t, "forbid_modification()")
        for t in APPEND_ONLY_TABLES
    ),
    ("flag_cases_guard", "BEFORE UPDATE OR DELETE", "flag_cases", "flag_cases_guard()"),
    (
        "prize_holds_release_once",
        "BEFORE UPDATE OR DELETE",
        "prize_holds",
        "forbid_except_set_once('released_at', 'release_reason')",
    ),
    (
        "explanation_requests_reply_once",
        "BEFORE UPDATE OR DELETE",
        "explanation_requests",
        "forbid_except_set_once('replied_at', 'reply')",
    ),
    (
        "decision_notices_final_once",
        "BEFORE UPDATE OR DELETE",
        "decision_notices",
        "forbid_except_set_once('final_at')",
    ),
    (
        "appeals_decide_once",
        "BEFORE UPDATE OR DELETE",
        "appeals",
        "forbid_except_set_once('outcome', 'reviewer_id', 'reviewer_note', 'decided_at')",
    ),
)


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=20)


def _fk(cols: list[str], target: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(cols, [target], name=op.f(name), ondelete="RESTRICT")


def _now(name: str) -> sa.Column[Any]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def seed_config() -> None:
    """Insert ``fraud`` v1 only if the namespace has no rows yet (safe to re-run). created_by is
    NULL (a migration). The values are exactly docs/M5-DESIGN.md §2.4 and are validated by
    ``fraud/_limits.py:FraudConfig`` when loaded."""
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason) "
            "SELECT CAST(:namespace AS varchar), 1, CAST(:values AS jsonb), :reason "
            "WHERE NOT EXISTS "
            "(SELECT 1 FROM config_versions WHERE namespace = CAST(:namespace AS varchar))"
        ),
        {"namespace": NAMESPACE, "values": json.dumps(FRAUD_V1), "reason": FRAUD_REASON},
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "fraud_detector_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("detector", sa.String(length=40), nullable=False),
        sa.Column("trigger", _enum("detector_run_trigger", "sweep", "cli"), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        _now("started_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            _enum("detector_run_status", "running", "done", "truncated", "failed"),
            server_default="running",
            nullable=False,
        ),
        sa.Column("edges_scanned", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cases_opened", sa.Integer(), server_default="0", nullable=False),
        sa.Column("deeds_added", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "detector ~ '^[a-z][a-z0-9_]{0,39}$'",
            name=op.f("ck_fraud_detector_runs_detector_name"),
        ),
        sa.CheckConstraint(
            "window_end > window_start", name=op.f("ck_fraud_detector_runs_window_valid")
        ),
        sa.CheckConstraint(
            "config_version >= 1", name=op.f("ck_fraud_detector_runs_config_version_positive")
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name=op.f("ck_fraud_detector_runs_finished_fields"),
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name=op.f("ck_fraud_detector_runs_finished_after"),
        ),
        sa.CheckConstraint(
            "edges_scanned >= 0 AND cases_opened >= 0 AND deeds_added >= 0",
            name=op.f("ck_fraud_detector_runs_counts_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fraud_detector_runs")),
    )
    op.create_index(
        "ix_fraud_detector_runs_detector_started_at",
        "fraud_detector_runs",
        ["detector", sa.literal_column("started_at DESC")],
        unique=False,
    )

    op.create_table(
        "flag_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "type",
            _enum(
                "flag_case_type", "reciprocal", "ring", "concentration", "invite_anomaly", "content"
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("flag_case_status", "open", "parked", "resolved"),
            server_default="open",
            nullable=False,
        ),
        sa.Column("outcome", _enum("flag_case_outcome", "confirmed", "dismissed"), nullable=True),
        sa.Column("subject_key", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "signal",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("detector_config_version", sa.Integer(), nullable=False),
        sa.Column("review_by", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("held_count", sa.Integer(), server_default="0", nullable=False),
        _now("opened_at"),
        _now("updated_at"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(status = 'resolved') = (outcome IS NOT NULL AND resolved_at IS NOT NULL "
            "AND resolved_by IS NOT NULL)",
            name=op.f("ck_flag_cases_resolved_fields"),
        ),
        sa.CheckConstraint(
            "subject_key ~ '^[0-9a-f]{64}$'", name=op.f("ck_flag_cases_subject_key_hex")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(signal) = 'object'", name=op.f("ck_flag_cases_signal_object")
        ),
        sa.CheckConstraint(
            "detector_config_version >= 1", name=op.f("ck_flag_cases_config_version_positive")
        ),
        sa.CheckConstraint(
            "deed_count >= 0 AND held_count >= 0 AND held_count <= deed_count",
            name=op.f("ck_flag_cases_counts_valid"),
        ),
        _fk(["resolved_by"], "users.id", "fk_flag_cases_resolved_by_users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_flag_cases")),
    )
    op.create_index(
        "uq_flag_cases_live",
        "flag_cases",
        ["type", "subject_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('open', 'parked')"),
    )
    op.create_index(
        "ix_flag_cases_queue", "flag_cases", ["status", "review_by", "opened_at"], unique=False
    )
    op.create_index("ix_flag_cases_type_status", "flag_cases", ["type", "status"], unique=False)
    op.create_index(
        "ix_flag_cases_type_subject_key", "flag_cases", ["type", "subject_key"], unique=False
    )
    op.create_index(
        "ix_flag_cases_resolved_at",
        "flag_cases",
        ["resolved_at"],
        unique=False,
        postgresql_where=sa.text("status = 'resolved'"),
    )

    op.create_table(
        "flag_case_users",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        _fk(["case_id"], "flag_cases.id", "fk_flag_case_users_case_id_flag_cases"),
        _fk(["user_id"], "users.id", "fk_flag_case_users_user_id_users"),
        sa.PrimaryKeyConstraint("case_id", "user_id", name="pk_flag_case_users"),
    )
    op.create_index("ix_flag_case_users_user_id", "flag_case_users", ["user_id"], unique=False)

    op.create_table(
        "flag_case_deeds",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        _now("added_at"),
        _fk(["case_id"], "flag_cases.id", "fk_flag_case_deeds_case_id_flag_cases"),
        _fk(["deed_id"], "deeds.id", "fk_flag_case_deeds_deed_id_deeds"),
        sa.PrimaryKeyConstraint("case_id", "deed_id", name="pk_flag_case_deeds"),
    )
    op.create_index("ix_flag_case_deeds_deed_id", "flag_case_deeds", ["deed_id"], unique=False)

    op.create_table(
        "prize_holds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        _now("held_at"),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "release_reason",
            _enum("hold_release_reason", "released_on_confirm", "appeal_overturned"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "(released_at IS NULL) = (release_reason IS NULL)",
            name=op.f("ck_prize_holds_released_has_reason"),
        ),
        sa.CheckConstraint(
            "released_at IS NULL OR released_at >= held_at",
            name=op.f("ck_prize_holds_released_after_held"),
        ),
        _fk(["case_id"], "flag_cases.id", "fk_prize_holds_case_id_flag_cases"),
        _fk(["deed_id"], "deeds.id", "fk_prize_holds_deed_id_deeds"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prize_holds")),
        sa.UniqueConstraint("deed_id", "case_id", name="uq_prize_holds_deed_case"),
    )
    op.create_index(
        "ix_prize_holds_active_deed_id",
        "prize_holds",
        ["deed_id"],
        unique=False,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index("ix_prize_holds_case_id", "prize_holds", ["case_id"], unique=False)

    op.create_table(
        "flag_case_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column(
            "action",
            _enum(
                "flag_case_action", "park", "unpark", "request_explanation", "confirm", "dismiss"
            ),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        _now("created_at"),
        sa.CheckConstraint(
            "length(btrim(note)) >= 5", name=op.f("ck_flag_case_decisions_note_required")
        ),
        _fk(["actor_id"], "users.id", "fk_flag_case_decisions_actor_id_users"),
        _fk(["case_id"], "flag_cases.id", "fk_flag_case_decisions_case_id_flag_cases"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_flag_case_decisions")),
    )
    op.create_index(
        "ix_flag_case_decisions_case_created_at",
        "flag_case_decisions",
        ["case_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "flag_case_deed_outcomes",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", _enum("deed_outcome", "exclude", "reject", "release"), nullable=False),
        _now("created_at"),
        sa.ForeignKeyConstraint(
            ["case_id", "deed_id"],
            ["flag_case_deeds.case_id", "flag_case_deeds.deed_id"],
            name="fk_flag_case_deed_outcomes_case_deed",
            ondelete="RESTRICT",
        ),
        _fk(
            ["decision_id"],
            "flag_case_decisions.id",
            "fk_flag_case_deed_outcomes_decision_id_flag_case_decisions",
        ),
        sa.PrimaryKeyConstraint("case_id", "deed_id", name="pk_flag_case_deed_outcomes"),
    )
    op.create_index(
        "ix_flag_case_deed_outcomes_decision_id",
        "flag_case_deed_outcomes",
        ["decision_id"],
        unique=False,
    )

    op.create_table(
        "explanation_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        _now("requested_at"),
        sa.Column("reply_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(reply IS NULL) = (replied_at IS NULL)",
            name=op.f("ck_explanation_requests_reply_pair"),
        ),
        sa.CheckConstraint(
            "reply IS NULL OR length(reply) <= 2000",
            name=op.f("ck_explanation_requests_reply_length"),
        ),
        sa.CheckConstraint(
            "reply_due_at > requested_at", name=op.f("ck_explanation_requests_due_after_request")
        ),
        sa.CheckConstraint(
            "replied_at IS NULL OR (replied_at >= requested_at AND replied_at <= reply_due_at)",
            name=op.f("ck_explanation_requests_replied_in_time"),
        ),
        sa.CheckConstraint(
            "requested_by <> user_id", name=op.f("ck_explanation_requests_not_self")
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "user_id"],
            ["flag_case_users.case_id", "flag_case_users.user_id"],
            name="fk_explanation_requests_case_user",
            ondelete="RESTRICT",
        ),
        _fk(["requested_by"], "users.id", "fk_explanation_requests_requested_by_users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_explanation_requests")),
        sa.UniqueConstraint("case_id", "user_id", name="uq_explanation_requests_case_user"),
    )
    op.create_index(
        "ix_explanation_requests_user_id", "explanation_requests", ["user_id"], unique=False
    )

    op.create_table(
        "decision_notices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", _enum("notice_kind", "case", "override"), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("override_id", sa.Uuid(), nullable=True),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason_key", sa.String(length=40), nullable=False),
        _now("created_at"),
        sa.Column("appeal_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("final_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(kind = 'case' AND case_id IS NOT NULL AND override_id IS NULL) "
            "OR (kind = 'override' AND override_id IS NOT NULL AND case_id IS NULL)",
            name=op.f("ck_decision_notices_kind_matches_source"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(items) = 'array'", name=op.f("ck_decision_notices_items_array")
        ),
        sa.CheckConstraint(
            "appeal_until > created_at",
            name=op.f("ck_decision_notices_appeal_until_after_created"),
        ),
        sa.CheckConstraint(
            "final_at IS NULL OR final_at >= created_at",
            name=op.f("ck_decision_notices_final_after_created"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "user_id"],
            ["flag_case_users.case_id", "flag_case_users.user_id"],
            name="fk_decision_notices_case_user",
            ondelete="RESTRICT",
        ),
        _fk(["case_id"], "flag_cases.id", "fk_decision_notices_case_id_flag_cases"),
        _fk(["user_id"], "users.id", "fk_decision_notices_user_id_users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decision_notices")),
        sa.UniqueConstraint("case_id", "user_id", name="uq_decision_notices_case_user"),
        sa.UniqueConstraint("override_id", "user_id", name="uq_decision_notices_override_user"),
        sa.UniqueConstraint("id", "user_id", name="uq_decision_notices_id_user"),
    )
    op.create_index(
        "ix_decision_notices_not_final",
        "decision_notices",
        ["appeal_until"],
        unique=False,
        postgresql_where=sa.text("final_at IS NULL"),
    )
    op.create_index(
        "ix_decision_notices_user_created_at",
        "decision_notices",
        ["user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "appeals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("notice_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        _now("created_at"),
        sa.Column("reviewer_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", _enum("appeal_outcome", "upheld", "overturned"), nullable=True),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(btrim(reason)) >= 10 AND length(reason) <= 2000",
            name=op.f("ck_appeals_reason_length"),
        ),
        sa.CheckConstraint(
            "(outcome IS NULL) = (reviewer_id IS NULL) "
            "AND (outcome IS NULL) = (decided_at IS NULL) "
            "AND (outcome IS NULL) = (reviewer_note IS NULL)",
            name=op.f("ck_appeals_decided_fields"),
        ),
        sa.CheckConstraint(
            "reviewer_note IS NULL OR length(btrim(reviewer_note)) >= 5",
            name=op.f("ck_appeals_reviewer_note_required"),
        ),
        sa.CheckConstraint(
            "reviewer_id IS NULL OR reviewer_id <> user_id", name=op.f("ck_appeals_not_self")
        ),
        sa.CheckConstraint(
            "decided_at IS NULL OR decided_at >= created_at", name=op.f("ck_appeals_decided_after")
        ),
        sa.ForeignKeyConstraint(
            ["notice_id", "user_id"],
            ["decision_notices.id", "decision_notices.user_id"],
            name="fk_appeals_notice_user",
            ondelete="RESTRICT",
        ),
        _fk(["reviewer_id"], "users.id", "fk_appeals_reviewer_id_users"),
        _fk(["user_id"], "users.id", "fk_appeals_user_id_users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appeals")),
        sa.UniqueConstraint("notice_id", name="uq_appeals_notice_id"),
    )
    op.create_index(
        "ix_appeals_open_created_at",
        "appeals",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("outcome IS NULL"),
    )

    op.create_table(
        "review_views",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        _fk(["user_id"], "users.id", "fk_review_views_user_id_users"),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_review_views")),
    )

    for sql in _FUNCTIONS.values():
        op.execute(sql)
    for name, when, table, call in _TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} {when} ON {table} FOR EACH ROW EXECUTE FUNCTION {call}")
    seed_config()


def downgrade() -> None:
    """Downgrade schema."""
    # config_versions is append-only, so its trigger is bypassed for this namespace. Any later
    # published versions go too: before 0026 the namespace did not exist.
    op.execute("ALTER TABLE config_versions DISABLE TRIGGER config_versions_append_only")
    op.execute(f"DELETE FROM config_versions WHERE namespace = '{NAMESPACE}'")
    op.execute("ALTER TABLE config_versions ENABLE TRIGGER config_versions_append_only")
    for name, _, table, _ in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    # Indexes go with their tables; children before parents.
    for table in reversed(TABLES):
        op.drop_table(table)
    for fn in _FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
