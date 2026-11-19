"""Feed projection, social tables, comment reports and Top snapshots (docs/M3-DESIGN.md §2.3,
migration 0015).

``feed_posts`` holds only what ordering, filtering and counting need (D38); deed content is read
live from ``deeds.service``. Visibility (the two hide columns and the generated ``listed``) is
stored here, apart from deed status, counts and prize fields, and scoring never reads it (D36).

Invariants live in the database; services map ``IntegrityError`` to error codes by constraint
name, so names here are part of the contract. ``comment_decisions`` is append-only: migration
0015 attaches the ``forbid_modification()`` trigger.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
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


def _posts_fk() -> ForeignKey:
    return ForeignKey("feed_posts.deed_id", ondelete="RESTRICT")


class PostState(StrEnum):
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"
    REJECTED = "rejected"
    # M7 moderation.
    REMOVED = "removed"
    # M5 flags.
    HELD = "held"


class CommentDeletedRole(StrEnum):
    AUTHOR = "author"
    DOER = "doer"
    REPORTER = "reporter"
    # A moderator or admin decision on a report (Q38).
    MODERATION = "moderation"


class ShareTarget(StrEnum):
    WHATSAPP = "whatsapp"
    COPY_LINK = "copy_link"
    WEB_SHARE = "web_share"


class TopPeriod(StrEnum):
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class ReportCaseStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    # The comment was deleted by its author, the doer or the reporter while the case was open.
    CLOSED = "closed"


class CaseDecision(StrEnum):
    KEPT = "kept"
    REMOVED = "removed"


class CommentReportReason(StrEnum):
    THREAT_OR_HARASSMENT = "threat_or_harassment"
    HATE = "hate"
    PERSONAL_INFO = "personal_info"
    SPAM = "spam"
    OTHER = "other"


# Queue priority per reason (Q38); a case's priority is the minimum over its reports.
COMMENT_REPORT_PRIORITY: dict[CommentReportReason, int] = {
    CommentReportReason.THREAT_OR_HARASSMENT: 1,
    CommentReportReason.PERSONAL_INFO: 1,
    CommentReportReason.HATE: 2,
    CommentReportReason.SPAM: 3,
    CommentReportReason.OTHER: 3,
}


class CommentDecisionAction(StrEnum):
    KEEP = "keep"
    REMOVE = "remove"
    RESTORE = "restore"


LISTED_SQL = "state = 'published' AND hidden_by_doer_at IS NULL AND hidden_by_reporter_at IS NULL"


class FeedPost(Base):
    """The projection: one row per deed that has ever been verified (D37). Synced from the
    deed's current status by ``sync_post``; terminal states never go back to ``published``."""

    __tablename__ = "feed_posts"
    __table_args__ = (
        CheckConstraint("like_count >= 0", name="like_count_non_negative"),
        CheckConstraint(
            "top_like_count >= 0 AND top_like_count <= like_count", name="top_like_count_valid"
        ),
        CheckConstraint("comment_count >= 0", name="comment_count_non_negative"),
        CheckConstraint("doer_id <> reporter_id", name="not_self"),
    )

    deed_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("deeds.id", ondelete="RESTRICT"), primary_key=True
    )
    doer_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    reporter_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    # The deed's verified_at; feed order.
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[PostState] = mapped_column(_enum(PostState, "post_state"))
    # Two separate hides, each undone only by the person who set it (Q24 = A).
    hidden_by_doer_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_by_reporter_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    listed: Mapped[bool] = mapped_column(Boolean, Computed(LISTED_SQL, persisted=True))
    # Every like, including the doer's and reporter's (shown everywhere).
    like_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Likes from anyone except the deed's doer and reporter; only the Top score reads it (Q43).
    top_like_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Live (not deleted) comments.
    comment_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Last sync_post.
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# Keyset order (published_at DESC, deed_id DESC), listed posts only.
# Nearby and the Top snapshot input.
Index(
    "ix_feed_posts_listed",
    FeedPost.published_at.desc(),
    FeedPost.deed_id.desc(),
    postgresql_where=text("listed"),
)
# Following (doers) and profile deeds.
Index(
    "ix_feed_posts_doer_listed",
    FeedPost.doer_id,
    FeedPost.published_at.desc(),
    FeedPost.deed_id.desc(),
    postgresql_where=text("listed"),
)
# Following (reporters, Q25 = A).
Index(
    "ix_feed_posts_reporter_listed",
    FeedPost.reporter_id,
    FeedPost.published_at.desc(),
    FeedPost.deed_id.desc(),
    postgresql_where=text("listed"),
)


class FeedLike(Base):
    __tablename__ = "feed_likes"
    __table_args__ = (PrimaryKeyConstraint("deed_id", "user_id", name="pk_feed_likes"),)

    deed_id: Mapped[uuid.UUID] = mapped_column(_posts_fk())
    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_feed_likes_user_created_at", FeedLike.user_id, FeedLike.created_at.desc())


class FeedComment(Base):
    """A comment on a feed post. Text retention (Q38): an author deletion erases ``body`` at once;
    a doer, reporter or moderation deletion keeps it for ``deleted_comment_retention_days``
    (moderators and admins only), then ``feed.erase_deleted_comments`` erases it."""

    __tablename__ = "feed_comments"
    __table_args__ = (
        CheckConstraint("(deleted_at IS NULL) = (deleted_by IS NULL)", name="deleted_by_set"),
        CheckConstraint("(deleted_at IS NULL) = (deleted_role IS NULL)", name="deleted_role_set"),
        # The author's own deletion erases the text in the same transaction.
        CheckConstraint(
            "deleted_role IS DISTINCT FROM 'author' OR body IS NULL", name="author_delete_erases"
        ),
        # The deed's live comments, oldest first.
        Index(
            "ix_feed_comments_deed_live",
            "deed_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # The erasure job.
        Index(
            "ix_feed_comments_erasable_deleted_at",
            "deleted_at",
            postgresql_where=text("body IS NOT NULL AND deleted_at IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    deed_id: Mapped[uuid.UUID] = mapped_column(_posts_fk())
    author_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    # Schema ceiling; the product limit (300) is config. NULL once erased.
    body: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # No FK, like audit_log.actor_id.
    deleted_by: Mapped[uuid.UUID | None]
    deleted_role: Mapped[CommentDeletedRole | None] = mapped_column(
        _enum(CommentDeletedRole, "comment_deleted_role")
    )
    # When the author dismissed the "comment removed" notice (moderation removals only).
    removal_notice_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# An author's comments, newest first (also the removal notices).
Index("ix_feed_comments_author_created_at", FeedComment.author_id, FeedComment.created_at.desc())


class CommentReportCase(Base):
    """At most one open case per comment (Q38). Reports never hide a comment by themselves."""

    __tablename__ = "comment_report_cases"
    __table_args__ = (
        Index(
            "uq_comment_report_cases_open",
            "comment_id",
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
        CheckConstraint("priority BETWEEN 1 AND 3", name="priority_range"),
        CheckConstraint("report_count >= 0", name="report_count_non_negative"),
        # Moderator queue order.
        Index("ix_comment_report_cases_queue", "status", "priority", "opened_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    comment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feed_comments.id", ondelete="RESTRICT")
    )
    # Denormalised for the conflict-of-interest check.
    deed_id: Mapped[uuid.UUID] = mapped_column(_posts_fk())
    status: Mapped[ReportCaseStatus] = mapped_column(
        _enum(ReportCaseStatus, "comment_report_case_status")
    )
    # Minimum of COMMENT_REPORT_PRIORITY over the case's reports.
    priority: Mapped[int] = mapped_column(SmallInteger)
    report_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[CaseDecision | None] = mapped_column(
        _enum(CaseDecision, "comment_report_case_decision")
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(_users_fk())


Index(
    "ix_comment_report_cases_comment_resolved_at",
    CommentReportCase.comment_id,
    CommentReportCase.resolved_at.desc(),
)


class CommentReport(Base):
    """One report by a member (visitors can't see comments). A duplicate gets the same 202."""

    __tablename__ = "comment_reports"
    __table_args__ = (
        UniqueConstraint("case_id", "reporter_user_id", name="uq_comment_reports_case_reporter"),
        Index("ix_comment_reports_case_created_at", "case_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comment_report_cases.id", ondelete="RESTRICT")
    )
    comment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feed_comments.id", ondelete="RESTRICT")
    )
    reporter_user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    reason: Mapped[CommentReportReason] = mapped_column(
        _enum(CommentReportReason, "comment_report_reason", length=30)
    )
    # Moderators and admins only.
    details: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CommentDecision(Base):
    """Append-only record of moderator/admin keep / remove / restore decisions (Q38). Every row
    is also written to ``audit_log``."""

    __tablename__ = "comment_decisions"
    __table_args__ = (
        CheckConstraint("length(btrim(note)) >= 5", name="note_min_length"),
        Index("ix_comment_decisions_comment_created_at", "comment_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    comment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feed_comments.id", ondelete="RESTRICT")
    )
    # Null for a restore made outside a case.
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("comment_report_cases.id", ondelete="RESTRICT")
    )
    action: Mapped[CommentDecisionAction] = mapped_column(
        _enum(CommentDecisionAction, "comment_decision_action")
    )
    note: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FeedSave(Base):
    """Private saves; never counted or shown to anyone else."""

    __tablename__ = "feed_saves"
    __table_args__ = (PrimaryKeyConstraint("user_id", "deed_id", name="pk_feed_saves"),)

    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    deed_id: Mapped[uuid.UUID] = mapped_column(_posts_fk())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index(
    "ix_feed_saves_user_created_at",
    FeedSave.user_id,
    FeedSave.created_at.desc(),
    FeedSave.deed_id.desc(),
)


class FeedShare(Base):
    """Each share is recorded (PRD §8 ``Share``); analytics only (M8), no count is shown."""

    __tablename__ = "feed_shares"
    __table_args__ = (Index("ix_feed_shares_deed_id", "deed_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    deed_id: Mapped[uuid.UUID] = mapped_column(_posts_fk())
    # Null for a visitor share from the public page, if allowed later.
    user_id: Mapped[uuid.UUID | None] = mapped_column(_users_fk())
    target: Mapped[ShareTarget] = mapped_column(_enum(ShareTarget, "share_target"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Follow(Base):
    """Open one-way follows."""

    __tablename__ = "follows"
    __table_args__ = (
        PrimaryKeyConstraint("follower_id", "followed_id", name="pk_follows"),
        CheckConstraint("follower_id <> followed_id", name="not_self"),
    )

    follower_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    followed_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Followers list; Following list (the PK has no time order).
Index("ix_follows_followed_created_at", Follow.followed_id, Follow.created_at.desc())
Index("ix_follows_follower_created_at", Follow.follower_id, Follow.created_at.desc())


class FeedNoticeSeen(Base):
    """One-time notices a user has dismissed (Q41). Known keys are a code constant."""

    __tablename__ = "feed_notices_seen"
    __table_args__ = (PrimaryKeyConstraint("user_id", "notice_key", name="pk_feed_notices_seen"),)

    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    notice_key: Mapped[str] = mapped_column(String(40))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FeedTopSnapshot(Base):
    """A periodic Top ranking for one period (Q26). The job keeps the newest two per period, so
    a reader holding the previous snapshot's cursor can finish."""

    __tablename__ = "feed_top_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    period: Mapped[TopPeriod] = mapped_column(_enum(TopPeriod, "top_period", length=10))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The weights used, e.g. {"like": 1, "comment": 3}.
    weights: Mapped[dict[str, Any]] = mapped_column(JSONB)


Index(
    "ix_feed_top_snapshots_period_computed_at",
    FeedTopSnapshot.period,
    FeedTopSnapshot.computed_at.desc(),
)


class FeedTopEntry(Base):
    __tablename__ = "feed_top_entries"
    __table_args__ = (
        PrimaryKeyConstraint("snapshot_id", "rank", name="pk_feed_top_entries"),
        UniqueConstraint("snapshot_id", "deed_id", name="uq_feed_top_entries_snapshot_deed"),
        CheckConstraint("rank >= 1", name="rank_positive"),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feed_top_snapshots.id", ondelete="CASCADE")
    )
    rank: Mapped[int] = mapped_column(SmallInteger)
    deed_id: Mapped[uuid.UUID] = mapped_column(_posts_fk())
    score: Mapped[int] = mapped_column(Integer)
