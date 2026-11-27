"""Building notification text at read time (docs/M6-DESIGN.md §4.1, D106, D109, D101).

Rows hold ids only; names, deed categories, region names and dates are read live here, in a few
batched queries per page:

- An actor whose account isn't active reads "Unavailable account", with no handle and no
  avatar (M4 SEC3 L6).
- A row about a deed (recognitions, network, likes, comments, replies) is shown only while the
  viewer can still see the deed in the feed (``feed.service.visible_deed_ids``). Otherwise it
  reads "This deed is no longer available", with no link and no names, so it never says who
  withdrew or hid it (PO Q93).
- Review updates use neutral words only (M5 D101): never a case, a type, a detector, another
  person or an admin.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import service as deeds
from app.modules.feed.service import visible_deed_ids
from app.modules.identity import service as identity
from app.modules.notifications._catalogue import (
    DEED_MUTABLE_KINDS,
    FEED_DEED_KINDS,
    PERSON_MUTABLE_CATEGORIES,
)
from app.modules.notifications._limits import NotificationsConfig
from app.modules.notifications.models import Notification, NotificationKind
from app.modules.notifications.schemas import (
    CanMute,
    Link,
    LinkKind,
    NotificationItem,
    UserRef,
)

K = NotificationKind
UNAVAILABLE_NAME = "Unavailable account"
UNAVAILABLE_DEED = "This deed is no longer available"
UNAVAILABLE = UserRef(handle=None, display_name=UNAVAILABLE_NAME, avatar=None)
REVIEW_UPDATE = "There's an update for you in Reviews."
PASSWORD_CHANGED = "Your password was changed. If this wasn't you, contact support."
AVATAR_REMOVED = "Your profile picture was removed because it didn't follow our photo guidelines."
COMMENT_REMOVED = "Your comment was removed because it broke our community guidelines."
PHOTO_REMOVED = "A photo on your deed was removed after review."
PHOTO_KEPT = "Review complete: your photo stays up. No action needed."
# Why a profile picture was removed (Q95), by report reason code (media's removal notice
# wording; notifications may read only media's event classes, D104).
AVATAR_REMOVAL_REASONS: dict[str, str] = {
    "child_no_guardian_consent": "Shows a child",
    "impersonation": "Not this person / impersonation",
    "private_details": "Private details",
    "inappropriate": "Inappropriate or harmful",
    "not_consented": "Shows someone who didn't agree",
}


def avatar_ref(summary: identity.UserSummary) -> str | None:
    """The opaque avatar ref of a person (D117): identity already gives None for no picture or
    an account that isn't active."""
    return summary.avatar_ref


async def user_refs(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, UserRef]:
    """A ``UserRef`` for every id: active accounts by name, anyone else as "Unavailable
    account"."""
    ids = set(user_ids)
    active = await identity.active_user_ids(session, ids)
    found = await identity.public_summaries(session, active)
    refs: dict[uuid.UUID, UserRef] = {}
    for uid in ids:
        summary = found.get(uid)
        refs[uid] = (
            UNAVAILABLE
            if summary is None
            else UserRef(
                handle=summary.handle,
                display_name=summary.display_name,
                avatar=avatar_ref(summary),
            )
        )
    return refs


def names(people: Sequence[UserRef], count: int) -> str:
    """ "Ravi", "Ravi and Sneha", "Ravi, Sneha and Ahmed", "Ravi, Sneha and 4 others"."""
    shown = [p.display_name for p in people]
    count = max(count, len(shown))
    if not shown:
        return "Someone" if count <= 1 else f"{count} people"
    if count <= 3 and len(shown) >= count:
        if count == 1:
            return shown[0]
        return ", ".join(shown[: count - 1]) + f" and {shown[count - 1]}"
    head = shown[:2]
    others = count - len(head)
    tail = "1 other" if others == 1 else f"{others} others"
    return ", ".join(head) + f" and {tail}"


def day_text(at: date) -> str:
    """``5 December`` (no platform-specific strftime flags)."""
    return f"{at.day} {at.strftime('%B')}"


def month_text(month: date) -> str:
    return month.strftime("%B %Y")


@dataclass(frozen=True)
class _Context:
    viewer_id: uuid.UUID
    viewer_handle: str
    people: dict[uuid.UUID, UserRef]
    deed_views: dict[uuid.UUID, deeds.DeedView]
    visible: set[uuid.UUID]
    labels: dict[str, str]
    regions: dict[int, deeds.RegionView]
    config: NotificationsConfig


def _link(kind: LinkKind, target: str | uuid.UUID | None = None) -> Link:
    return Link(kind=kind, target=None if target is None else str(target))


def _deed_phrase(deed: deeds.DeedView | None, viewer_id: uuid.UUID) -> str:
    if deed is not None and deed.doer_id == viewer_id:
        return "the deed about you"
    return "the deed you recognised"


def _due_text(data: dict[str, Any], ctx: _Context) -> str | None:
    raw = data.get("reply_due_at")
    if not isinstance(raw, str):
        return None
    try:
        due = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if due.tzinfo is None:
        return None
    return day_text(due.astimezone(ctx.config.zone).date())


def _review_text(data: dict[str, Any], ctx: _Context) -> str:
    kind = data.get("review_kind")
    due = _due_text(data, ctx)
    if kind == "explanation_requested":
        text = "We'd like to hear from you about some of your recent deeds."
        return f"{text} Please reply by {due}." if due else f"{text} Please reply in Reviews."
    if kind == "explanation_reminder":
        text = "A reminder: we'd like to hear from you about some of your recent deeds."
        return f"{text} Please reply by {due}." if due else f"{text} Please reply in Reviews."
    return REVIEW_UPDATE


def _title_text(data: dict[str, Any], ctx: _Context) -> str:
    region_id = data.get("region_id")
    region = ctx.regions.get(region_id) if isinstance(region_id, int) else None
    where = region.name if region is not None else "your area"
    raw_month = data.get("period_month")
    month = None
    if isinstance(raw_month, str):
        try:
            month = month_text(date.fromisoformat(raw_month))
        except ValueError:
            month = None
    when = f" for {month}" if month else ""
    if data.get("level_kind") == "ward" and data.get("title_key", "hero") == "hero":
        return f"You're Kindness Hero of {where}{when}!"
    label = data.get("title_label")
    if isinstance(label, str) and label:
        filled = label.replace("{region}", where).replace("{month}", month or "").strip(" ·")
        return f"You earned the title {filled}!"
    return f"You made the Top 3 in {where}{when}!"


def _avatar_removed_text(data: dict[str, Any]) -> str:
    reason = data.get("removal_reason")
    label = AVATAR_REMOVAL_REASONS.get(reason) if isinstance(reason, str) else None
    return f"{AVATAR_REMOVED} Reason: {label}." if label else AVATAR_REMOVED


def _badge_text(data: dict[str, Any]) -> str:
    label = data.get("badge_label")
    if isinstance(label, str) and label:
        return f"You earned the {label} badge."
    return "You earned a new badge."


def _render_one(row: Notification, ctx: _Context) -> NotificationItem:
    kind = row.kind
    actors = [ctx.people[a] for a in row.actor_ids if a in ctx.people]
    count = row.actor_count
    deed = ctx.deed_views.get(row.deed_id) if row.deed_id is not None else None
    data = dict(row.data)
    link: Link | None = None
    mute = CanMute()

    if kind in FEED_DEED_KINDS and row.deed_id is not None and row.deed_id not in ctx.visible:
        return _item(row, UNAVAILABLE_DEED, [], 0, None, CanMute())

    who = names(actors, count)
    if kind == K.RECOGNISED:
        label = ctx.labels.get(deed.category, deed.category) if deed is not None else None
        text = (
            f"{who} recognised you for a good deed: {label}."
            if label
            else f"{who} recognised you for a good deed."
        )
        link = _link("deed", row.deed_id)
    elif kind == K.DEED_VERIFIED:
        text = f"Your deed about {who} was verified."
        link = _link("deed", row.deed_id)
    elif kind == K.NETWORK:
        if count <= 1:
            text = f"{who}, who you follow, shared a good deed today."
        else:
            text = f"{who} you follow shared good deeds today."
        link = _link("home")
    elif kind == K.LIKE:
        text = f"{who} liked {_deed_phrase(deed, ctx.viewer_id)}."
        link = _link("deed", row.deed_id)
    elif kind == K.COMMENT:
        text = f"{who} commented on {_deed_phrase(deed, ctx.viewer_id)}."
        link = _link("deed", row.deed_id)
    elif kind == K.REPLY:
        text = f"{who} also commented on a deed you commented on."
        link = _link("deed", row.deed_id)
    elif kind == K.FOLLOW:
        text = f"{who} started following you."
        link = _link("connections", "followers")
    elif kind == K.BADGE_AWARDED:
        text = _badge_text(data)
        link = _link("badges", ctx.viewer_handle)
    elif kind == K.TITLE_AWARDED:
        text = _title_text(data, ctx)
        link = _link("profile", ctx.viewer_handle)
    elif kind == K.PHOTO_REMOVED:
        text = PHOTO_REMOVED
        link = _link("my_deed", row.deed_id) if row.deed_id is not None else None
    elif kind == K.PHOTO_KEPT:
        text = PHOTO_KEPT
        link = _link("my_deed", row.deed_id) if row.deed_id is not None else None
    elif kind == K.PHOTO_REMOVED_BY_DOER:
        text = f"{who} removed 1 photo from this deed."
        link = _link("my_deed", row.deed_id) if row.deed_id is not None else None
    elif kind == K.COMMENT_REMOVED:
        text = COMMENT_REMOVED
        link = _link("guidelines", "community")
    elif kind == K.AVATAR_REMOVED:
        text = _avatar_removed_text(data)
        link = _link("guidelines", "photo")
    elif kind == K.PASSWORD_CHANGED:
        text = PASSWORD_CHANGED
    else:  # K.REVIEW_UPDATE
        text = _review_text(data, ctx)
        link = _link("reviews")

    if row.category in PERSON_MUTABLE_CATEGORIES:
        first = next((a.handle for a in actors if a.handle is not None), None)
        mute = CanMute(person=first)
    if kind in DEED_MUTABLE_KINDS and row.deed_id is not None:
        mute = CanMute(person=mute.person, deed=row.deed_id)
    return _item(row, text, actors, count, link, mute)


def _item(
    row: Notification,
    text: str,
    actors: list[UserRef],
    count: int,
    link: Link | None,
    mute: CanMute,
) -> NotificationItem:
    return NotificationItem(
        id=row.id,
        kind=row.kind.value,
        category=row.category.value,
        created_at=row.created_at,
        updated_at=row.updated_at,
        read=row.read_at is not None,
        text=text,
        actors=actors,
        actor_count=count,
        link=link,
        can_mute=mute,
    )


async def render(
    session: AsyncSession,
    rows: Sequence[Notification],
    *,
    viewer_id: uuid.UUID,
    viewer_handle: str,
    config: NotificationsConfig,
) -> list[NotificationItem]:
    """The items of one page, in the order of ``rows``."""
    actor_ids = {a for row in rows for a in row.actor_ids}
    deed_ids = {row.deed_id for row in rows if row.deed_id is not None}
    feed_deed_ids = {
        row.deed_id for row in rows if row.deed_id is not None and row.kind in FEED_DEED_KINDS
    }
    region_ids = {
        row.data["region_id"]
        for row in rows
        if row.kind == K.TITLE_AWARDED and isinstance(row.data.get("region_id"), int)
    }
    ctx = _Context(
        viewer_id=viewer_id,
        viewer_handle=viewer_handle,
        people=await user_refs(session, actor_ids),
        deed_views=await deeds.get_deeds(session, deed_ids),
        visible=await visible_deed_ids(session, viewer_id, feed_deed_ids),
        labels=await deeds.category_labels(session) if deed_ids else {},
        regions=await deeds.regions_by_id(session, region_ids),
        config=config,
    )
    return [_render_one(row, ctx) for row in rows]
