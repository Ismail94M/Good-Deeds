"""override requests may raise points

M5 SEC5-M3 (docs/M5-SEC5.md; PO Q80 = A): a raise to more than
``override_second_admin.max_cut_points`` above the rule points needs a second admin, like a big
cut, so it is stored as a pending ``override_requests`` row. The 0027 CHECK
``ck_override_requests_points_is_cut`` (``0 <= points < previous_points``) allowed only cuts; it
is replaced by ``ck_override_requests_points_changed`` (``points >= 0 AND points <>
previous_points``).

Downgrade refuses to run while a request raises points (``points > previous_points``): the old
CHECK would reject it, and requests are never deleted.

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-26 02:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "override_requests"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f("ck_override_requests_points_is_cut"), TABLE, type_="check")
    op.create_check_constraint(
        op.f("ck_override_requests_points_changed"),
        TABLE,
        "points >= 0 AND points <> previous_points",
    )


def downgrade() -> None:
    """Downgrade schema."""
    raised = op.get_bind().scalar(
        sa.text(f"SELECT EXISTS (SELECT 1 FROM {TABLE} WHERE points > previous_points)")
    )
    if raised:
        raise RuntimeError(f"{TABLE} has requests that raise points; they can't be kept below 0028")
    op.drop_constraint(op.f("ck_override_requests_points_changed"), TABLE, type_="check")
    op.create_check_constraint(
        op.f("ck_override_requests_points_is_cut"),
        TABLE,
        "points >= 0 AND points < previous_points",
    )
