"""fraud review reminders

M6A DB15 (docs/M6-DESIGN.md §2.4, D112; PO Q91 = A, 2026-11-27):

- ``explanation_reminders`` (insert-only, ``forbid_modification()``): ``request_id`` PK and FK
  to ``explanation_requests`` (RESTRICT), ``sent_at``. One row per request, so the daily
  ``fraud.remind_explanations`` job sends the reminder once. The M5 trigger on
  ``explanation_requests`` is not touched.
- ``fraud`` config: the next version = the latest plus ``explanation.reminder_days_before_due:
  3`` (``FraudConfig`` checks 1 <= value < ``explanation.reply_days``). Appended only if the
  latest version lacks it, so re-running is safe and a version published by hand is built on.

Downgrade appends a version without that key and drops the table.

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-27 03:00:00

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "fraud"
TABLE = "explanation_reminders"
KEY = "reminder_days_before_due"
EXPLANATION_KEYS = {KEY: 3}
UPGRADE_REASON = "M6A review reminders (docs/M6-DESIGN.md §2.4, D112; PO Q91, 2026-11-27)"
DOWNGRADE_REASON = "Rollback of 0033: explanation reminder key removed"

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
            "jsonb_set(latest.values, '{explanation}', "
            "  (latest.values->'explanation') || CAST(:keys AS jsonb)), :reason, NULL "
            + _LATEST
            + f"AND NOT ((latest.values->'explanation') ? '{KEY}')"
        ),
        {"namespace": NAMESPACE, "keys": json.dumps(EXPLANATION_KEYS), "reason": UPGRADE_REASON},
    )


def _remove_keys() -> None:
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "SELECT latest.namespace, latest.version + 1, "
            f"jsonb_set(latest.values, '{{explanation}}', (latest.values->'explanation') - '{KEY}'), "
            ":reason, NULL " + _LATEST + f"AND (latest.values->'explanation') ? '{KEY}'"
        ),
        {"namespace": NAMESPACE, "reason": DOWNGRADE_REASON},
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        TABLE,
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["explanation_requests.id"],
            name=op.f("fk_explanation_reminders_request_id_explanation_requests"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("request_id", name=op.f("pk_explanation_reminders")),
    )
    op.execute(
        f"CREATE TRIGGER {TABLE}_append_only BEFORE UPDATE OR DELETE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
    )
    _add_keys()


def downgrade() -> None:
    """Downgrade schema."""
    _remove_keys()
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_append_only ON {TABLE}")
    op.drop_table(TABLE)
