"""SEC6: reply fan-out cap and the profile-picture cooldown reject reason

M6A SEC6-M1 (docs/M6-SEC6.md): the "also commented" fan-out of one new comment reaches at most
``replies.max_recipients_per_comment`` earlier commenters (the most recent first).

- ``notifications`` config: the next version = the latest plus
  ``replies.max_recipients_per_comment: 200``. Appended only if the latest version lacks it, so
  re-running is safe and a version published by hand in between is built on.
- ``photos.reject_reason`` also allows ``cooldown`` (SEC6-L3, Q95): a profile picture queued
  before an admin removal is rejected, not made live, when the worker finishes it during the
  wait (``ck_photos_photo_reject_reason``).

Downgrade appends a version without that key and restores the CHECK; it refuses while
``cooldown`` rows exist (the old CHECK would reject them; rows are kept).

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-28 00:00:00

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "notifications"
KEY = "max_recipients_per_comment"
REPLY_KEYS = {KEY: 200}
UPGRADE_REASON = "SEC6-M1: cap on earlier commenters told per new comment (docs/M6-SEC6.md)"
DOWNGRADE_REASON = "Rollback of 0034: replies.max_recipients_per_comment removed"

PHOTOS = "photos"
REJECT_CHECK = "ck_photos_photo_reject_reason"
REJECT_REASONS = (
    "unsupported_type",
    "too_large",
    "too_many_pixels",
    "animated",
    "corrupt",
    "size_mismatch",
    "removed_by_doer",
)
COOLDOWN = "cooldown"

_LATEST = (
    "FROM config_versions AS latest "
    "WHERE latest.namespace = CAST(:namespace AS varchar) "
    "AND latest.version = (SELECT max(version) FROM config_versions "
    "WHERE namespace = CAST(:namespace AS varchar)) "
)


def _reject_check(reasons: Sequence[str]) -> None:
    op.drop_constraint(op.f(REJECT_CHECK), PHOTOS, type_="check")
    listed = ", ".join(f"'{r}'" for r in reasons)
    op.create_check_constraint(op.f(REJECT_CHECK), PHOTOS, f"reject_reason IN ({listed})")


def upgrade() -> None:
    """Upgrade schema."""
    _reject_check((*REJECT_REASONS, COOLDOWN))
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "SELECT latest.namespace, latest.version + 1, "
            "jsonb_set(latest.values, '{replies}', "
            "  (latest.values->'replies') || CAST(:replies AS jsonb)), :reason, NULL "
            + _LATEST
            + "AND NOT ((latest.values->'replies') ? CAST(:key AS text))"
        ),
        {
            "namespace": NAMESPACE,
            "replies": json.dumps(REPLY_KEYS),
            "key": KEY,
            "reason": UPGRADE_REASON,
        },
    )


def downgrade() -> None:
    """Downgrade schema."""
    cooldown = op.get_bind().scalar(
        sa.text(f"SELECT EXISTS (SELECT 1 FROM {PHOTOS} WHERE reject_reason = '{COOLDOWN}')")
    )
    if cooldown:
        raise RuntimeError(f"{PHOTOS} has '{COOLDOWN}' rejections; they can't be kept below 0034")
    _reject_check(REJECT_REASONS)
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "SELECT latest.namespace, latest.version + 1, "
            "jsonb_set(latest.values, '{replies}', "
            "  (latest.values->'replies') - CAST(:key AS text)), :reason, NULL "
            + _LATEST
            + "AND (latest.values->'replies') ? CAST(:key AS text)"
        ),
        {"namespace": NAMESPACE, "key": KEY, "reason": DOWNGRADE_REASON},
    )
