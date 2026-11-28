"""identity avatar

M6A DB14 (docs/M6-DESIGN.md §2.3, D117, §4.4): the profile-picture pointer on ``users``.

- ``users.avatar_ref`` varchar(80) null: the opaque media reference of the live picture
  (``{photo_id}-{key_version}``), never a URL. Set only by ``identity``'s ``AvatarChanged``
  handler.
- ``users.avatar_changed_at`` timestamptz null: the ``changed_at`` of the event that last set it,
  so an older event never overwrites a newer one. It stays set after a removal (ref null).
- CHECK ``ck_users_avatar_ref_has_time``: a ref always has its time.

No backfill: nobody has a picture before M6A. Downgrade drops the CHECK and both columns.

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-27 02:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK = "ck_users_avatar_ref_has_time"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("avatar_ref", sa.String(length=80), nullable=True))
    op.add_column(
        "users", sa.Column("avatar_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f(CHECK), "users", "avatar_ref IS NULL OR avatar_changed_at IS NOT NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f(CHECK), "users", type_="check")
    op.drop_column("users", "avatar_changed_at")
    op.drop_column("users", "avatar_ref")
