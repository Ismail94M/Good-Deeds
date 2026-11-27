"""media avatars

M6A DB13 (docs/M6-DESIGN.md §2.2, §2.5, D116 to D123; PO answers Q86 to Q95, 2026-11-27):
profile pictures are photos with ``purpose = 'avatar'``.

- ``photos.purpose`` gains ``avatar`` (CHECK ``ck_photos_photo_purpose`` recreated; the enums
  are not native Postgres enums).
- ``photos.avatar_crop`` jsonb null: ``{x, y, size}`` normalised 0..1 on the oriented image.
  CHECKs: only avatars have one (``avatar_crop_avatar_only``); an avatar has one once processing
  is queued (``avatar_crop_required``: the upload row is created before ``PUT /me/avatar`` sends
  the crop); it is an object of three numbers in range (``avatar_crop_shape``).
- ``ck_photos_owner_matches_purpose`` is unchanged: it already keeps ``deed_id`` to deed photos
  and ``self_log_id`` to self-log photos, so an avatar has neither.
- ``ck_photos_takedown_deed_only`` becomes ``takedown = 'none' OR purpose IN ('deed',
  'avatar')`` (admin removal of avatars, D121; the name is kept, services map it), and the new
  ``ck_photos_doer_removed_deed_only`` keeps ``doer_removed`` for deed photos.
- ``uq_photos_live_avatar UNIQUE (uploader_user_id) WHERE purpose = 'avatar' AND status =
  'ready' AND takedown = 'none'``: one live picture per person.
- The report reason gains ``impersonation`` (priority 2, in code) on ``photo_reports.reason``,
  ``photo_report_cases.removal_reason`` and ``photo_decisions.removal_reason``.
- ``media`` config: the next version = the latest plus the avatar keys (§2.5), appended only if
  the latest lacks them, so re-running is safe and a version published by hand is built on.

Variant keys reuse ``_keys.variant_key`` (``photos/{id}/avatar_md-1.webp``): no schema change.

Downgrade appends a version without the avatar keys and restores the CHECKs; it refuses while
avatar photos or ``impersonation`` reasons exist (photos are never deleted and
``photo_decisions`` is append-only, so they can't be rewritten).

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-27 01:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "media"
PURPOSES = ("deed", "self_log")
NEW_PURPOSE = "avatar"
REASONS = (
    "not_consented",
    "child_no_guardian_consent",
    "private_details",
    "inappropriate",
    "other",
)
NEW_REASON = "impersonation"
# (table, column, CHECK name) of every column typed by the report reason enum.
REASON_COLUMNS = (
    ("photo_reports", "reason", "ck_photo_reports_photo_report_reason"),
    (
        "photo_report_cases",
        "removal_reason",
        "ck_photo_report_cases_photo_report_case_removal_reason",
    ),
    ("photo_decisions", "removal_reason", "ck_photo_decisions_photo_decision_removal_reason"),
)
PURPOSE_CHECK = "ck_photos_photo_purpose"
TAKEDOWN_CHECK = "ck_photos_takedown_deed_only"
DOER_REMOVED_CHECK = "ck_photos_doer_removed_deed_only"
CROP_CHECKS = {
    "ck_photos_avatar_crop_avatar_only": "avatar_crop IS NULL OR purpose = 'avatar'",
    "ck_photos_avatar_crop_required": (
        "purpose <> 'avatar' OR avatar_crop IS NOT NULL "
        "OR status NOT IN ('queued', 'processing', 'ready')"
    ),
    # Kept in step with ``media/models.py:AVATAR_CROP_SHAPE_SQL``.
    "ck_photos_avatar_crop_shape": (
        "avatar_crop IS NULL OR (jsonb_typeof(avatar_crop) = 'object' AND CASE "
        "WHEN jsonb_typeof(avatar_crop->'x') = 'number' "
        "AND jsonb_typeof(avatar_crop->'y') = 'number' "
        "AND jsonb_typeof(avatar_crop->'size') = 'number' "
        "THEN (avatar_crop->>'x')::numeric BETWEEN 0 AND 1 "
        "AND (avatar_crop->>'y')::numeric BETWEEN 0 AND 1 "
        "AND (avatar_crop->>'size')::numeric > 0 AND (avatar_crop->>'size')::numeric <= 1 "
        "ELSE false END)"
    ),
}
LIVE_AVATAR_INDEX = "uq_photos_live_avatar"

# docs/M6-DESIGN.md §2.5; D116, D118, D123 and Q95 (approved defaults).
AVATAR_KEYS: dict[str, Any] = {
    "avatars_enabled": True,
    "avatar_variants": [
        {"name": "avatar_sm", "max_side_px": 96},
        {"name": "avatar_md", "max_side_px": 256},
        {"name": "avatar_lg", "max_side_px": 512},
    ],
    "avatar_min_side_px": 256,
    "avatar_url_ttl_minutes": 1440,
    "avatar_url_step_minutes": 720,
    "avatar_sign_max_refs": 100,
    "avatar_reupload_cooldown_hours": 24,
    "rate_avatar_change_user": "5/86400",
    "rate_avatar_sign_user": "120/60",
}
UPGRADE_REASON = (
    "M6A profile pictures (docs/M6-DESIGN.md §2.5; PO answers Q86 to Q95 and defaults, 2026-11-27)"
)
DOWNGRADE_REASON = "Rollback of 0031: profile-picture keys removed"

_LATEST = (
    "FROM config_versions AS latest "
    "WHERE latest.namespace = CAST(:namespace AS varchar) "
    "AND latest.version = (SELECT max(version) FROM config_versions "
    "WHERE namespace = CAST(:namespace AS varchar)) "
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _recheck(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, condition)


def _add_keys() -> None:
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "SELECT latest.namespace, latest.version + 1, "
            "latest.values || CAST(:keys AS jsonb), :reason, NULL "
            + _LATEST
            + "AND NOT (latest.values ? 'avatars_enabled')"
        ),
        {"namespace": NAMESPACE, "keys": json.dumps(AVATAR_KEYS), "reason": UPGRADE_REASON},
    )


def _remove_keys() -> None:
    keys = ", ".join(f"'{k}'" for k in AVATAR_KEYS)
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            f"SELECT latest.namespace, latest.version + 1, latest.values - ARRAY[{keys}], "
            ":reason, NULL " + _LATEST + "AND latest.values ? 'avatars_enabled'"
        ),
        {"namespace": NAMESPACE, "reason": DOWNGRADE_REASON},
    )


def upgrade() -> None:
    """Upgrade schema."""
    _recheck("photos", PURPOSE_CHECK, _in("purpose", (*PURPOSES, NEW_PURPOSE)))
    for table, column, name in REASON_COLUMNS:
        _recheck(table, name, _in(column, (*REASONS, NEW_REASON)))

    op.add_column(
        "photos",
        sa.Column("avatar_crop", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    for name, condition in CROP_CHECKS.items():
        op.create_check_constraint(op.f(name), "photos", condition)
    _recheck("photos", TAKEDOWN_CHECK, "takedown = 'none' OR purpose IN ('deed', 'avatar')")
    op.create_check_constraint(
        op.f(DOER_REMOVED_CHECK), "photos", "takedown <> 'doer_removed' OR purpose = 'deed'"
    )
    op.create_index(
        LIVE_AVATAR_INDEX,
        "photos",
        ["uploader_user_id"],
        unique=True,
        postgresql_where=sa.text("purpose = 'avatar' AND status = 'ready' AND takedown = 'none'"),
    )
    _add_keys()


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM photos WHERE purpose = :p)"), {"p": NEW_PURPOSE}
    ):
        raise RuntimeError("photos has profile pictures (purpose 'avatar'); can't go below 0031")
    for table, column, _ in REASON_COLUMNS:
        used = bind.scalar(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE {column} = :r)"), {"r": NEW_REASON}
        )
        if used:
            raise RuntimeError(f"{table}.{column} has {NEW_REASON!r} rows; can't go below 0031")
    _remove_keys()
    op.drop_index(LIVE_AVATAR_INDEX, table_name="photos")
    op.drop_constraint(op.f(DOER_REMOVED_CHECK), "photos", type_="check")
    _recheck("photos", TAKEDOWN_CHECK, "takedown = 'none' OR purpose = 'deed'")
    for name in CROP_CHECKS:
        op.drop_constraint(op.f(name), "photos", type_="check")
    op.drop_column("photos", "avatar_crop")
    for table, column, name in REASON_COLUMNS:
        _recheck(table, name, _in(column, REASONS))
    _recheck("photos", PURPOSE_CHECK, _in("purpose", PURPOSES))
