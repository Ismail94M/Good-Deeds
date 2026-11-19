"""Photos, photo reports and admin photo decisions (docs/M3-DESIGN.md §2.2, migration 0014).

Invariants live in the database (check constraints, partial unique indexes); services map
``IntegrityError`` to error codes by constraint name, so names here are part of the contract.

``photo_decisions`` is append-only: migration 0014 attaches the ``forbid_modification()``
trigger. A ``photos`` row is never deleted; it stays as the upload record after its objects are
purged (``status = 'deleted'``).
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
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
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


class PhotoPurpose(StrEnum):
    DEED = "deed"
    SELF_LOG = "self_log"


class PhotoStatus(StrEnum):
    PENDING_UPLOAD = "pending_upload"
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"
    DELETED = "deleted"


class PhotoTakedown(StrEnum):
    NONE = "none"
    # Reversible by an admin ``restore``.
    ADMIN_REMOVED = "admin_removed"
    # Final (Q29): nobody, not even an admin, can restore it; objects are deleted at once.
    DOER_REMOVED = "doer_removed"


class PhotoType(StrEnum):
    JPEG = "image/jpeg"
    PNG = "image/png"
    WEBP = "image/webp"


class PhotoRejectReason(StrEnum):
    UNSUPPORTED_TYPE = "unsupported_type"
    TOO_LARGE = "too_large"
    TOO_MANY_PIXELS = "too_many_pixels"
    ANIMATED = "animated"
    CORRUPT = "corrupt"
    SIZE_MISMATCH = "size_mismatch"
    # The same file as a photo the doer removed from this deed (Q29).
    REMOVED_BY_DOER = "removed_by_doer"


class ObjectMove(StrEnum):
    # Admin removal: move variants to ``held/``.
    HOLD = "hold"
    # Restore: copy back under ``photos/`` with a new version.
    RELEASE = "release"


class ReportCaseStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    # The photo was deleted by its uploader or doer while the case was open.
    CLOSED = "closed"


class CaseDecision(StrEnum):
    KEPT = "kept"
    REMOVED = "removed"


class PhotoReportReason(StrEnum):
    NOT_CONSENTED = "not_consented"
    CHILD_NO_GUARDIAN_CONSENT = "child_no_guardian_consent"
    PRIVATE_DETAILS = "private_details"
    INAPPROPRIATE = "inappropriate"
    OTHER = "other"


# Queue priority per reason (Q44): 1 = child, 2 = privacy and harm (equal), 3 = other.
# A case's priority is the minimum over its reports.
PHOTO_REPORT_PRIORITY: dict[PhotoReportReason, int] = {
    PhotoReportReason.CHILD_NO_GUARDIAN_CONSENT: 1,
    PhotoReportReason.NOT_CONSENTED: 2,
    PhotoReportReason.PRIVATE_DETAILS: 2,
    PhotoReportReason.INAPPROPRIATE: 2,
    PhotoReportReason.OTHER: 3,
}


class PhotoDecisionAction(StrEnum):
    KEEP = "keep"
    REMOVE = "remove"
    RESTORE = "restore"


class Photo(Base):
    """One row per upload, kept after deletion as the upload record. §2.2."""

    __tablename__ = "photos"
    __table_args__ = (
        CheckConstraint("NOT (deed_id IS NOT NULL AND self_log_id IS NOT NULL)", name="one_owner"),
        CheckConstraint(
            "(deed_id IS NULL OR purpose = 'deed') "
            "AND (self_log_id IS NULL OR purpose = 'self_log')",
            name="owner_matches_purpose",
        ),
        CheckConstraint("takedown = 'none' OR purpose = 'deed'", name="takedown_deed_only"),
        CheckConstraint(
            "status <> 'ready' OR (variant_keys IS NOT NULL AND width IS NOT NULL "
            "AND height IS NOT NULL)",
            name="ready_has_variants",
        ),
        CheckConstraint("declared_bytes > 0", name="declared_bytes_positive"),
        CheckConstraint("position IS NULL OR position >= 1", name="position_positive"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint(
            "(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)",
            name="dimensions_positive",
        ),
        CheckConstraint("(status = 'deleted') = (deleted_at IS NOT NULL)", name="deleted_at"),
        CheckConstraint(
            "takedown <> 'admin_removed' OR removed_at IS NOT NULL", name="removed_has_time"
        ),
        CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="content_sha256_hex",
        ),
        CheckConstraint("jsonb_typeof(blur_regions) = 'array'", name="blur_regions_array"),
        # Carousel slots: one live photo per position per owner.
        Index(
            "uq_photos_deed_position",
            "deed_id",
            "position",
            unique=True,
            postgresql_where=text(
                "status IN ('queued', 'processing', 'ready') AND takedown = 'none'"
            ),
        ),
        Index(
            "uq_photos_self_log_position",
            "self_log_id",
            "position",
            unique=True,
            postgresql_where=text(
                "status IN ('queued', 'processing', 'ready') AND takedown = 'none'"
            ),
        ),
        # Photos for a page of deeds / a self-log.
        Index(
            "ix_photos_deed_position",
            "deed_id",
            "position",
            postgresql_where=text("deed_id IS NOT NULL"),
        ),
        Index(
            "ix_photos_self_log_id",
            "self_log_id",
            postgresql_where=text("self_log_id IS NOT NULL"),
        ),
        # Expiry sweep of uploads never attached.
        Index(
            "ix_photos_unattached_status_created_at",
            "status",
            "created_at",
            postgresql_where=text("status IN ('pending_upload', 'uploaded')"),
        ),
        # Worker queue and stuck-job detection.
        Index(
            "ix_photos_work_status_started_at",
            "status",
            "processing_started_at",
            postgresql_where=text("status IN ('queued', 'processing')"),
        ),
        # Purge retries.
        Index(
            "ix_photos_unpurged_deleted_at",
            "deleted_at",
            postgresql_where=text("objects_purged_at IS NULL AND status = 'deleted'"),
        ),
        # Object moves left to retry after an admin removal or restore.
        Index(
            "ix_photos_object_move",
            "object_move",
            postgresql_where=text("object_move IS NOT NULL"),
        ),
    )

    # Also the object-key stem; random, never derived from anything guessable.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Who uploaded (recorded instead of a consent tick).
    uploader_user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    purpose: Mapped[PhotoPurpose] = mapped_column(_enum(PhotoPurpose, "photo_purpose"))
    deed_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deeds.id", ondelete="RESTRICT"))
    # Deleting a self-log detaches its photos; the cleanup job deletes their objects.
    self_log_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("self_logs.id", ondelete="SET NULL")
    )
    position: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[PhotoStatus] = mapped_column(_enum(PhotoStatus, "photo_status"))
    takedown: Mapped[PhotoTakedown] = mapped_column(
        _enum(PhotoTakedown, "photo_takedown"),
        default=PhotoTakedown.NONE,
        server_default=PhotoTakedown.NONE.value,
    )
    declared_type: Mapped[PhotoType] = mapped_column(
        _enum(PhotoType, "photo_declared_type", length=40)
    )
    declared_bytes: Mapped[int] = mapped_column(Integer)
    # ``staging/{id}``: the raw upload, deleted after processing.
    staging_key: Mapped[str] = mapped_column(String(200))
    staging_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # SHA-256 (hex) of the raw upload bytes, set by the worker. Kept after deletion so a photo
    # the doer removed can't be re-uploaded byte-for-byte to the same deed (Q29).
    content_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    # {"thumb": "photos/{id}/thumb-{v}.webp", "display": "photos/{id}/display-{v}.webp"}
    # none_as_null: Python None is stored as SQL NULL, not JSON 'null' (ready_has_variants).
    variant_keys: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    # Of the ``display`` variant.
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # Normalised ellipses applied by the worker (numbers only).
    blur_regions: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    reject_reason: Mapped[PhotoRejectReason | None] = mapped_column(
        _enum(PhotoRejectReason, "photo_reject_reason", length=40)
    )
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    # keyed_hash("ip", ip)
    upload_ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Uploader, doer (Q29) or null = system. No FK, like audit_log.actor_id.
    deleted_by: Mapped[uuid.UUID | None]
    # Derivatives deleted from storage.
    objects_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Pending storage move after an admin removal or restore; null when done.
    object_move: Mapped[ObjectMove | None] = mapped_column(
        _enum(ObjectMove, "photo_object_move", length=10)
    )
    # Last admin removal; null when restored.
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Pending-upload count and upload history per user.
Index("ix_photos_uploader_created_at", Photo.uploader_user_id, Photo.created_at.desc())


class PhotoReportCase(Base):
    """At most one open case per photo. Reports never change the photo (D42)."""

    __tablename__ = "photo_report_cases"
    __table_args__ = (
        Index(
            "uq_photo_report_cases_open",
            "photo_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        # Only a resolved case has a decision and a decider, and it has a time. (A closed case
        # may record when it was closed in resolved_at.)
        CheckConstraint(
            "(status = 'resolved') = (decision IS NOT NULL) "
            "AND (decision IS NULL) = (decided_by IS NULL) "
            "AND (status <> 'resolved' OR resolved_at IS NOT NULL)",
            name="resolved_has_decision",
        ),
        CheckConstraint(
            "(decision IS NOT DISTINCT FROM 'removed') = (removal_reason IS NOT NULL)",
            name="removal_reason_iff_removed",
        ),
        CheckConstraint("priority BETWEEN 1 AND 3", name="priority_range"),
        CheckConstraint("report_count >= 0", name="report_count_non_negative"),
        # Admin queue order.
        Index("ix_photo_report_cases_queue", "status", "priority", "opened_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    photo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("photos.id", ondelete="RESTRICT"))
    status: Mapped[ReportCaseStatus] = mapped_column(
        _enum(ReportCaseStatus, "photo_report_case_status")
    )
    # Minimum of PHOTO_REPORT_PRIORITY over the case's reports.
    priority: Mapped[int] = mapped_column(SmallInteger)
    report_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[CaseDecision | None] = mapped_column(
        _enum(CaseDecision, "photo_report_case_decision")
    )
    # Shown to the uploader; the admin's choice, defaulting to the top-priority report reason.
    removal_reason: Mapped[PhotoReportReason | None] = mapped_column(
        _enum(PhotoReportReason, "photo_report_case_removal_reason", length=30)
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(_users_fk())


# Latest outcome per photo (uploader's outcome banner).
Index(
    "ix_photo_report_cases_photo_resolved_at",
    PhotoReportCase.photo_id,
    PhotoReportCase.resolved_at.desc(),
)


class PhotoReport(Base):
    """One report by a member or a visitor. A duplicate gets the same 202 and creates nothing."""

    __tablename__ = "photo_reports"
    __table_args__ = (
        Index(
            "uq_photo_reports_case_member",
            "case_id",
            "reporter_user_id",
            unique=True,
            postgresql_where=text("reporter_user_id IS NOT NULL"),
        ),
        Index(
            "uq_photo_reports_case_visitor",
            "case_id",
            "reporter_ip_hash",
            unique=True,
            postgresql_where=text("reporter_user_id IS NULL"),
        ),
        Index("ix_photo_reports_case_created_at", "case_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("photo_report_cases.id", ondelete="RESTRICT")
    )
    photo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("photos.id", ondelete="RESTRICT"))
    # Null = a visitor.
    reporter_user_id: Mapped[uuid.UUID | None] = mapped_column(_users_fk())
    # keyed hash, for visitors and members
    reporter_ip_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[PhotoReportReason] = mapped_column(
        _enum(PhotoReportReason, "photo_report_reason", length=30)
    )
    # Free text, admins only; the product limit is config (report_details_max).
    details: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PhotoDecision(Base):
    """Append-only record of admin keep / remove / restore decisions (D42). Every row is also
    written to ``audit_log``."""

    __tablename__ = "photo_decisions"
    __table_args__ = (
        CheckConstraint("length(btrim(note)) >= 5", name="note_min_length"),
        CheckConstraint(
            "(action = 'remove') = (removal_reason IS NOT NULL)", name="removal_reason_iff_remove"
        ),
        Index("ix_photo_decisions_photo_created_at", "photo_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    photo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("photos.id", ondelete="RESTRICT"))
    # Null for a restore made outside a case.
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("photo_report_cases.id", ondelete="RESTRICT")
    )
    action: Mapped[PhotoDecisionAction] = mapped_column(
        _enum(PhotoDecisionAction, "photo_decision_action")
    )
    removal_reason: Mapped[PhotoReportReason | None] = mapped_column(
        _enum(PhotoReportReason, "photo_decision_removal_reason", length=30)
    )
    note: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
