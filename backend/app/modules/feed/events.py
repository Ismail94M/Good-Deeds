"""Domain events emitted by the feed module (docs/M3-DESIGN.md §5).

Payloads carry IDs only: no comment text, no coordinates, no phone numbers. Nothing subscribes in
M3 (notifications are M6, analytics M8). Each is emitted only for a real insert: a repeated like or
follow, unlikes, unfollows, removed followers, hides and saves emit nothing.
"""

import uuid

from app.core.events import DomainEvent


class DeedLiked(DomainEvent):
    event_type = "feed.deed_liked"
    deed_id: uuid.UUID
    user_id: uuid.UUID


class CommentPosted(DomainEvent):
    event_type = "feed.comment_posted"
    deed_id: uuid.UUID
    comment_id: uuid.UUID
    author_id: uuid.UUID


class UserFollowed(DomainEvent):
    event_type = "feed.user_followed"
    follower_id: uuid.UUID
    followed_id: uuid.UUID


class CommentModerationDecided(DomainEvent):
    """A moderator or admin kept, removed or restored a reported comment (Q38)."""

    event_type = "feed.comment_moderation_decided"
    comment_id: uuid.UUID
    case_id: uuid.UUID | None
    deed_id: uuid.UUID
    author_id: uuid.UUID
    action: str
