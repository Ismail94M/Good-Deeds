"""Fraud tables: flag cases, their users and deeds, prize holds, review decisions and per-deed
outcomes, explanation requests, decision notices, appeals and detector runs (docs/M5-DESIGN.md
§2.1, migration 0026).

Invariants live in the database (migration 0026); services map ``IntegrityError`` to error codes
by constraint name, so names here are part of the contract.

- A flag never edits a deed or a ledger row (D79). A deed is *held* while any of its
  ``prize_holds`` rows is unreleased; holds come only from a confirmed decision (D80).
- One live (``open`` or ``parked``) case per (type, subject set): partial unique index
  ``uq_flag_cases_live`` (D81). ``type`` and ``subject_key`` never change, and a resolved case
  never changes again (trigger ``flag_cases_guard``).
- ``flag_case_users``, ``flag_case_deeds`` and ``concentration_signals`` (scoring) are
  insert-only; ``flag_case_decisions`` and ``flag_case_deed_outcomes`` are append-only (D88). All
  use ``forbid_modification()``.
- ``prize_holds``, ``explanation_requests``, ``decision_notices`` and ``appeals`` are never
  deleted, and only their "closing" columns may be set, once (trigger function
  ``forbid_except_set_once``): a release, a reply, the final time, an appeal decision.
- Composite foreign keys keep rows inside their case: a per-deed outcome is for a deed of the
  case, an explanation request and a case notice are for a subject of the case, and an appeal is
  by the person the notice is for.

No table stores coordinates, phone numbers, device or IP hashes, or descriptions. Foreign keys to
``users`` and ``deeds`` use RESTRICT and are named by string, so this module imports nothing from
``deeds`` or ``identity``. ``decision_notices.override_id`` has no foreign key: ``scoring`` owns
score overrides (D78).
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
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


def _cases_fk() -> ForeignKey:
    return ForeignKey("flag_cases.id", ondelete="RESTRICT")


# Shared CHECK fragment: a written note (the service enforces ``note_min_chars`` from config).
_NOTE_MIN = "length(btrim(note)) >= 5"


class FlagCaseType(StrEnum):
    RECIPROCAL = "reciprocal"
    RING = "ring"
    CONCENTRATION = "concentration"
    INVITE_ANOMALY = "invite_anomaly"
    CONTENT = "content"


class FlagCaseStatus(StrEnum):
    OPEN = "open"
    PARKED = "parked"
    RESOLVED = "resolved"


class FlagCaseOutcome(StrEnum):
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


class HoldReleaseReason(StrEnum):
    RELEASED_ON_CONFIRM = "released_on_confirm"
    APPEAL_OVERTURNED = "appeal_overturned"


class FlagCaseAction(StrEnum):
    PARK = "park"
    UNPARK = "unpark"
    REQUEST_EXPLANATION = "request_explanation"
    CONFIRM = "confirm"
    DISMISS = "dismiss"


class DeedOutcome(StrEnum):
    EXCLUDE = "exclude"
    REJECT = "reject"
    RELEASE = "release"


class NoticeKind(StrEnum):
    CASE = "case"
    OVERRIDE = "override"


class AppealOutcome(StrEnum):
    UPHELD = "upheld"
    OVERTURNED = "overturned"


class DetectorRunTrigger(StrEnum):
    SWEEP = "sweep"
    CLI = "cli"
    # A per-deed check that was truncated (SEC5-M6, migration 0029); only those are recorded.
    EVENT = "event"


class DetectorRunStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    TRUNCATED = "truncated"
    FAILED = "failed"


class FlagCase(Base):
    """A pattern worth an admin's look (D81, D88). ``subject_key`` is the SHA-256 hex of the type
    and the sorted subject user ids. ``deed_count``, ``held_count``, ``review_by`` and
    ``updated_at`` are maintained under the case row lock."""

    __tablename__ = "flag_cases"
    __table_args__ = (
        CheckConstraint(
            "(status = 'resolved') = (outcome IS NOT NULL AND resolved_at IS NOT NULL "
            "AND resolved_by IS NOT NULL)",
            name="resolved_fields",
        ),
        CheckConstraint("subject_key ~ '^[0-9a-f]{64}$'", name="subject_key_hex"),
        CheckConstraint("jsonb_typeof(signal) = 'object'", name="signal_object"),
        CheckConstraint("detector_config_version >= 1", name="config_version_positive"),
        CheckConstraint(
            "deed_count >= 0 AND held_count >= 0 AND held_count <= deed_count",
            name="counts_valid",
        ),
        # D81: one live case per (type, subject set); a new matching deed joins it.
        Index(
            "uq_flag_cases_live",
            "type",
            "subject_key",
            unique=True,
            postgresql_where=text("status IN ('open', 'parked')"),
        ),
        # The queue: open/parked by review_by (ascending b-tree order puts NULLs last), then age.
        Index("ix_flag_cases_queue", "status", "review_by", "opened_at"),
        Index("ix_flag_cases_type_status", "type", "status"),
        # D81 step 2: deeds of an earlier dismissed case with the same key are not re-flagged.
        Index("ix_flag_cases_type_subject_key", "type", "subject_key"),
        # The resolved tab, newest first.
        Index(
            "ix_flag_cases_resolved_at",
            "resolved_at",
            postgresql_where=text("status = 'resolved'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    type: Mapped[FlagCaseType] = mapped_column(_enum(FlagCaseType, "flag_case_type"))
    status: Mapped[FlagCaseStatus] = mapped_column(
        _enum(FlagCaseStatus, "flag_case_status"), server_default=FlagCaseStatus.OPEN.value
    )
    outcome: Mapped[FlagCaseOutcome | None] = mapped_column(
        _enum(FlagCaseOutcome, "flag_case_outcome")
    )
    subject_key: Mapped[str] = mapped_column(CHAR(64))
    # Numbers and codes only, per type (§2.1).
    signal: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # The ``fraud`` config version that opened it.
    detector_config_version: Mapped[int] = mapped_column(Integer)
    # Earliest (IST month end + 24 h) of its deeds' months not yet final; null when all are.
    review_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deed_count: Mapped[int] = mapped_column(Integer, server_default="0")
    held_count: Mapped[int] = mapped_column(Integer, server_default="0")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(_users_fk())


class FlagCaseUser(Base):
    """A subject of a case. Insert-only."""

    __tablename__ = "flag_case_users"
    __table_args__ = (
        PrimaryKeyConstraint("case_id", "user_id", name="pk_flag_case_users"),
        Index("ix_flag_case_users_user_id", "user_id"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(_cases_fk())
    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())


class FlagCaseDeed(Base):
    """A deed of a case. Insert-only (``ON CONFLICT DO NOTHING`` on join)."""

    __tablename__ = "flag_case_deeds"
    __table_args__ = (
        PrimaryKeyConstraint("case_id", "deed_id", name="pk_flag_case_deeds"),
        Index("ix_flag_case_deeds_deed_id", "deed_id"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(_cases_fk())
    deed_id: Mapped[uuid.UUID] = mapped_column(_deeds_fk())
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PrizeHold(Base):
    """One hold per (deed, case), placed only by a confirmed decision (D79, D80). A deed is held
    while any of its holds is unreleased; a release is set once and never undone."""

    __tablename__ = "prize_holds"
    __table_args__ = (
        UniqueConstraint("deed_id", "case_id", name="uq_prize_holds_deed_case"),
        CheckConstraint(
            "(released_at IS NULL) = (release_reason IS NULL)", name="released_has_reason"
        ),
        CheckConstraint(
            "released_at IS NULL OR released_at >= held_at", name="released_after_held"
        ),
        # The hot ``is_held`` / ``held_deed_ids`` read.
        Index(
            "ix_prize_holds_active_deed_id",
            "deed_id",
            postgresql_where=text("released_at IS NULL"),
        ),
        # Releasing a case's holds (dismissal, appeal).
        Index("ix_prize_holds_case_id", "case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    deed_id: Mapped[uuid.UUID] = mapped_column(_deeds_fk())
    case_id: Mapped[uuid.UUID] = mapped_column(_cases_fk())
    held_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[HoldReleaseReason | None] = mapped_column(
        _enum(HoldReleaseReason, "hold_release_reason")
    )


class FlagCaseDecision(Base):
    """One admin action on a case, with its required note (D88). Append-only."""

    __tablename__ = "flag_case_decisions"
    __table_args__ = (
        CheckConstraint(_NOTE_MIN, name="note_required"),
        Index("ix_flag_case_decisions_case_created_at", "case_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(_cases_fk())
    action: Mapped[FlagCaseAction] = mapped_column(_enum(FlagCaseAction, "flag_case_action"))
    actor_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FlagCaseDeedOutcome(Base):
    """What a confirm decided for one deed of the case (D88). Append-only; one per (case, deed),
    and only for a deed of that case (composite foreign key to ``flag_case_deeds``)."""

    __tablename__ = "flag_case_deed_outcomes"
    __table_args__ = (
        PrimaryKeyConstraint("case_id", "deed_id", name="pk_flag_case_deed_outcomes"),
        ForeignKeyConstraint(
            ["case_id", "deed_id"],
            ["flag_case_deeds.case_id", "flag_case_deeds.deed_id"],
            name="fk_flag_case_deed_outcomes_case_deed",
            ondelete="RESTRICT",
        ),
        Index("ix_flag_case_deed_outcomes_decision_id", "decision_id"),
    )

    case_id: Mapped[uuid.UUID]
    deed_id: Mapped[uuid.UUID]
    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("flag_case_decisions.id", ondelete="RESTRICT")
    )
    outcome: Mapped[DeedOutcome] = mapped_column(_enum(DeedOutcome, "deed_outcome"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExplanationRequest(Base):
    """An admin's request that a subject explain their deeds before an adverse decision (D99).
    One per (case, user), only for a subject of the case. Only the reply may be set, once, and
    not after ``reply_due_at``."""

    __tablename__ = "explanation_requests"
    __table_args__ = (
        UniqueConstraint("case_id", "user_id", name="uq_explanation_requests_case_user"),
        ForeignKeyConstraint(
            ["case_id", "user_id"],
            ["flag_case_users.case_id", "flag_case_users.user_id"],
            name="fk_explanation_requests_case_user",
            ondelete="RESTRICT",
        ),
        CheckConstraint("(reply IS NULL) = (replied_at IS NULL)", name="reply_pair"),
        CheckConstraint("reply IS NULL OR length(reply) <= 2000", name="reply_length"),
        CheckConstraint("reply_due_at > requested_at", name="due_after_request"),
        CheckConstraint(
            "replied_at IS NULL OR (replied_at >= requested_at AND replied_at <= reply_due_at)",
            name="replied_in_time",
        ),
        CheckConstraint("requested_by <> user_id", name="not_self"),
        # A member's Reviews page.
        Index("ix_explanation_requests_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    requested_by: Mapped[uuid.UUID] = mapped_column(_users_fk())
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reply_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reply: Mapped[str | None] = mapped_column(Text)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExplanationReminder(Base):
    """The one reminder sent for an explanation request (M6A D112, migration 0033): written by
    ``fraud.remind_explanations`` with the ``MemberReviewUpdate`` it emits, so the reminder fires
    once. Insert-only (``forbid_modification()``)."""

    __tablename__ = "explanation_reminders"

    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("explanation_requests.id", ondelete="RESTRICT"), primary_key=True
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DecisionNotice(Base):
    """What a person is told about an adverse decision on their own deeds (D100): one per person
    per confirmed case, or per lowering override. ``items`` holds ids and numbers only. Only
    ``final_at`` may be set, once (by the appeal decision or once ``appeal_until`` passes)."""

    __tablename__ = "decision_notices"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'case' AND case_id IS NOT NULL AND override_id IS NULL) "
            "OR (kind = 'override' AND override_id IS NOT NULL AND case_id IS NULL)",
            name="kind_matches_source",
        ),
        CheckConstraint("jsonb_typeof(items) = 'array'", name="items_array"),
        CheckConstraint("appeal_until > created_at", name="appeal_until_after_created"),
        CheckConstraint("final_at IS NULL OR final_at >= created_at", name="final_after_created"),
        UniqueConstraint("case_id", "user_id", name="uq_decision_notices_case_user"),
        UniqueConstraint("override_id", "user_id", name="uq_decision_notices_override_user"),
        # Target of the appeals (notice, user) foreign key.
        UniqueConstraint("id", "user_id", name="uq_decision_notices_id_user"),
        # A case notice is only for a subject of the case.
        ForeignKeyConstraint(
            ["case_id", "user_id"],
            ["flag_case_users.case_id", "flag_case_users.user_id"],
            name="fk_decision_notices_case_user",
            ondelete="RESTRICT",
        ),
        # ``fraud.finalize_decisions``: notices past ``appeal_until`` that are not final yet.
        Index(
            "ix_decision_notices_not_final",
            "appeal_until",
            postgresql_where=text("final_at IS NULL"),
        ),
        # A member's Reviews page.
        Index("ix_decision_notices_user_created_at", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    kind: Mapped[NoticeKind] = mapped_column(_enum(NoticeKind, "notice_kind"))
    case_id: Mapped[uuid.UUID | None] = mapped_column(_cases_fk())
    # No foreign key: ``scoring`` owns score overrides (D78).
    override_id: Mapped[uuid.UUID | None]
    # [{deed_id, effect, old_points?, new_points?}]: ids and numbers only.
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # A key of ``fraud.member_reason_labels``.
    reason_key: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    appeal_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    final_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Appeal(Base):
    """The one appeal a person may make against a notice (D100). Decided once, by an admin who
    is not the decider and not conflicted (the service checks that)."""

    __tablename__ = "appeals"
    __table_args__ = (
        UniqueConstraint("notice_id", name="uq_appeals_notice_id"),
        ForeignKeyConstraint(
            ["notice_id", "user_id"],
            ["decision_notices.id", "decision_notices.user_id"],
            name="fk_appeals_notice_user",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(btrim(reason)) >= 10 AND length(reason) <= 2000", name="reason_length"
        ),
        CheckConstraint(
            "(outcome IS NULL) = (reviewer_id IS NULL) "
            "AND (outcome IS NULL) = (decided_at IS NULL) "
            "AND (outcome IS NULL) = (reviewer_note IS NULL)",
            name="decided_fields",
        ),
        CheckConstraint(
            "reviewer_note IS NULL OR length(btrim(reviewer_note)) >= 5",
            name="reviewer_note_required",
        ),
        CheckConstraint("reviewer_id IS NULL OR reviewer_id <> user_id", name="not_self"),
        CheckConstraint("decided_at IS NULL OR decided_at >= created_at", name="decided_after"),
        # The open appeals queue, oldest first.
        Index("ix_appeals_open_created_at", "created_at", postgresql_where=text("outcome IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    notice_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(_users_fk())
    outcome: Mapped[AppealOutcome | None] = mapped_column(_enum(AppealOutcome, "appeal_outcome"))
    reviewer_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FlagRejectionSent(Base):
    """A ``reject`` outcome already sent to ``deeds`` in a ``FlagDecisionFinal`` event (SEC5-L6,
    migration 0029): ``finalize_decisions`` reads only outcomes without one, so it never re-sends
    a deed and doesn't depend on outbox rows being kept. Insert-only."""

    __tablename__ = "flag_rejections_sent"
    __table_args__ = (
        PrimaryKeyConstraint("case_id", "deed_id", name="pk_flag_rejections_sent"),
        ForeignKeyConstraint(
            ["case_id", "deed_id"],
            ["flag_case_deed_outcomes.case_id", "flag_case_deed_outcomes.deed_id"],
            name="fk_flag_rejections_sent_outcome",
            ondelete="RESTRICT",
        ),
    )

    case_id: Mapped[uuid.UUID]
    deed_id: Mapped[uuid.UUID]
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FraudDetectorRun(Base):
    """One sweep or CLI run of a detector (D82), or one truncated per-deed check (trigger
    ``event``, SEC5-M6); other per-deed checks are not logged here.
    ``detector`` is a plain name, not an enum: detectors are plug-ins (D102), so adding one needs
    no migration."""

    __tablename__ = "fraud_detector_runs"
    __table_args__ = (
        CheckConstraint("detector ~ '^[a-z][a-z0-9_]{0,39}$'", name="detector_name"),
        CheckConstraint("window_end > window_start", name="window_valid"),
        CheckConstraint("config_version >= 1", name="config_version_positive"),
        CheckConstraint("(status = 'running') = (finished_at IS NULL)", name="finished_fields"),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="finished_after"),
        CheckConstraint(
            "edges_scanned >= 0 AND cases_opened >= 0 AND deeds_added >= 0",
            name="counts_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    detector: Mapped[str] = mapped_column(String(40))
    trigger: Mapped[DetectorRunTrigger] = mapped_column(
        _enum(DetectorRunTrigger, "detector_run_trigger")
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The ``fraud`` config version used.
    config_version: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[DetectorRunStatus] = mapped_column(
        _enum(DetectorRunStatus, "detector_run_status"),
        server_default=DetectorRunStatus.RUNNING.value,
    )
    edges_scanned: Mapped[int] = mapped_column(Integer, server_default="0")
    cases_opened: Mapped[int] = mapped_column(Integer, server_default="0")
    deeds_added: Mapped[int] = mapped_column(Integer, server_default="0")


class ReviewView(Base):
    """When a member last opened *Profile -> Reviews* (``POST /api/reviews/seen``). Explanation
    requests and notices created after ``seen_at`` count as unread (the Profile tab dot). One row
    per user, upserted; not append-only."""

    __tablename__ = "review_views"

    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk(), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# The admin status page: the last run of each detector.
Index(
    "ix_fraud_detector_runs_detector_started_at",
    FraudDetectorRun.detector,
    FraudDetectorRun.started_at.desc(),
)
