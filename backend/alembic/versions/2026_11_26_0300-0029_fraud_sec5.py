"""fraud: SEC5 fixes (ring bounds, truncation records, rejection markers, member rate limits)

M5 SEC5 (docs/M5-SEC5.md; PO Q81 = A):

- ``fraud_detector_runs.trigger`` also allows ``event``: a per-deed check that was truncated is
  recorded, so admins see it on the status page (SEC5-M6).
- ``flag_rejections_sent`` (insert-only): one row per ``reject`` outcome already sent to
  ``deeds`` in a ``FlagDecisionFinal`` event, so ``finalize_decisions`` no longer reads its
  history back from the outbox (SEC5-L6). Backfilled from the existing outbox rows.
- ``fraud`` config: the next version = the latest plus ``ring.max_queries_per_check``,
  ``ring.max_cycles``, ``ring.max_group_size``, ``ring.retry_max_search_edges`` (SEC5-M5, M6),
  ``rate_member_read`` / ``rate_member_write`` (SEC5-L5) and ``sweep_chunk_edges`` (SEC5-L7).
  Appended only if the latest version lacks them, so re-running is safe and a version published
  by hand in between is built on.

Downgrade appends a version without those keys, drops the table and restores the CHECK; it
refuses while ``event`` runs exist (the old CHECK would reject them; runs are kept).

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-26 03:00:00

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "fraud"
RUNS = "fraud_detector_runs"
TRIGGER_CHECK = "ck_fraud_detector_runs_detector_run_trigger"
SENT = "flag_rejections_sent"

RING_KEYS = {
    "max_queries_per_check": 12,
    "max_cycles": 5000,
    "max_group_size": 50,
    "retry_max_search_edges": 50000,
}
TOP_KEYS = {
    "rate_member_read": "120/60",
    "rate_member_write": "20/60",
    "sweep_chunk_edges": 500,
}
UPGRADE_REASON = (
    "SEC5 (PO Q81): ring search and group bounds, retry budget, member rate limits, sweep chunks"
)
DOWNGRADE_REASON = "Rollback of 0029: SEC5 fraud keys removed"

_LATEST = (
    "FROM config_versions AS latest "
    "WHERE latest.namespace = CAST(:namespace AS varchar) "
    "AND latest.version = (SELECT max(version) FROM config_versions "
    "WHERE namespace = CAST(:namespace AS varchar)) "
)


def _add_keys() -> None:
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "SELECT latest.namespace, latest.version + 1, "
            "jsonb_set(latest.values || CAST(:top AS jsonb), '{ring}', "
            "  (latest.values->'ring') || CAST(:ring AS jsonb)), :reason, NULL "
            + _LATEST
            + "AND NOT (latest.values ? 'sweep_chunk_edges' "
            "AND (latest.values->'ring') ? 'max_queries_per_check')"
        ),
        {
            "namespace": NAMESPACE,
            "top": json.dumps(TOP_KEYS),
            "ring": json.dumps(RING_KEYS),
            "reason": UPGRADE_REASON,
        },
    )


def _remove_keys() -> None:
    top = ", ".join(f"'{k}'" for k in TOP_KEYS)
    ring = ", ".join(f"'{k}'" for k in RING_KEYS)
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "SELECT latest.namespace, latest.version + 1, "
            f"jsonb_set(latest.values - ARRAY[{top}], '{{ring}}', "
            f"  (latest.values->'ring') - ARRAY[{ring}]), :reason, NULL "
            + _LATEST
            + "AND (latest.values ? 'sweep_chunk_edges' "
            "OR (latest.values->'ring') ? 'max_queries_per_check')"
        ),
        {"namespace": NAMESPACE, "reason": DOWNGRADE_REASON},
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f(TRIGGER_CHECK), RUNS, type_="check")
    op.create_check_constraint(op.f(TRIGGER_CHECK), RUNS, "trigger IN ('sweep', 'cli', 'event')")

    op.create_table(
        SENT,
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "deed_id"],
            ["flag_case_deed_outcomes.case_id", "flag_case_deed_outcomes.deed_id"],
            name=op.f("fk_flag_rejections_sent_outcome"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("case_id", "deed_id", name=op.f("pk_flag_rejections_sent")),
    )
    op.execute(
        f"CREATE TRIGGER {SENT}_append_only BEFORE UPDATE OR DELETE ON {SENT} "
        "FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
    )
    # Every deed already sent (read back from the outbox, as finalize_decisions did until now).
    op.execute(
        f"INSERT INTO {SENT} (case_id, deed_id, sent_at) "
        "SELECT o.case_id, o.deed_id, min(e.occurred_at) "
        "FROM outbox_events AS e "
        "CROSS JOIN LATERAL jsonb_array_elements_text(e.payload->'rejected_deed_ids') AS d(id) "
        "JOIN flag_case_deed_outcomes AS o "
        "  ON o.case_id = CAST(e.payload->>'case_id' AS uuid) AND o.deed_id = CAST(d.id AS uuid) "
        "WHERE e.event_type = 'fraud.flag_decision_final' "
        "GROUP BY o.case_id, o.deed_id "
        "ON CONFLICT DO NOTHING"
    )
    _add_keys()


def downgrade() -> None:
    """Downgrade schema."""
    events = op.get_bind().scalar(
        sa.text(f"SELECT EXISTS (SELECT 1 FROM {RUNS} WHERE trigger = 'event')")
    )
    if events:
        raise RuntimeError(f"{RUNS} has per-deed (event) runs; they can't be kept below 0029")
    _remove_keys()
    op.execute(f"DROP TRIGGER IF EXISTS {SENT}_append_only ON {SENT}")
    op.drop_table(SENT)
    op.drop_constraint(op.f(TRIGGER_CHECK), RUNS, type_="check")
    op.create_check_constraint(op.f(TRIGGER_CHECK), RUNS, "trigger IN ('sweep', 'cli')")
