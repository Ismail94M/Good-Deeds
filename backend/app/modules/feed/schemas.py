"""Request and response shapes of the feed API (docs/M3-DESIGN.md §3.2, §3.4, §3.4b).

``UserRef = {handle, display_name, avatar}`` is the only user information in any response: no
user IDs, phones or emails (``avatar`` is an opaque picture reference, null on public pages).
No response contains coordinates; the area is a name only.
"""

import uuid
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

TopPeriodId = Literal["week", "month", "year"]


class _Summary(Protocol):
    """identity's ``UserSummary`` (read through ``identity.service``)."""

    @property
    def handle(self) -> str: ...
    @property
    def display_name(self) -> str: ...
    @property
    def avatar_ref(self) -> str | None: ...


class UserRef(BaseModel):
    handle: str
    display_name: str
    # Opaque profile-picture reference (M6A D117), never a URL; null for no picture, an
    # unavailable account, or a public (signed-out) response.
    avatar: str | None = None

    @classmethod
    def of(cls, summary: _Summary) -> "UserRef":
        return cls(
            handle=summary.handle, display_name=summary.display_name, avatar=summary.avatar_ref
        )


class PhotoOut(BaseModel):
    """A ready, public photo with short-lived signed URLs (built by ``media.service``)."""

    id: uuid.UUID
    thumb_url: str
    display_url: str
    width: int
    height: int
    urls_expire_at: datetime


class PostOut(BaseModel):
    deed_id: uuid.UUID
    doer: UserRef
    reporter: UserRef
    category: str
    # The category's display label (§3.4b #11): visitors and not-onboarded users can't read
    # GET /api/deeds/options.
    category_label: str
    area_name: str
    occurred_at: datetime
    published_at: datetime
    # The deed text exactly as the reporter wrote it.
    description: str
    photos: list[PhotoOut]
    like_count: int
    comment_count: int
    liked: bool
    saved: bool
    following_doer: bool
    my_role: Literal["doer", "reporter"] | None
    # Top tab only.
    rank: int | None
    share_url: str


class VisibilityOut(BaseModel):
    """The caller's hide controls (separate hides per person, Q24)."""

    listed: bool
    hidden_by_me: bool
    hidden_by_other: bool
    can_hide: bool
    can_show: bool


class PostDetail(PostOut):
    beneficiaries_count: int | None
    verification: Literal["qr_scan", "invite_accept"]
    # Null unless the caller is the doer or reporter.
    visibility: VisibilityOut | None


class PostPage(BaseModel):
    items: list[PostOut]
    next_cursor: str | None


class PublicDeed(BaseModel):
    """The public share page (no auth): no viewer fields."""

    deed_id: uuid.UUID
    doer: UserRef
    reporter: UserRef
    category: str
    # The category's display label (§3.4b #11): visitors and not-onboarded users can't read
    # GET /api/deeds/options.
    category_label: str
    area_name: str
    occurred_at: datetime
    description: str
    photos: list[PhotoOut]
    like_count: int
    comment_count: int


class SocialOut(BaseModel):
    """Counts only (Q36: other people's lists are never shown)."""

    followers: int
    following: int
    # Null when signed out.
    is_following: bool | None
    is_me: bool


class TopPeriodOut(BaseModel):
    id: TopPeriodId
    days: int


class ReportReasonOut(BaseModel):
    id: str
    label: str
    priority: int


class FeedOptions(BaseModel):
    """``GET /api/feed/options`` (§3.4b #7): values the feed screens need, from config."""

    comment_max_chars: int
    top_badge_ranks: int
    top_periods: list[TopPeriodOut]
    comment_report_reasons: list[ReportReasonOut]
    report_details_max: int


# --------------------------------------------------- writes (§3.4 #3 to #9, #11)


class LikeOut(BaseModel):
    liked: bool
    like_count: int


class SaveOut(BaseModel):
    saved: bool


class CommentIn(BaseModel):
    # The product limit (``comment_max_chars``) is checked by the service, so a too-long comment
    # gets ``comment_too_long``; this is only a ceiling on the request size.
    body: str = Field(max_length=5000)


class CommentOut(BaseModel):
    id: uuid.UUID
    author: UserRef
    body: str
    created_at: datetime
    mine: bool
    # The author, or the deed's doer or reporter.
    can_delete: bool


class CommentPage(BaseModel):
    items: list[CommentOut]
    next_cursor: str | None


class ShareIn(BaseModel):
    target: Literal["whatsapp", "copy_link", "web_share"]


class ConnectionOut(BaseModel):
    user: UserRef
    # Whether I follow this person.
    following: bool


class ConnectionPage(BaseModel):
    items: list[ConnectionOut]
    next_cursor: str | None


# --------------------------------------------------- comment reports (#13) and notices


class CommentReportIn(BaseModel):
    # Checked against the reasons in the service (422 invalid_reason).
    reason: str = Field(max_length=40)
    # The product limit (report_details_max) is checked in the service; this is a ceiling.
    details: str | None = Field(default=None, max_length=5000)


class ReportReceived(BaseModel):
    status: Literal["received"] = "received"


class CommentNotice(BaseModel):
    """One of my comments removed by moderation: never who reported, who decided, or the note."""

    comment_id: uuid.UUID
    deed_id: uuid.UUID
    doer: UserRef
    # First 80 characters while the text is kept, else null.
    excerpt: str | None
    removed_at: datetime


# --------------------------------------------------- admin comment reports (#14 to #17)


class AdminDeedRef(BaseModel):
    doer: UserRef | None
    category: str
    area_name: str


class CommentCaseOut(BaseModel):
    case_id: uuid.UUID
    status: Literal["open", "resolved", "closed"]
    priority: int
    top_reason: str
    report_count: int
    opened_at: datetime
    resolved_at: datetime | None
    decision: Literal["kept", "removed"] | None


class CommentCaseItem(CommentCaseOut):
    oldest_report_at: datetime
    comment_excerpt: str
    author: UserRef | None
    deed: AdminDeedRef


class CommentCasePage(BaseModel):
    items: list[CommentCaseItem]
    next_cursor: str | None
    open_count: int
    resolved_count: int


class AdminCommentOut(BaseModel):
    id: uuid.UUID
    body: str | None
    author: UserRef | None
    created_at: datetime
    state: Literal["visible", "removed", "deleted"]
    restorable_until: datetime | None


class AdminCommentDeed(BaseModel):
    doer: UserRef | None
    reporter: UserRef | None
    category: str
    area_name: str
    listed: bool


class MemberReporterRef(BaseModel):
    kind: Literal["member"] = "member"
    handle: str


class CommentReportEntry(BaseModel):
    reason: str
    details: str | None
    reporter: MemberReporterRef
    created_at: datetime


class DecisionRecord(BaseModel):
    action: Literal["keep", "remove", "restore"]
    note: str
    actor: UserRef | None
    created_at: datetime


class CommentCaseDetail(BaseModel):
    case: CommentCaseOut
    comment: AdminCommentOut
    deed: AdminCommentDeed
    reports: list[CommentReportEntry]
    decisions: list[DecisionRecord]
    author_removed_last_90_days: int


class CommentDecisionIn(BaseModel):
    action: Literal["keep", "remove"]
    # Length rules use config (5 to decision_note_max), checked in the service.
    note: str = Field(max_length=10000)


class CommentRestoreIn(BaseModel):
    note: str = Field(max_length=10000)


# --------------------------------------------------- stories (D73)


class StoryRing(BaseModel):
    """One person in the stories bar. Counts only: photos load when the ring is opened."""

    user: UserRef
    is_me: bool
    # Deeds this person witnessed and reported (blue ring).
    reported_count: int
    # This person's own deeds verified by a witness (green ring).
    verified_count: int
    # Stories the caller hasn't watched; 0 means a grey ring.
    unseen_count: int
    latest_at: datetime


class StoryRings(BaseModel):
    items: list[StoryRing]
    seconds_per_story: int
    window_hours: int


class StoryOut(BaseModel):
    role: Literal["reported", "verified"]
    seen: bool
    post: PostOut


class StoryPerson(BaseModel):
    user: UserRef
    is_me: bool
    seconds_per_story: int
    # Oldest first.
    items: list[StoryOut]


class StorySeenIn(BaseModel):
    deed_id: uuid.UUID
    role: Literal["reported", "verified"]
