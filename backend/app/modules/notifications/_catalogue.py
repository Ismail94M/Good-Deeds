"""The notification catalogue (docs/M6-DESIGN.md §4.1, D107, D108, D113): which kinds group and
how, which mutes apply, and the category labels. The texts themselves are built on read
(``_render.py``). Copy is interim, for PO review.
"""

import uuid
from dataclasses import dataclass
from datetime import date

from app.modules.notifications.models import (
    GROUPED_KINDS,
    KIND_CATEGORY,
    NotificationCategory,
    NotificationKind,
)

K = NotificationKind
C = NotificationCategory

# Grouped per deed (``like:{deed_id}``) or per IST day (``follow:{day}``), D108.
PER_DEED_GROUPS = frozenset({K.LIKE, K.COMMENT, K.REPLY})
PER_DAY_GROUPS = frozenset({K.FOLLOW, K.NETWORK})
assert PER_DEED_GROUPS | PER_DAY_GROUPS == GROUPED_KINDS

# D113: muting a person stops the Activity and Your network rows they cause; recognitions of
# you still arrive. Muting a deed stops its likes, comments and replies.
PERSON_MUTABLE_CATEGORIES = frozenset({C.ACTIVITY, C.NETWORK})
DEED_MUTABLE_KINDS = frozenset({K.LIKE, K.COMMENT, K.REPLY})

# Rows about a deed shown only while the viewer can still see it in the feed (D109); otherwise
# "This deed is no longer available" with no link and no names (PO Q93).
FEED_DEED_KINDS = frozenset({K.RECOGNISED, K.DEED_VERIFIED, K.NETWORK, K.LIKE, K.COMMENT, K.REPLY})


@dataclass(frozen=True)
class CategoryInfo:
    label: str
    description: str


CATEGORY_INFO: dict[NotificationCategory, CategoryInfo] = {
    C.ABOUT_YOU: CategoryInfo("About you", "When someone recognises you for a good deed."),
    C.NETWORK: CategoryInfo("Your network", "Good deeds shared by people you follow."),
    C.ACTIVITY: CategoryInfo("Activity", "Likes, comments and new followers."),
    C.ACCOUNT: CategoryInfo(
        "Account & safety", "Badges, titles, photo and comment reviews, and password changes."
    ),
    C.REVIEWS: CategoryInfo("Reviews", "When we'd like to hear from you, and review updates."),
}
# The order categories are listed in (N5).
CATEGORY_ORDER: tuple[NotificationCategory, ...] = (
    C.ABOUT_YOU,
    C.NETWORK,
    C.ACTIVITY,
    C.ACCOUNT,
    C.REVIEWS,
)


def category_of(kind: NotificationKind) -> NotificationCategory:
    return KIND_CATEGORY[kind]


def group_key(kind: NotificationKind, deed_id: uuid.UUID | None, day: date) -> str | None:
    """The group of a new row of ``kind`` (D108), or None for kinds that don't group."""
    if kind in PER_DEED_GROUPS:
        if deed_id is None:
            raise ValueError(f"{kind.value} notifications need a deed")
        return f"{kind.value}:{deed_id}"
    if kind in PER_DAY_GROUPS:
        return f"{kind.value}:{day.isoformat()}"
    return None
