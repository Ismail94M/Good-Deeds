"""Scoring tables: evidence, impact evaluations, the points ledger, leaderboard snapshots, month
finalizations, Kindness Hero titles and badges (docs/M4-DESIGN.md §2.3, migration 0020).

Invariants live in the database (migration 0020); services map ``IntegrityError`` to error codes
by constraint name, so names here are part of the contract.

- ``points_ledger``, ``impact_evaluations`` and ``month_finalizations`` are append-only
  (``forbid_modification()``). A change of points is a reversal row (D48), checked by the
  ``points_ledger_check_entry`` trigger: a reversal negates exactly one earlier award or decision
  of the same user, deed and role and copies its prize flag, region and dates; an award or
  decision is accepted only when the (deed, role) has no other active (unreversed) entry, so the
  reversal of the old entry must be inserted **before** the new award.
- ``scoring_evidence`` is monotonic (D51): once set, the first photo can't change or be cleared,
  and rows are never deleted.
- Final ``leaderboard_snapshots`` and their entries never change (D64).
- ``hero_titles`` and ``user_badges`` are permanent: the only allowed change is a revocation
  (``revoked_*`` set once, M5); rows are never deleted (D70).

Points are only ever summed from ``points_ledger``; no table here stores a description,
coordinates or a phone number. Foreign keys to ``users``, ``deeds`` and ``regions`` use RESTRICT
and are named by string, so this module imports nothing from ``deeds`` or ``identity`` (D47).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _enum(cls: type[StrEnum], name: str, length: int = 20) -> Enum:
    """A non-native enum stored as a string, with a CHECK constraint named ``ck_<table>_<name>``."""
    return Enum(
        cls,
        create_constraint=True,
        name=name,
        native_enum=False,
        length=length,
        values_callable=lambda e: [m.value for m in e],
    )


def _users_fk() -> ForeignKey:
    return ForeignKey("users.id", ondelete="RESTRICT")


def _deeds_fk() -> ForeignKey:
    return ForeignKey("deeds.id", ondelete="RESTRICT")


def _regions_fk() -> ForeignKey:
    return ForeignKey("regions.id", ondelete="RESTRICT")


# Shared CHECK fragment: a ``period_month`` is the first day of its month.
_FIRST_OF_MONTH = "period_month = CAST(date_trunc('month', period_month) AS date)"


class EvaluationMethod(StrEnum):
    RULE_BASED = "rule_based"
    AI = "ai"


class EvaluationMode(StrEnum):
    LIVE = "live"
    SHADOW = "shadow"


class EvaluationStatus(StrEnum):
    # M5 adds ``approved`` and ``overridden`` (as new rows).
    PROPOSED = "proposed"


class TextCheck(StrEnum):
    OK = "ok"
    CATEGORY_MISMATCH = "category_mismatch"
    SPAM_OR_EMPTY = "spam_or_empty"


class EvidenceLevel(StrEnum):
    TEXT_ONLY = "text_only"
    PHOTO = "photo"
    CORROBORATED = "corroborated"


class LedgerRole(StrEnum):
    DOER = "doer"
    REPORTER = "reporter"


class LedgerKind(StrEnum):
    AWARD = "award"
    DECISION = "decision"
    REVERSAL = "reversal"


class LedgerReason(StrEnum):
    # award
    VERIFIED = "verified"
    PROMOTED = "promoted"
    EDITED = "edited"
    PRIZE_RANK_CHANGED = "prize_rank_changed"
    PHOTO_EVIDENCE = "photo_evidence"
    # decision (zero points)
    DOER_DAILY_CAP = "doer_daily_cap"
    REPORTER_DAILY_CAP = "reporter_daily_cap"
    # reversal (a re-score's reversal takes the reason of the new award)
    WITHDRAWN = "withdrawn"
    REJECTED = "rejected"
    REMOVED = "removed"
    COUNTS_FOR_POINTS_LOST = "counts_for_points_lost"


class TitleLevel(StrEnum):
    """The region kind a title was won at (same values as ``deeds`` ``RegionKind``)."""

    COUNTRY = "country"
    STATE = "state"
    CITY = "city"
    WARD = "ward"


class ScoringEvidence(Base):
    """One row per deed with evidence. ``first_photo_ready_at`` is a high-water mark (D51): set
    once by the ``DeedPhotoReady`` handler, never changed or cleared (trigger
    ``scoring_evidence_monotonic``)."""

    __tablename__ = "scoring_evidence"
    __table_args__ = (
        CheckConstraint(
            "(first_photo_ready_at IS NULL) = (first_photo_id IS NULL)", name="photo_pair"
        ),
    )

    deed_id: Mapped[uuid.UUID] = mapped_column(_deeds_fk(), primary_key=True)
    first_photo_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # No foreign key: kept for audit even after the photo is purged.
    first_photo_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImpactEvaluation(Base):
    """One evaluation of one counted report (D54, D62). Append-only. Rule-based rows are live;
    AI rows (M4b) can only ever be shadow (CHECK ``ai_is_shadow``)."""

    __tablename__ = "impact_evaluations"
    __table_args__ = (
        CheckConstraint("method <> 'ai' OR mode = 'shadow'", name="ai_is_shadow"),
        CheckConstraint("method <> 'rule_based' OR mode = 'live'", name="rule_based_is_live"),
        CheckConstraint(
            "method <> 'rule_based' OR (rule_points IS NOT NULL AND text_check IS NOT NULL "
            "AND impact_score IS NULL AND raw_score IS NULL AND sub_scores IS NULL "
            "AND input_hash IS NULL)",
            name="rule_based_fields",
        ),
        CheckConstraint(
            "method <> 'ai' OR (impact_score IS NOT NULL AND raw_score IS NOT NULL "
            "AND sub_scores IS NOT NULL AND input_hash IS NOT NULL AND model_version IS NOT NULL "
            "AND prompt_version IS NOT NULL AND rule_points IS NULL AND text_check IS NULL)",
            name="ai_fields",
        ),
        CheckConstraint("rule_points IS NULL OR rule_points >= 0", name="rule_points_non_negative"),
        CheckConstraint(
            "(impact_score IS NULL OR impact_score BETWEEN 0 AND 100) "
            "AND (raw_score IS NULL OR raw_score BETWEEN 0 AND 100) "
            "AND (impact_score IS NULL OR raw_score IS NULL OR impact_score <= raw_score)",
            name="scores_valid",
        ),
        CheckConstraint(
            "sub_scores IS NULL OR jsonb_typeof(sub_scores) = 'object'", name="sub_scores_object"
        ),
        CheckConstraint(
            "impact_explanation IS NULL OR length(impact_explanation) <= 1500",
            name="explanation_length",
        ),
        CheckConstraint(
            "estimated_beneficiaries IS NULL OR estimated_beneficiaries >= 0",
            name="beneficiaries_non_negative",
        ),
        CheckConstraint(
            "evaluation_confidence IS NULL OR evaluation_confidence BETWEEN 0 AND 1",
            name="confidence_range",
        ),
        CheckConstraint(
            "input_hash IS NULL OR input_hash ~ '^[0-9a-f]{64}$'", name="input_hash_hex"
        ),
        CheckConstraint("scoring_config_version >= 1", name="config_version_positive"),
        Index("ix_impact_evaluations_deed_created_at", "deed_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    deed_id: Mapped[uuid.UUID] = mapped_column(_deeds_fk())
    method: Mapped[EvaluationMethod] = mapped_column(_enum(EvaluationMethod, "evaluation_method"))
    mode: Mapped[EvaluationMode] = mapped_column(_enum(EvaluationMode, "evaluation_mode"))
    status: Mapped[EvaluationStatus] = mapped_column(
        _enum(EvaluationStatus, "evaluation_status"), server_default=EvaluationStatus.PROPOSED.value
    )
    # rule_based: points before caps and the pair multiplier
    rule_points: Mapped[int | None] = mapped_column(Integer)
    text_check: Mapped[TextCheck | None] = mapped_column(_enum(TextCheck, "text_check"))
    # ai (M4b): after / before the evidence cap, 0-100
    impact_score: Mapped[int | None] = mapped_column(SmallInteger)
    raw_score: Mapped[int | None] = mapped_column(SmallInteger)
    sub_scores: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evidence_level: Mapped[EvidenceLevel] = mapped_column(_enum(EvidenceLevel, "evidence_level"))
    evidence_cap_applied: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Admin-only (never in a member response).
    impact_explanation: Mapped[str | None] = mapped_column(Text)
    estimated_beneficiaries: Mapped[int | None] = mapped_column(Integer)
    evaluation_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    human_review_required: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    model_version: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    scoring_config_version: Mapped[int] = mapped_column(Integer)
    input_hash: Mapped[str | None] = mapped_column(CHAR(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PointsLedger(Base):
    """The points ledger (D48 to D58). Append-only; totals are ``SUM(points)``.

    ``award`` > 0 points, ``decision`` = 0 (a capped outcome), ``reversal`` = minus the reversed
    entry (``reverses_entry_id``, unique: an entry is reversed at most once). Reporter rows never
    count for prize rank (D57). ``earned_on`` / ``period_month`` are the IST day and month of
    verification (D58); ``region_id`` is the deed's leaf region frozen at the first decision
    (D66). ``deed_units`` is 1 on a doer award that counts as a counted deed.
    """

    __tablename__ = "points_ledger"
    __table_args__ = (
        CheckConstraint(
            "role <> 'reporter' OR NOT counts_for_prize_rank", name="reporter_never_prize_rank"
        ),
        CheckConstraint(
            "(kind = 'reversal') = (reverses_entry_id IS NOT NULL)", name="reversal_has_target"
        ),
        CheckConstraint(
            "(kind = 'award' AND points > 0) OR (kind = 'decision' AND points = 0) "
            "OR (kind = 'reversal' AND points <= 0)",
            name="points_by_kind",
        ),
        CheckConstraint(
            "(kind = 'award' AND deed_units IN (0, 1)) OR (kind = 'decision' AND deed_units = 0) "
            "OR (kind = 'reversal' AND deed_units IN (-1, 0))",
            name="deed_units_by_kind",
        ),
        CheckConstraint("role = 'doer' OR deed_units = 0", name="reporter_no_deed_units"),
        CheckConstraint("jsonb_typeof(breakdown) = 'object'", name="breakdown_object"),
        CheckConstraint(
            "period_month = CAST(date_trunc('month', earned_on) AS date)",
            name="period_month_of_earned_on",
        ),
        CheckConstraint(
            "counterpart_user_id IS NULL OR counterpart_user_id <> user_id",
            name="counterpart_not_self",
        ),
        CheckConstraint("scoring_config_version >= 1", name="config_version_positive"),
        UniqueConstraint("reverses_entry_id", name="uq_points_ledger_reverses_entry_id"),
        Index("ix_points_ledger_deed_role", "deed_id", "role"),
        Index(
            "ix_points_ledger_board",
            "period_month",
            "region_id",
            "user_id",
            postgresql_where=text("role = 'doer' AND counts_for_prize_rank"),
        ),
        Index("ix_points_ledger_user_day_role", "user_id", "earned_on", "role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    deed_id: Mapped[uuid.UUID] = mapped_column(_deeds_fk())
    role: Mapped[LedgerRole] = mapped_column(_enum(LedgerRole, "ledger_role"))
    kind: Mapped[LedgerKind] = mapped_column(_enum(LedgerKind, "ledger_kind"))
    points: Mapped[int] = mapped_column(Integer)
    deed_units: Mapped[int] = mapped_column(SmallInteger)
    counts_for_prize_rank: Mapped[bool] = mapped_column(Boolean)
    region_id: Mapped[int] = mapped_column(_regions_fk())
    reason: Mapped[LedgerReason] = mapped_column(_enum(LedgerReason, "ledger_reason", length=40))
    # {base, text_check, photo_bonus, pair_ordinal, pair_percent, cap}: numbers and codes only.
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # The reporter on doer rows, the doer on reporter rows.
    counterpart_user_id: Mapped[uuid.UUID | None] = mapped_column(_users_fk())
    reverses_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("points_ledger.id", ondelete="RESTRICT")
    )
    # Copied from the deed; provisional until verified_at + deeds.withdraw_window_hours.
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    earned_on: Mapped[date] = mapped_column(Date)
    period_month: Mapped[date] = mapped_column(Date)
    scoring_config_version: Mapped[int] = mapped_column(Integer)
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("impact_evaluations.id", ondelete="RESTRICT")
    )
    source_event_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_points_ledger_user_created_at", PointsLedger.user_id, PointsLedger.created_at.desc())


class MonthFinalization(Base):
    """Written once by ``scoring.finalize_month`` (D64). Append-only."""

    __tablename__ = "month_finalizations"
    __table_args__ = (
        CheckConstraint(_FIRST_OF_MONTH, name="first_of_month"),
        CheckConstraint("config_version >= 1", name="config_version_positive"),
        CheckConstraint("snapshot_count >= 0 AND titles_awarded >= 0", name="counts_non_negative"),
        CheckConstraint("finalized_at >= cutoff_at", name="after_cutoff"),
    )

    period_month: Mapped[date] = mapped_column(Date, primary_key=True)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # The ``leaderboard`` config version used.
    config_version: Mapped[int] = mapped_column(Integer)
    snapshot_count: Mapped[int] = mapped_column(Integer)
    titles_awarded: Mapped[int] = mapped_column(Integer)


class LeaderboardSnapshot(Base):
    """A computed board for one region and month (D59). Provisional rows are replaced by the
    refresh job; at most one final row per (region, month), and a final row never changes
    (trigger ``leaderboard_snapshots_final_frozen``)."""

    __tablename__ = "leaderboard_snapshots"
    __table_args__ = (
        CheckConstraint(_FIRST_OF_MONTH, name="first_of_month"),
        CheckConstraint("entry_count >= 0", name="entry_count_non_negative"),
        CheckConstraint("config_version >= 1", name="config_version_positive"),
        Index(
            "uq_leaderboard_snapshots_final",
            "region_id",
            "period_month",
            unique=True,
            postgresql_where=text("final"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    region_id: Mapped[int] = mapped_column(_regions_fk())
    period_month: Mapped[date] = mapped_column(Date)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # The ``leaderboard`` config version used.
    config_version: Mapped[int] = mapped_column(Integer)
    final: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    entry_count: Mapped[int] = mapped_column(Integer)


# The latest snapshot of a board.
Index(
    "ix_leaderboard_snapshots_region_month_computed",
    LeaderboardSnapshot.region_id,
    LeaderboardSnapshot.period_month,
    LeaderboardSnapshot.computed_at.desc(),
)


class LeaderboardEntry(Base):
    """One user's row on a snapshot. Shared ranks repeat (D67, dense by config). ``doer_points``
    and ``rank_score`` are internal and never in a public response (D69)."""

    __tablename__ = "leaderboard_entries"
    __table_args__ = (
        PrimaryKeyConstraint("snapshot_id", "user_id", name="pk_leaderboard_entries"),
        CheckConstraint("rank >= 1", name="rank_positive"),
        CheckConstraint("deed_count >= 1", name="deed_count_positive"),
        CheckConstraint(
            "distinct_reporters >= 0 AND distinct_days >= 0", name="distinct_non_negative"
        ),
        Index("ix_leaderboard_entries_snapshot_rank", "snapshot_id", "rank"),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leaderboard_snapshots.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    rank: Mapped[int] = mapped_column(Integer)
    rank_score: Mapped[int] = mapped_column(Integer)
    deed_count: Mapped[int] = mapped_column(Integer)
    doer_points: Mapped[int] = mapped_column(Integer)
    distinct_reporters: Mapped[int] = mapped_column(Integer)
    distinct_days: Mapped[int] = mapped_column(Integer)


class HeroTitle(Base):
    """A Kindness Hero title from a final snapshot (D64, D70). One per (month, region, user);
    a user may hold several in a month (ward and city). Only a revocation may change it (M5)."""

    __tablename__ = "hero_titles"
    __table_args__ = (
        UniqueConstraint(
            "period_month", "region_id", "user_id", name="uq_hero_titles_month_region_user"
        ),
        CheckConstraint(_FIRST_OF_MONTH, name="first_of_month"),
        CheckConstraint("place >= 1", name="place_positive"),
        CheckConstraint("config_version >= 1", name="config_version_positive"),
        CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)", name="revoked_has_reason"
        ),
        CheckConstraint(
            "revoked_at IS NOT NULL OR revoked_by IS NULL", name="revoked_by_only_if_revoked"
        ),
        Index("ix_hero_titles_user_id", "user_id"),
        Index("ix_hero_titles_region_month", "region_id", "period_month"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    period_month: Mapped[date] = mapped_column(Date)
    region_id: Mapped[int] = mapped_column(_regions_fk())
    level_kind: Mapped[TitleLevel] = mapped_column(_enum(TitleLevel, "title_level"))
    place: Mapped[int] = mapped_column(Integer)
    # The title's key in the level config, e.g. ``hero`` or ``top3``.
    title_key: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leaderboard_snapshots.id", ondelete="RESTRICT")
    )
    awarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The ``leaderboard`` config version used.
    config_version: Mapped[int] = mapped_column(Integer)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(_users_fk())


class UserBadge(Base):
    """A badge, awarded once the deed that crossed its threshold is past its window (D70).
    One per (user, badge). Only a revocation may change it (M5)."""

    __tablename__ = "user_badges"
    __table_args__ = (
        UniqueConstraint("user_id", "badge_slug", name="uq_user_badges_user_badge"),
        CheckConstraint("config_version >= 1", name="config_version_positive"),
        CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)", name="revoked_has_reason"
        ),
        CheckConstraint(
            "revoked_at IS NOT NULL OR revoked_by IS NULL", name="revoked_by_only_if_revoked"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    badge_slug: Mapped[str] = mapped_column(String(40))
    awarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The ``leaderboard`` config version used.
    config_version: Mapped[int] = mapped_column(Integer)
    trigger_deed_id: Mapped[uuid.UUID | None] = mapped_column(_deeds_fk())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(_users_fk())
