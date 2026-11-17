"""Deeds, QR scans, same-act groups, invites, the verification ledger and self-logs.

docs/M2-DESIGN.md §2.2 to §2.7. Invariants live in the database (check constraints, unique
constraints, partial unique indexes); services map ``IntegrityError`` to error codes by
constraint name, so names here are part of the contract.

``verification_events`` and ``deed_revisions`` are append-only: migration 0005 attaches the
``forbid_modification()`` trigger to both.

Precise locations are stored as ``geography(Point, 4326)`` and are never returned by the API in
M2. Each location column has an explicit GiST index (``spatial_index=False`` on the type, so the
index is a normal metadata ``Index`` that Alembic can compare).
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _point() -> Geography:
    return Geography(geometry_type="POINT", srid=4326, spatial_index=False)


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


def _users_fk(ondelete: str = "RESTRICT") -> ForeignKey:
    return ForeignKey("users.id", ondelete=ondelete)


class ScanMethod(StrEnum):
    QR = "qr"
    MANUAL_CODE = "manual_code"


class DeedStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    REJECTED = "rejected"
    REMOVED = "removed"
    WITHDRAWN = "withdrawn"


class VerificationMethod(StrEnum):
    QR_SCAN = "qr_scan"
    INVITE_ACCEPT = "invite_accept"


class InviteSentVia(StrEnum):
    WHATSAPP = "whatsapp"
    SHARE_SHEET = "share_sheet"
    COPY_LINK = "copy_link"
    OTHER = "other"


class InviteStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class VerificationEventKind(StrEnum):
    QR_VERIFIED = "qr_verified"
    INVITE_ACCEPTED = "invite_accepted"
    INVITE_EXPIRED = "invite_expired"
    WITHDRAWN = "withdrawn"
    REJECTED = "rejected"


class QrScan(Base):
    """One row per accepted scan (QR or ID + manual code). §2.2."""

    __tablename__ = "qr_scans"
    __table_args__ = (
        # ROADMAP single-use: one scan per (doer, token window, witness).
        UniqueConstraint(
            "doer_user_id",
            "token_window",
            "witness_user_id",
            name="uq_qr_scans_doer_window_witness",
        ),
        # ROADMAP (doer, witness, IST day). An abandoned scan also uses up the pair-day.
        UniqueConstraint(
            "doer_user_id", "witness_user_id", "scan_day", name="uq_qr_scans_doer_witness_day"
        ),
        CheckConstraint("doer_user_id <> witness_user_id", name="not_self"),
        CheckConstraint("accuracy_m > 0", name="accuracy_positive"),
        # The purge job looks for unconsumed scans past their expiry.
        Index(
            "ix_qr_scans_unconsumed_expires_at",
            "expires_at",
            postgresql_where=text("consumed_at IS NULL"),
        ),
        Index("ix_qr_scans_location", "location", postgresql_using="gist"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    witness_user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    doer_user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    scan_method: Mapped[ScanMethod] = mapped_column(_enum(ScanMethod, "scan_method"))
    token_window: Mapped[int] = mapped_column(BigInteger)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # IST date of scanned_at, set by the app (the conversion is not immutable).
    scan_day: Mapped[date] = mapped_column(Date)
    location: Mapped[WKBElement] = mapped_column(_point())
    accuracy_m: Mapped[int] = mapped_column(Integer)
    device_hash: Mapped[str | None] = mapped_column(String(64))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    limits_version: Mapped[int] = mapped_column(Integer)


# Travel-speed checks read the latest scans of a witness and of a doer.
Index("ix_qr_scans_witness_scanned_at", QrScan.witness_user_id, QrScan.scanned_at.desc())
Index("ix_qr_scans_doer_scanned_at", QrScan.doer_user_id, QrScan.scanned_at.desc())


class SameActGroup(Base):
    """Reports of one act: within ``radius_m`` and ``window_minutes`` of the anchor. §2.3.

    The parameters are copied from config at creation, so a later config change never reshuffles
    existing groups. ``centre`` is null for a group anchored by a deed with no location; such a
    group never matches anything.
    """

    __tablename__ = "same_act_groups"
    __table_args__ = (
        CheckConstraint(
            "counted_count <= cap AND counted_count <= verified_count", name="counted_within_cap"
        ),
        CheckConstraint(
            "verified_count >= 0 AND counted_count >= 0 AND distinct_reporters >= 0 "
            "AND distinct_reporters <= verified_count",
            name="counters_valid",
        ),
        CheckConstraint(
            "radius_m > 0 AND window_minutes > 0 AND cap > 0", name="parameters_positive"
        ),
        Index("ix_same_act_groups_centre", "centre", postgresql_using="gist"),
        Index("ix_same_act_groups_doer_anchor_at", "doer_user_id", "anchor_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    doer_user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    centre: Mapped[WKBElement | None] = mapped_column(_point())
    # The anchor report's server grouping time (scan time / invite reported_at), never its
    # occurred_at (L7-A).
    anchor_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    radius_m: Mapped[int] = mapped_column(Integer)
    window_minutes: Mapped[int] = mapped_column(Integer)
    cap: Mapped[int] = mapped_column(Integer)
    # PRD `report_count`.
    verified_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    counted_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    distinct_reporters: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    limits_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Deed(Base):
    """A deed reported by a witness about a doer. §2.4.

    ``counts_for_points`` is mutable: it is set at grouping time and can flip false -> true when
    a counted report in the same group is withdrawn and this one is promoted (D13).
    """

    __tablename__ = "deeds"
    __table_args__ = (
        # PRD: never verify your own deed.
        CheckConstraint(
            "doer_user_id IS NULL OR doer_user_id <> reporter_user_id", name="not_self"
        ),
        CheckConstraint(
            "status <> 'verified' OR (doer_user_id IS NOT NULL AND verified_at IS NOT NULL "
            "AND same_act_group_id IS NOT NULL)",
            name="verified_has_doer",
        ),
        CheckConstraint(
            "status <> 'pending' OR (verification_method = 'invite_accept' "
            "AND doer_user_id IS NULL)",
            name="pending_is_invite",
        ),
        CheckConstraint(
            "verification_method <> 'qr_scan' OR scan_id IS NOT NULL", name="qr_has_scan"
        ),
        CheckConstraint(
            "NOT counts_for_points OR same_act_group_id IS NOT NULL", name="counts_needs_group"
        ),
        CheckConstraint(
            "verification_method <> 'qr_scan' OR location IS NOT NULL", name="qr_has_location"
        ),
        CheckConstraint("(status = 'withdrawn') = (withdrawn_at IS NOT NULL)", name="withdrawn_at"),
        CheckConstraint(
            "beneficiaries_count IS NULL OR beneficiaries_count BETWEEN 1 AND 10000",
            name="beneficiaries_range",
        ),
        CheckConstraint(
            "location_accuracy_m IS NULL OR location_accuracy_m > 0",
            name="location_accuracy_positive",
        ),
        # (doer, reporter, IST day) for both paths; the QR path is also covered on qr_scans.
        Index(
            "uq_deeds_pair_day",
            "doer_user_id",
            "reporter_user_id",
            "report_day",
            unique=True,
            postgresql_where=text("doer_user_id IS NOT NULL"),
        ),
        Index("ix_deeds_location", "location", postgresql_using="gist"),
        Index("ix_deeds_doer_occurred_at", "doer_user_id", "occurred_at"),
        Index("ix_deeds_same_act_group_id", "same_act_group_id"),
        # Profile "Verified Deeds" count (D4, D15: counted reports only).
        Index(
            "ix_deeds_doer_counted",
            "doer_user_id",
            postgresql_where=text("status = 'verified' AND counts_for_points"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reporter_user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    # Null while an invite is pending.
    doer_user_id: Mapped[uuid.UUID | None] = mapped_column(_users_fk())
    category: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(1000))
    beneficiaries_count: Mapped[int | None] = mapped_column(Integer)
    # Precise; never returned by the API in M2. Always set for QR deeds.
    location: Mapped[WKBElement | None] = mapped_column(_point())
    location_accuracy_m: Mapped[int | None] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # IST date of reported_at, set by the app.
    report_day: Mapped[date] = mapped_column(Date)
    status: Mapped[DeedStatus] = mapped_column(_enum(DeedStatus, "deed_status"))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_method: Mapped[VerificationMethod] = mapped_column(
        _enum(VerificationMethod, "verification_method")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("qr_scans.id", ondelete="RESTRICT"), unique=True
    )
    same_act_group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("same_act_groups.id", ondelete="RESTRICT")
    )
    counts_for_points: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    prize_rank_eligible: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    limits_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# A reporter's own deeds, newest first; also the monthly reporting limit's count (D16).
Index("ix_deeds_reporter_reported_at", Deed.reporter_user_id, Deed.reported_at.desc())


class Invite(Base):
    """A single-use invite link for a pending deed. §2.5. Only the token's hash is stored."""

    __tablename__ = "invites"
    __table_args__ = (
        CheckConstraint(
            "(status = 'accepted') = (accepted_by IS NOT NULL AND accepted_at IS NOT NULL)",
            name="accepted_together",
        ),
        CheckConstraint("accepted_by IS NULL OR accepted_by <> reporter_user_id", name="not_self"),
        # Daily invite count per reporter (IST day).
        Index("ix_invites_reporter_created_day", "reporter_user_id", "created_day"),
        # The expiry job.
        Index(
            "ix_invites_pending_expires_at",
            "expires_at",
            postgresql_where=text("status = 'pending'"),
        ),
        # Shared-device accepts per reporter-doer pair per month (D6).
        Index(
            "ix_invites_shared_device_pair",
            "reporter_user_id",
            "accepted_by",
            "accepted_at",
            postgresql_where=text("shared_device"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    deed_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("deeds.id", ondelete="RESTRICT"), unique=True
    )
    reporter_user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    # sha256 hex of the raw token; the raw token is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    sent_via: Mapped[InviteSentVia] = mapped_column(_enum(InviteSentVia, "invite_sent_via"))
    status: Mapped[InviteStatus] = mapped_column(_enum(InviteStatus, "invite_status"))
    shared_device: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # IST day, used for the daily count.
    created_day: Mapped[date] = mapped_column(Date)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reissued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(_users_fk())
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # PRD `accepted_phone`, as a keyed hash (DPDP).
    accepted_phone_hash: Mapped[str | None] = mapped_column(String(64))


class VerificationEvent(Base):
    """Append-only ledger of deed status transitions (PRD ``VerificationEvent``). §2.6."""

    __tablename__ = "verification_events"
    __table_args__ = (Index("ix_verification_events_deed_id", "deed_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    deed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deeds.id", ondelete="RESTRICT"))
    kind: Mapped[VerificationEventKind] = mapped_column(
        _enum(VerificationEventKind, "verification_event_kind", length=30)
    )
    # Witness, accepting doer, or null for the system. No FK, like audit_log.actor_id.
    actor_user_id: Mapped[uuid.UUID | None]
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("qr_scans.id", ondelete="RESTRICT")
    )
    # Plausibility figures only; never raw coordinates.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeedRevision(Base):
    """Append-only history of reporter edits (D7). §2.6b."""

    __tablename__ = "deed_revisions"
    __table_args__ = (Index("ix_deed_revisions_deed_id_created_at", "deed_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    deed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deeds.id", ondelete="RESTRICT"))
    editor_user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    # Only the changed fields: category, description, beneficiaries_count.
    old_values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    new_values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SelfLog(Base):
    """A private self-logged deed (D3). No link to deeds, scans or groups. §2.7."""

    __tablename__ = "self_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # CASCADE: self-logs are private and are deleted with the account (D8).
    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk("CASCADE"))
    category: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(1000))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    location: Mapped[WKBElement | None] = mapped_column(_point())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


Index("ix_self_logs_user_created_at", SelfLog.user_id, SelfLog.created_at.desc())
