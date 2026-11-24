"""scoring

M4a DB8 (docs/M4-DESIGN.md §2.3, §2.6, D48 to D70): the ``scoring`` module tables and the
``scoring`` v1 and ``leaderboard`` v1 config (PO values, Q45 to Q58). The M4b tables
(``ai_eval_runs``, ``ai_tiebreak_shadow``) and ``ai_eval`` config come in 0021.

Tables and the invariants the database enforces:

- ``points_ledger`` (append-only, ``forbid_modification()``): CHECKs ``reporter_never_prize_rank``
  (D57), ``reversal_has_target``, ``points_by_kind`` (award > 0, decision = 0, reversal <= 0),
  ``deed_units_by_kind``, ``reporter_no_deed_units``, ``period_month_of_earned_on`` (D58);
  ``reverses_entry_id`` unique (an entry is reversed at most once). Trigger
  ``points_ledger_check_entry``: a reversal must negate one earlier non-reversal entry of the same
  user, deed and role, copying its prize flag, region and dates; an award or decision is refused
  while the (deed, role) still has an active (unreversed) award or decision, so a re-score
  inserts the reversal first. Indexes follow §2.3: history ``(user_id, created_at desc)``,
  ``(deed_id, role)``, the board aggregate ``(period_month, region_id, user_id)`` over doer
  prize-rank rows, and the daily caps ``(user_id, earned_on, role)``.
- ``impact_evaluations`` (append-only): ``ai_is_shadow`` (``method <> 'ai' OR mode = 'shadow'``,
  D62), ``rule_based_is_live`` and the per-method field shapes.
- ``scoring_evidence``: trigger ``scoring_evidence_monotonic`` (D51): the first photo, once set,
  can't change or be cleared, and rows are never deleted.
- ``month_finalizations`` (append-only, D64).
- ``leaderboard_snapshots`` (one final row per region and month, partial unique index) and
  ``leaderboard_entries``: a final snapshot and its entries can't be updated or deleted.
- ``hero_titles`` (unique per month, region and user) and ``user_badges`` (unique per user and
  badge): permanent; the only change allowed is a single revocation (``revoked_*``, M5).

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-22 01:00:00

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "month_finalizations",
    "leaderboard_snapshots",
    "hero_titles",
    "leaderboard_entries",
    "impact_evaluations",
    "scoring_evidence",
    "user_badges",
    "points_ledger",
)
APPEND_ONLY_TABLES = ("points_ledger", "impact_evaluations", "month_finalizations")

SCORING_REASON = (
    "Initial M4 values (docs/M4-DESIGN.md §2.6; PO decisions Q45 = C, Q46 = A, Q47, Q48 = A, "
    "2026-11-22)"
)
SCORING_V1: dict[str, Any] = {
    "timezone": "Asia/Kolkata",
    # Q45 = C: every category gives 10 doer points.
    "category_base_points": {
        "helping_elderly": 10,
        "animals": 10,
        "environment": 10,
        "food_sharing": 10,
        "road_safety": 10,
        "medical_help": 10,
        "community": 10,
        "other": 10,
    },
    "min_points": 4,
    "photo_bonus": 2,
    "photo_bonus_window_hours": 24,
    "reporter_points": 2,
    "reporter_points_beyond_cap": 1,
    "doer_daily_cap": 10,
    "reporter_daily_cap": 10,
    # Q47: per IST month, both roles: pair reports 1-3 100 %, 4-7 75 %, 8-15 50 %.
    "pair_window": "ist_month",
    "pair_roles": ["doer", "reporter"],
    "pair_multipliers": [
        {"from_ordinal": 1, "percent": 100},
        {"from_ordinal": 4, "percent": 75},
        {"from_ordinal": 8, "percent": 50},
    ],
    "text_rules": {
        "min_letters": 10,
        "max_repeated_char_run": 6,
        "spam_patterns": [r"(?i)\b(follow me|subscribe|whatsapp me|dm me)\b"],
        "category_keywords": {"animals": ["dog", "cat", "cow", "bird", "puppy", "stray"]},
    },
    "rate_me_user": "120/60",
    "rate_public_ip": "120/60",
}

LEADERBOARD_REASON = (
    "Initial M4 values (docs/M4-DESIGN.md §2.6; PO decisions Q50, Q53 = A, Q54 replacement, "
    "Q55, Q58 = dense, 2026-11-22). Tier and badge names and title wording await PO approval"
)
LEADERBOARD_V1: dict[str, Any] = {
    "weights": {"deeds": 1, "points": 1},
    "refresh_seconds": 300,
    "page_size": 50,
    "finalize_margin_minutes": 60,
    "shared_rank_style": "dense",
    "levels": [
        {
            "kind": "ward",
            "enabled": True,
            "min_counted_deeds": 3,
            "min_distinct_reporters": 2,
            "titles": [
                {"key": "hero", "places": [1], "label": "Kindness Hero · {region} · {month}"}
            ],
        },
        {
            "kind": "city",
            "enabled": True,
            "min_counted_deeds": 0,
            "min_distinct_reporters": 0,
            "titles": [
                {"key": "hero", "places": [1], "label": "{region} City Kindness Hero · {month}"},
                {"key": "top3", "places": [2, 3], "label": "Top 3 in {region} · {month}"},
            ],
        },
        {
            "kind": "state",
            "enabled": False,
            "min_counted_deeds": 0,
            "min_distinct_reporters": 0,
            "titles": [],
        },
        {
            "kind": "country",
            "enabled": False,
            "min_counted_deeds": 0,
            "min_distinct_reporters": 0,
            "titles": [],
        },
    ],
    "tiers": [
        {"slug": "seed", "label": "Seed", "min_points": 0},
        {"slug": "sprout", "label": "Sprout", "min_points": 50},
        {"slug": "tree", "label": "Tree", "min_points": 250},
        {"slug": "forest", "label": "Forest", "min_points": 1000},
    ],
    "badges": [
        {
            "slug": "first_recognised",
            "label": "First recognition",
            "kind": "doer_deeds",
            "threshold": 1,
        },
        {"slug": "kind_5", "label": "5 deeds", "kind": "doer_deeds", "threshold": 5},
        {"slug": "kind_25", "label": "25 deeds", "kind": "doer_deeds", "threshold": 25},
        {"slug": "kind_100", "label": "100 deeds", "kind": "doer_deeds", "threshold": 100},
        {"slug": "first_witness", "label": "First witness", "kind": "reports", "threshold": 1},
        {"slug": "witness_10", "label": "10 deeds recognised", "kind": "reports", "threshold": 10},
        {"slug": "witness_50", "label": "50 deeds recognised", "kind": "reports", "threshold": 50},
    ],
}

SEEDS: tuple[tuple[str, dict[str, Any], str], ...] = (
    ("scoring", SCORING_V1, SCORING_REASON),
    ("leaderboard", LEADERBOARD_V1, LEADERBOARD_REASON),
)

_FUNCTIONS: dict[str, str] = {
    "points_ledger_check_entry": """
CREATE FUNCTION points_ledger_check_entry() RETURNS trigger AS $$
DECLARE
    t points_ledger%ROWTYPE;
BEGIN
    IF NEW.kind = 'reversal' THEN
        SELECT * INTO t FROM points_ledger WHERE id = NEW.reverses_entry_id;
        IF NOT FOUND THEN
            RETURN NEW;  -- the foreign key reports it
        END IF;
        IF t.kind = 'reversal' OR t.user_id <> NEW.user_id OR t.deed_id <> NEW.deed_id
           OR t.role <> NEW.role OR NEW.points <> -t.points OR NEW.deed_units <> -t.deed_units
           OR NEW.counts_for_prize_rank <> t.counts_for_prize_rank
           OR NEW.region_id <> t.region_id OR NEW.verified_at <> t.verified_at
           OR NEW.earned_on <> t.earned_on OR NEW.period_month <> t.period_month THEN
            RAISE EXCEPTION 'points_ledger reversal must negate an award or decision of the '
                'same user, deed and role and copy its prize flag, region and dates'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF EXISTS (
        SELECT 1 FROM points_ledger AS e
        WHERE e.deed_id = NEW.deed_id AND e.role = NEW.role AND e.kind <> 'reversal'
          AND NOT EXISTS (SELECT 1 FROM points_ledger AS r WHERE r.reverses_entry_id = e.id)
    ) THEN
        RAISE EXCEPTION 'points_ledger: this deed already has an active % entry; reverse it first',
            NEW.role USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    "scoring_evidence_monotonic": """
CREATE FUNCTION scoring_evidence_monotonic() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'scoring_evidence rows are never deleted';
    END IF;
    IF NEW.deed_id IS DISTINCT FROM OLD.deed_id OR (OLD.first_photo_ready_at IS NOT NULL AND (
           NEW.first_photo_ready_at IS DISTINCT FROM OLD.first_photo_ready_at
           OR NEW.first_photo_id IS DISTINCT FROM OLD.first_photo_id)) THEN
        RAISE EXCEPTION 'scoring_evidence: the first photo is set once and never changed'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    "leaderboard_snapshots_final_frozen": """
CREATE FUNCTION leaderboard_snapshots_final_frozen() RETURNS trigger AS $$
BEGIN
    IF OLD.final THEN
        RAISE EXCEPTION 'a final leaderboard snapshot never changes';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    "leaderboard_entries_final_frozen": """
CREATE FUNCTION leaderboard_entries_final_frozen() RETURNS trigger AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM leaderboard_snapshots WHERE id = OLD.snapshot_id AND final) THEN
        RAISE EXCEPTION 'the entries of a final leaderboard snapshot never change';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    "forbid_except_revocation": """
CREATE FUNCTION forbid_except_revocation() RETURNS trigger AS $$
DECLARE
    revocation text[] := ARRAY['revoked_at', 'revoked_reason', 'revoked_by'];
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '% rows are never deleted', TG_TABLE_NAME;
    END IF;
    IF OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL
       OR (to_jsonb(NEW) - revocation) IS DISTINCT FROM (to_jsonb(OLD) - revocation) THEN
        RAISE EXCEPTION '% rows can only be revoked, once', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
}

# (trigger name, timing and events, table, function)
_TRIGGERS: tuple[tuple[str, str, str, str], ...] = (
    *(
        (f"{t}_append_only", "BEFORE UPDATE OR DELETE", t, "forbid_modification")
        for t in APPEND_ONLY_TABLES
    ),
    ("points_ledger_check_entry", "BEFORE INSERT", "points_ledger", "points_ledger_check_entry"),
    (
        "scoring_evidence_monotonic",
        "BEFORE UPDATE OR DELETE",
        "scoring_evidence",
        "scoring_evidence_monotonic",
    ),
    (
        "leaderboard_snapshots_final_frozen",
        "BEFORE UPDATE OR DELETE",
        "leaderboard_snapshots",
        "leaderboard_snapshots_final_frozen",
    ),
    (
        "leaderboard_entries_final_frozen",
        "BEFORE UPDATE OR DELETE",
        "leaderboard_entries",
        "leaderboard_entries_final_frozen",
    ),
    (
        "hero_titles_revoke_only",
        "BEFORE UPDATE OR DELETE",
        "hero_titles",
        "forbid_except_revocation",
    ),
    (
        "user_badges_revoke_only",
        "BEFORE UPDATE OR DELETE",
        "user_badges",
        "forbid_except_revocation",
    ),
)


def seed_config() -> None:
    """Insert ``scoring`` v1 and ``leaderboard`` v1, each only if its namespace has no rows yet
    (safe to re-run). created_by is NULL (a migration)."""
    bind = op.get_bind()
    for namespace, values, reason in SEEDS:
        bind.execute(
            sa.text(
                "INSERT INTO config_versions (namespace, version, values, reason) "
                "SELECT CAST(:namespace AS varchar), 1, CAST(:values AS jsonb), :reason "
                "WHERE NOT EXISTS "
                "(SELECT 1 FROM config_versions WHERE namespace = CAST(:namespace AS varchar))"
            ),
            {"namespace": namespace, "values": json.dumps(values), "reason": reason},
        )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "month_finalizations",
        sa.Column("period_month", sa.Date(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "finalized_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_count", sa.Integer(), nullable=False),
        sa.Column("titles_awarded", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "period_month = CAST(date_trunc('month', period_month) AS date)",
            name=op.f("ck_month_finalizations_first_of_month"),
        ),
        sa.CheckConstraint(
            "config_version >= 1", name=op.f("ck_month_finalizations_config_version_positive")
        ),
        sa.CheckConstraint(
            "finalized_at >= cutoff_at", name=op.f("ck_month_finalizations_after_cutoff")
        ),
        sa.CheckConstraint(
            "snapshot_count >= 0 AND titles_awarded >= 0",
            name=op.f("ck_month_finalizations_counts_non_negative"),
        ),
        sa.PrimaryKeyConstraint("period_month", name=op.f("pk_month_finalizations")),
    )
    op.create_table(
        "leaderboard_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.Date(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("final", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "period_month = CAST(date_trunc('month', period_month) AS date)",
            name=op.f("ck_leaderboard_snapshots_first_of_month"),
        ),
        sa.CheckConstraint(
            "config_version >= 1", name=op.f("ck_leaderboard_snapshots_config_version_positive")
        ),
        sa.CheckConstraint(
            "entry_count >= 0", name=op.f("ck_leaderboard_snapshots_entry_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["region_id"],
            ["regions.id"],
            name=op.f("fk_leaderboard_snapshots_region_id_regions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leaderboard_snapshots")),
    )
    op.create_index(
        "ix_leaderboard_snapshots_region_month_computed",
        "leaderboard_snapshots",
        ["region_id", "period_month", sa.literal_column("computed_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_leaderboard_snapshots_final",
        "leaderboard_snapshots",
        ["region_id", "period_month"],
        unique=True,
        postgresql_where=sa.text("final"),
    )
    op.create_table(
        "hero_titles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("period_month", sa.Date(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column(
            "level_kind",
            sa.Enum(
                "country",
                "state",
                "city",
                "ward",
                name="title_level",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("place", sa.Integer(), nullable=False),
        sa.Column("title_key", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column(
            "awarded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "period_month = CAST(date_trunc('month', period_month) AS date)",
            name=op.f("ck_hero_titles_first_of_month"),
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)",
            name=op.f("ck_hero_titles_revoked_has_reason"),
        ),
        sa.CheckConstraint(
            "config_version >= 1", name=op.f("ck_hero_titles_config_version_positive")
        ),
        sa.CheckConstraint("place >= 1", name=op.f("ck_hero_titles_place_positive")),
        sa.CheckConstraint(
            "revoked_at IS NOT NULL OR revoked_by IS NULL",
            name=op.f("ck_hero_titles_revoked_by_only_if_revoked"),
        ),
        sa.ForeignKeyConstraint(
            ["region_id"],
            ["regions.id"],
            name=op.f("fk_hero_titles_region_id_regions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["users.id"],
            name=op.f("fk_hero_titles_revoked_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["leaderboard_snapshots.id"],
            name=op.f("fk_hero_titles_snapshot_id_leaderboard_snapshots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_hero_titles_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hero_titles")),
        sa.UniqueConstraint(
            "period_month", "region_id", "user_id", name="uq_hero_titles_month_region_user"
        ),
    )
    op.create_index(
        "ix_hero_titles_region_month", "hero_titles", ["region_id", "period_month"], unique=False
    )
    op.create_index("ix_hero_titles_user_id", "hero_titles", ["user_id"], unique=False)
    op.create_table(
        "leaderboard_entries",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("rank_score", sa.Integer(), nullable=False),
        sa.Column("deed_count", sa.Integer(), nullable=False),
        sa.Column("doer_points", sa.Integer(), nullable=False),
        sa.Column("distinct_reporters", sa.Integer(), nullable=False),
        sa.Column("distinct_days", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "deed_count >= 1", name=op.f("ck_leaderboard_entries_deed_count_positive")
        ),
        sa.CheckConstraint(
            "distinct_reporters >= 0 AND distinct_days >= 0",
            name=op.f("ck_leaderboard_entries_distinct_non_negative"),
        ),
        sa.CheckConstraint("rank >= 1", name=op.f("ck_leaderboard_entries_rank_positive")),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["leaderboard_snapshots.id"],
            name=op.f("fk_leaderboard_entries_snapshot_id_leaderboard_snapshots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_leaderboard_entries_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "user_id", name="pk_leaderboard_entries"),
    )
    op.create_index(
        "ix_leaderboard_entries_snapshot_rank",
        "leaderboard_entries",
        ["snapshot_id", "rank"],
        unique=False,
    )
    op.create_table(
        "impact_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column(
            "method",
            sa.Enum(
                "rule_based",
                "ai",
                name="evaluation_method",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "mode",
            sa.Enum(
                "live",
                "shadow",
                name="evaluation_mode",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "proposed",
                name="evaluation_status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            server_default="proposed",
            nullable=False,
        ),
        sa.Column("rule_points", sa.Integer(), nullable=True),
        sa.Column(
            "text_check",
            sa.Enum(
                "ok",
                "category_mismatch",
                "spam_or_empty",
                name="text_check",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=True,
        ),
        sa.Column("impact_score", sa.SmallInteger(), nullable=True),
        sa.Column("raw_score", sa.SmallInteger(), nullable=True),
        sa.Column("sub_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "evidence_level",
            sa.Enum(
                "text_only",
                "photo",
                "corroborated",
                name="evidence_level",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "evidence_cap_applied", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("impact_explanation", sa.Text(), nullable=True),
        sa.Column("estimated_beneficiaries", sa.Integer(), nullable=True),
        sa.Column("evaluation_confidence", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column(
            "human_review_required", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("model_version", sa.String(length=80), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=True),
        sa.Column("scoring_config_version", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.CHAR(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "input_hash IS NULL OR input_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_impact_evaluations_input_hash_hex"),
        ),
        sa.CheckConstraint(
            "method <> 'ai' OR (impact_score IS NOT NULL AND raw_score IS NOT NULL AND sub_scores IS NOT NULL AND input_hash IS NOT NULL AND model_version IS NOT NULL AND prompt_version IS NOT NULL AND rule_points IS NULL AND text_check IS NULL)",
            name=op.f("ck_impact_evaluations_ai_fields"),
        ),
        sa.CheckConstraint(
            "method <> 'ai' OR mode = 'shadow'", name=op.f("ck_impact_evaluations_ai_is_shadow")
        ),
        sa.CheckConstraint(
            "method <> 'rule_based' OR (rule_points IS NOT NULL AND text_check IS NOT NULL AND impact_score IS NULL AND raw_score IS NULL AND sub_scores IS NULL AND input_hash IS NULL)",
            name=op.f("ck_impact_evaluations_rule_based_fields"),
        ),
        sa.CheckConstraint(
            "method <> 'rule_based' OR mode = 'live'",
            name=op.f("ck_impact_evaluations_rule_based_is_live"),
        ),
        sa.CheckConstraint(
            "sub_scores IS NULL OR jsonb_typeof(sub_scores) = 'object'",
            name=op.f("ck_impact_evaluations_sub_scores_object"),
        ),
        sa.CheckConstraint(
            "(impact_score IS NULL OR impact_score BETWEEN 0 AND 100) AND (raw_score IS NULL OR raw_score BETWEEN 0 AND 100) AND (impact_score IS NULL OR raw_score IS NULL OR impact_score <= raw_score)",
            name=op.f("ck_impact_evaluations_scores_valid"),
        ),
        sa.CheckConstraint(
            "estimated_beneficiaries IS NULL OR estimated_beneficiaries >= 0",
            name=op.f("ck_impact_evaluations_beneficiaries_non_negative"),
        ),
        sa.CheckConstraint(
            "evaluation_confidence IS NULL OR evaluation_confidence BETWEEN 0 AND 1",
            name=op.f("ck_impact_evaluations_confidence_range"),
        ),
        sa.CheckConstraint(
            "impact_explanation IS NULL OR length(impact_explanation) <= 1500",
            name=op.f("ck_impact_evaluations_explanation_length"),
        ),
        sa.CheckConstraint(
            "rule_points IS NULL OR rule_points >= 0",
            name=op.f("ck_impact_evaluations_rule_points_non_negative"),
        ),
        sa.CheckConstraint(
            "scoring_config_version >= 1",
            name=op.f("ck_impact_evaluations_config_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"],
            ["deeds.id"],
            name=op.f("fk_impact_evaluations_deed_id_deeds"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_impact_evaluations")),
    )
    op.create_index(
        "ix_impact_evaluations_deed_created_at",
        "impact_evaluations",
        ["deed_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "scoring_evidence",
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column("first_photo_ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_photo_id", sa.Uuid(), nullable=True),
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
            "(first_photo_ready_at IS NULL) = (first_photo_id IS NULL)",
            name=op.f("ck_scoring_evidence_photo_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"],
            ["deeds.id"],
            name=op.f("fk_scoring_evidence_deed_id_deeds"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("deed_id", name=op.f("pk_scoring_evidence")),
    )
    op.create_table(
        "user_badges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("badge_slug", sa.String(length=40), nullable=False),
        sa.Column(
            "awarded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("trigger_deed_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)",
            name=op.f("ck_user_badges_revoked_has_reason"),
        ),
        sa.CheckConstraint(
            "config_version >= 1", name=op.f("ck_user_badges_config_version_positive")
        ),
        sa.CheckConstraint(
            "revoked_at IS NOT NULL OR revoked_by IS NULL",
            name=op.f("ck_user_badges_revoked_by_only_if_revoked"),
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["users.id"],
            name=op.f("fk_user_badges_revoked_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trigger_deed_id"],
            ["deeds.id"],
            name=op.f("fk_user_badges_trigger_deed_id_deeds"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_badges_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_badges")),
        sa.UniqueConstraint("user_id", "badge_slug", name="uq_user_badges_user_badge"),
    )
    op.create_table(
        "points_ledger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("deed_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "doer",
                "reporter",
                name="ledger_role",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "award",
                "decision",
                "reversal",
                name="ledger_kind",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("deed_units", sa.SmallInteger(), nullable=False),
        sa.Column("counts_for_prize_rank", sa.Boolean(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
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
                name="ledger_reason",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column(
            "breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("counterpart_user_id", sa.Uuid(), nullable=True),
        sa.Column("reverses_entry_id", sa.Uuid(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("earned_on", sa.Date(), nullable=False),
        sa.Column("period_month", sa.Date(), nullable=False),
        sa.Column("scoring_config_version", sa.Integer(), nullable=False),
        sa.Column("evaluation_id", sa.Uuid(), nullable=True),
        sa.Column("source_event_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'award' AND deed_units IN (0, 1)) OR (kind = 'decision' AND deed_units = 0) OR (kind = 'reversal' AND deed_units IN (-1, 0))",
            name=op.f("ck_points_ledger_deed_units_by_kind"),
        ),
        sa.CheckConstraint(
            "(kind = 'award' AND points > 0) OR (kind = 'decision' AND points = 0) OR (kind = 'reversal' AND points <= 0)",
            name=op.f("ck_points_ledger_points_by_kind"),
        ),
        sa.CheckConstraint(
            "(kind = 'reversal') = (reverses_entry_id IS NOT NULL)",
            name=op.f("ck_points_ledger_reversal_has_target"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(breakdown) = 'object'", name=op.f("ck_points_ledger_breakdown_object")
        ),
        sa.CheckConstraint(
            "period_month = CAST(date_trunc('month', earned_on) AS date)",
            name=op.f("ck_points_ledger_period_month_of_earned_on"),
        ),
        sa.CheckConstraint(
            "role <> 'reporter' OR NOT counts_for_prize_rank",
            name=op.f("ck_points_ledger_reporter_never_prize_rank"),
        ),
        sa.CheckConstraint(
            "role = 'doer' OR deed_units = 0", name=op.f("ck_points_ledger_reporter_no_deed_units")
        ),
        sa.CheckConstraint(
            "counterpart_user_id IS NULL OR counterpart_user_id <> user_id",
            name=op.f("ck_points_ledger_counterpart_not_self"),
        ),
        sa.CheckConstraint(
            "scoring_config_version >= 1", name=op.f("ck_points_ledger_config_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["counterpart_user_id"],
            ["users.id"],
            name=op.f("fk_points_ledger_counterpart_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deed_id"],
            ["deeds.id"],
            name=op.f("fk_points_ledger_deed_id_deeds"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["impact_evaluations.id"],
            name=op.f("fk_points_ledger_evaluation_id_impact_evaluations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["region_id"],
            ["regions.id"],
            name=op.f("fk_points_ledger_region_id_regions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reverses_entry_id"],
            ["points_ledger.id"],
            name=op.f("fk_points_ledger_reverses_entry_id_points_ledger"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_points_ledger_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_points_ledger")),
        sa.UniqueConstraint("reverses_entry_id", name="uq_points_ledger_reverses_entry_id"),
    )
    op.create_index(
        "ix_points_ledger_board",
        "points_ledger",
        ["period_month", "region_id", "user_id"],
        unique=False,
        postgresql_where=sa.text("role = 'doer' AND counts_for_prize_rank"),
    )
    op.create_index(
        "ix_points_ledger_deed_role", "points_ledger", ["deed_id", "role"], unique=False
    )
    op.create_index(
        "ix_points_ledger_user_created_at",
        "points_ledger",
        ["user_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_points_ledger_user_day_role",
        "points_ledger",
        ["user_id", "earned_on", "role"],
        unique=False,
    )
    for sql in _FUNCTIONS.values():
        op.execute(sql)
    for name, when, table, fn in _TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} {when} ON {table} FOR EACH ROW EXECUTE FUNCTION {fn}()")
    seed_config()


def downgrade() -> None:
    """Downgrade schema."""
    # config_versions is append-only, so its trigger is bypassed for these two namespaces. Any
    # later published versions go too: before 0020 the namespaces did not exist.
    op.execute("ALTER TABLE config_versions DISABLE TRIGGER config_versions_append_only")
    for namespace, _, _ in SEEDS:
        op.execute(f"DELETE FROM config_versions WHERE namespace = '{namespace}'")
    op.execute("ALTER TABLE config_versions ENABLE TRIGGER config_versions_append_only")
    for name, _, table, _ in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    # Indexes go with their tables; points_ledger first (it references impact_evaluations).
    for table in reversed(TABLES):
        op.drop_table(table)
    for fn in _FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
