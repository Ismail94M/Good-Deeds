"""scoring overrides

M5 DB11 (docs/M5-DESIGN.md §2.2, D85, D91, Q77; revision 4, PO approved 2026-11-26):

- ``score_overrides`` (append-only, ``forbid_modification()``): an admin's points for one deed
  and role; the latest row per (deed, role) is in force, ``points`` null clears it. ``case_id``
  has no foreign key (``fraud`` owns cases, D78); ``request_id`` (unique) names the approved
  ``override_requests`` row when a second admin approved a big cut.
- ``override_requests``: a big cut waiting for a different admin. CHECKs ``points_is_cut``,
  ``second_admin`` (``decided_by <> requested_by``) and ``status_fields`` (which columns are set
  in each status); partial unique ``uq_override_requests_pending (deed_id, role) WHERE status =
  'pending'``. Trigger ``override_requests_decide_once``: decided once, from ``pending`` only,
  changing only the decision columns; never deleted. ``override_id`` references
  ``score_overrides`` (added after both tables exist; the two tables point at each other).
- ``concentration_signals`` (insert-only): one row per (doer, reporter, month), so
  ``ReporterConcentrationExceeded`` fires once (D85).
- ``points_ledger.reason`` gains ``admin_override`` (CHECK ``ck_points_ledger_ledger_reason``
  recreated with the extra value). The ``points_ledger_check_entry`` trigger needs no change: it
  checks a reversal against its target and refuses a second active award or decision per (deed,
  role), but never looks at ``reason``, so an ``admin_override`` award, zero-point decision or
  reversal is accepted under the same rules as every other entry.
- ``ix_deeds_verified_at (verified_at) WHERE status = 'verified'``: the detectors' window reads
  (``deeds.service.verified_edges``, §3.1). No equivalent index existed.

Downgrade refuses to run while ledger rows with reason ``admin_override`` exist (they would break
the restored CHECK; the ledger is append-only), then drops everything above.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-26 01:00:00

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("override_requests", "score_overrides", "concentration_signals")
APPEND_ONLY_TABLES = ("score_overrides", "concentration_signals")

LEDGER_REASONS = (
    "verified",
    "promoted",
    "edited",
    "prize_rank_changed",
    "photo_evidence",
    "doer_daily_cap",
    "reporter_daily_cap",
    "withdrawn",
    "rejected",
    "removed",
    "counts_for_points_lost",
)
NEW_LEDGER_REASON = "admin_override"
LEDGER_REASON_CHECK = "ck_points_ledger_ledger_reason"

_FUNCTIONS: dict[str, str] = {
    "override_requests_decide_once": """
CREATE FUNCTION override_requests_decide_once() RETURNS trigger AS $$
DECLARE
    decision text[] := ARRAY['status', 'decided_by', 'decision_note', 'decided_at', 'override_id'];
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'override_requests rows are never deleted';
    END IF;
    IF OLD.status <> 'pending' OR NEW.status = 'pending'
       OR (to_jsonb(NEW) - decision) IS DISTINCT FROM (to_jsonb(OLD) - decision) THEN
        RAISE EXCEPTION 'override_requests rows are decided once, from pending only';
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
    (
        "override_requests_decide_once",
        "BEFORE UPDATE OR DELETE",
        "override_requests",
        "override_requests_decide_once()",
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


def _ledger_reason_check(reasons: Sequence[str]) -> None:
    listed = ", ".join(f"'{r}'" for r in reasons)
    op.drop_constraint(op.f(LEDGER_REASON_CHECK), "points_ledger", type_="check")
    op.create_check_constraint(op.f(LEDGER_REASON_CHECK), "points_ledger", f"reason IN ({listed})")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "override_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column("role", _enum("ledger_role", "doer", "reporter"), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("previous_points", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=30), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        _now("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            _enum("override_request_status", "pending", "approved", "declined", "expired"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("override_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "points >= 0 AND points < previous_points",
            name=op.f("ck_override_requests_points_is_cut"),
        ),
        sa.CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9_]{0,29}$'",
            name=op.f("ck_override_requests_reason_code_format"),
        ),
        sa.CheckConstraint(
            "length(btrim(note)) >= 5", name=op.f("ck_override_requests_note_required")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_override_requests_expires_after_created")
        ),
        sa.CheckConstraint(
            "decided_by <> requested_by", name=op.f("ck_override_requests_second_admin")
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_by IS NULL AND decided_at IS NULL "
            "AND decision_note IS NULL AND override_id IS NULL) "
            "OR (status = 'approved' AND decided_by IS NOT NULL AND decided_at IS NOT NULL "
            "AND decision_note IS NOT NULL AND override_id IS NOT NULL) "
            "OR (status = 'declined' AND decided_by IS NOT NULL AND decided_at IS NOT NULL "
            "AND decision_note IS NOT NULL AND override_id IS NULL) "
            "OR (status = 'expired' AND decided_by IS NULL AND decided_at IS NOT NULL "
            "AND decision_note IS NULL AND override_id IS NULL)",
            name=op.f("ck_override_requests_status_fields"),
        ),
        sa.CheckConstraint(
            "decision_note IS NULL OR length(btrim(decision_note)) >= 5",
            name=op.f("ck_override_requests_decision_note_required"),
        ),
        sa.CheckConstraint(
            "decided_at IS NULL OR decided_at >= created_at",
            name=op.f("ck_override_requests_decided_after"),
        ),
        _fk(["decided_by"], "users.id", "fk_override_requests_decided_by_users"),
        _fk(["deed_id"], "deeds.id", "fk_override_requests_deed_id_deeds"),
        _fk(["requested_by"], "users.id", "fk_override_requests_requested_by_users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_override_requests")),
        sa.UniqueConstraint("override_id", name="uq_override_requests_override_id"),
    )
    op.create_index(
        "uq_override_requests_pending",
        "override_requests",
        ["deed_id", "role"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_override_requests_status_created_at",
        "override_requests",
        ["status", "created_at"],
        unique=False,
    )

    op.create_table(
        "score_overrides",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column("role", _enum("ledger_role", "doer", "reporter"), nullable=False),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.Column("previous_points", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=30), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        _now("created_at"),
        sa.CheckConstraint(
            "points IS NULL OR points >= 0", name=op.f("ck_score_overrides_points_non_negative")
        ),
        sa.CheckConstraint(
            "previous_points >= 0", name=op.f("ck_score_overrides_previous_points_non_negative")
        ),
        sa.CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9_]{0,29}$'",
            name=op.f("ck_score_overrides_reason_code_format"),
        ),
        sa.CheckConstraint(
            "length(btrim(note)) >= 5", name=op.f("ck_score_overrides_note_required")
        ),
        _fk(["actor_id"], "users.id", "fk_score_overrides_actor_id_users"),
        _fk(["deed_id"], "deeds.id", "fk_score_overrides_deed_id_deeds"),
        _fk(
            ["request_id"],
            "override_requests.id",
            "fk_score_overrides_request_id_override_requests",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_score_overrides")),
        sa.UniqueConstraint("request_id", name="uq_score_overrides_request_id"),
    )
    op.create_index(
        "ix_score_overrides_deed_role_created_at",
        "score_overrides",
        ["deed_id", "role", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_override_requests_override_id_score_overrides"),
        "override_requests",
        "score_overrides",
        ["override_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "concentration_signals",
        sa.Column("doer_id", sa.Uuid(), nullable=False),
        sa.Column("reporter_id", sa.Uuid(), nullable=False),
        sa.Column("period_month", sa.Date(), nullable=False),
        sa.Column("share_bp", sa.Integer(), nullable=False),
        sa.Column("doer_points", sa.Integer(), nullable=False),
        _now("detected_at"),
        sa.CheckConstraint(
            "period_month = CAST(date_trunc('month', period_month) AS date)",
            name=op.f("ck_concentration_signals_first_of_month"),
        ),
        sa.CheckConstraint(
            "share_bp BETWEEN 0 AND 10000", name=op.f("ck_concentration_signals_share_bp_range")
        ),
        sa.CheckConstraint(
            "doer_points >= 0", name=op.f("ck_concentration_signals_doer_points_non_negative")
        ),
        sa.CheckConstraint(
            "doer_id <> reporter_id", name=op.f("ck_concentration_signals_not_self")
        ),
        _fk(["doer_id"], "users.id", "fk_concentration_signals_doer_id_users"),
        _fk(["reporter_id"], "users.id", "fk_concentration_signals_reporter_id_users"),
        sa.PrimaryKeyConstraint(
            "doer_id", "reporter_id", "period_month", name="pk_concentration_signals"
        ),
    )

    for sql in _FUNCTIONS.values():
        op.execute(sql)
    for name, when, table, call in _TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} {when} ON {table} FOR EACH ROW EXECUTE FUNCTION {call}")

    _ledger_reason_check((*LEDGER_REASONS, NEW_LEDGER_REASON))

    op.create_index(
        "ix_deeds_verified_at",
        "deeds",
        ["verified_at"],
        unique=False,
        postgresql_where=sa.text("status = 'verified'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    used = op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM points_ledger WHERE reason = :reason)"),
        {"reason": NEW_LEDGER_REASON},
    )
    if used:
        raise RuntimeError(
            f"points_ledger has rows with reason {NEW_LEDGER_REASON!r}; the append-only ledger "
            "can't be rolled back below 0027"
        )
    op.drop_index("ix_deeds_verified_at", table_name="deeds")
    _ledger_reason_check(LEDGER_REASONS)
    for name, _, table, _ in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.drop_constraint(
        op.f("fk_override_requests_override_id_score_overrides"),
        "override_requests",
        type_="foreignkey",
    )
    for table in reversed(TABLES):
        op.drop_table(table)
    for fn in _FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
