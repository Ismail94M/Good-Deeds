"""Request and response bodies of the notifications API (docs/M6-DESIGN.md §3.2, N1 to N7)."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_serializer

LinkKind = Literal[
    "deed",
    "my_deed",
    "profile",
    "reviews",
    "home",
    "badges",
    "guidelines",
    "connections",
    "settings",
]


class UserRef(BaseModel):
    """A person named in a notification. ``handle`` is null and ``display_name`` is
    "Unavailable account" for an account that isn't active (M4 SEC3 L6, D109). ``avatar`` is the
    opaque profile-picture reference (D117), never a URL; null when there is none."""

    handle: str | None
    display_name: str
    avatar: str | None


class Link(BaseModel):
    kind: LinkKind
    target: str | None


class CanMute(BaseModel):
    """What the row's "⋯" menu may offer; a key is left out when it doesn't apply."""

    person: str | None = None
    deed: uuid.UUID | None = None

    @model_serializer(mode="wrap")
    def _drop_empty(self, handler: Any) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        return {k: v for k, v in data.items() if v is not None}


class NotificationItem(BaseModel):
    id: uuid.UUID
    kind: str
    category: str
    created_at: datetime
    updated_at: datetime
    read: bool
    text: str
    actors: list[UserRef]
    actor_count: int
    link: Link | None
    can_mute: CanMute


class NotificationPage(BaseModel):
    items: list[NotificationItem]
    next_cursor: str | None


class UnreadCount(BaseModel):
    unread: int
    # Unread rows new or updated since the bell was last opened (the bell shows this, D114).
    new_since_seen: int


class CategoryOut(BaseModel):
    id: str
    label: str
    description: str
    on: bool
    locked: bool


class MutedDeed(BaseModel):
    deed_id: uuid.UUID
    title_text: str


class Mutes(BaseModel):
    people: list[UserRef]
    deeds: list[MutedDeed]


class Preferences(BaseModel):
    categories: list[CategoryOut]
    mutes: Mutes


class PreferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Annotated[str, StringConstraints(max_length=40)]
    on: bool


class MuteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["person", "deed"]
    # A handle (person) or a deed id (deed).
    target: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
