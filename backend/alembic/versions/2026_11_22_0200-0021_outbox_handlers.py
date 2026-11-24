"""outbox handlers and scoring indexes

M4a SEC3 fixes M1 and L2 (docs/M4-DESIGN.md D64):

- ``outbox_events.dispatched_handlers`` (text[], nullable): the handlers an event was enqueued
  for when it was dispatched. The finalization barrier (``core.events.unprocessed_before``)
  counts an (event, handler) pair as waiting only when the event is not dispatched yet or the
  handler is in this list, so an event dispatched before a handler existed never blocks it.
  Backfill for events already dispatched: the handlers that have processed them (what we know
  ran), so old M2/M3 events don't block M4 finalization.
- ``ix_outbox_events_occurred_at`` and ``ix_points_ledger_period_month``: the barrier and the
  "closed month with no deeds" read look up a month's events and ledger rows by these.

Downgrade drops the indexes and the column.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-22 02:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "outbox_events",
        sa.Column("dispatched_handlers", postgresql.ARRAY(sa.String(length=200)), nullable=True),
    )
    op.execute(
        """
        UPDATE outbox_events o
        SET dispatched_handlers = COALESCE(
            (SELECT array_agg(p.handler ORDER BY p.handler)
             FROM processed_events p WHERE p.event_id = o.id),
            '{}'
        )
        WHERE o.dispatched_at IS NOT NULL
        """
    )
    op.create_index("ix_outbox_events_occurred_at", "outbox_events", ["occurred_at"])
    op.create_index("ix_points_ledger_period_month", "points_ledger", ["period_month"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_points_ledger_period_month", table_name="points_ledger")
    op.drop_index("ix_outbox_events_occurred_at", table_name="outbox_events")
    op.drop_column("outbox_events", "dispatched_handlers")
