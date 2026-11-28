"""Domain events emitted by the media module (docs/M3-DESIGN.md §5).

Payloads are IDs and enums only: no text, no coordinates, no image data or URLs. Nothing
subscribes in M3 (M6 adds notifications). M4: ``scoring`` subscribes to ``DeedPhotoReady`` only
(photo evidence, docs/M4-DESIGN.md D47, D51, §5), never to a removal or moderation event.
"""

import uuid
from datetime import datetime
from typing import Literal

from app.core.events import DomainEvent


class PhotoRemovedByDoer(DomainEvent):
    """The doer of a verified deed removed a photo from it (Q29). Final: never restorable."""

    event_type = "media.photo_removed_by_doer"
    photo_id: uuid.UUID
    deed_id: uuid.UUID
    uploader_id: uuid.UUID
    doer_id: uuid.UUID


class PhotoModerationDecided(DomainEvent):
    """A moderator or admin kept, removed or restored a reported photo (D42). M7's Moderation
    module can take over report intake along this seam."""

    event_type = "media.photo_moderation_decided"
    photo_id: uuid.UUID
    case_id: uuid.UUID | None
    deed_id: uuid.UUID
    uploader_id: uuid.UUID
    action: Literal["keep", "remove", "restore"]
    removal_reason: str | None


class DeedPhotoReady(DomainEvent):
    """A deed photo finished processing (``ready``), emitted in the same transaction (M4 §5).

    Attaching to a deed is only possible inside the photo add window, so every deed photo that
    reaches ``ready`` was attached within it; ``attached_at`` lets a subscriber apply its own
    window (scoring's ``photo_bonus_window_hours``). Emitted at most once per photo: a photo
    reaches ``ready`` once and never goes back to processing."""

    event_type = "media.deed_photo_ready"
    photo_id: uuid.UUID
    deed_id: uuid.UUID
    uploader_id: uuid.UUID
    attached_at: datetime
    # When processing finished (``photos.processed_at``): scoring's photo evidence time, so a
    # worker backlog never decides the photo bonus (M4 SEC3 L5). None only on older events.
    ready_at: datetime | None = None


# --------------------------------------------------- profile pictures (M6A, docs/M6-DESIGN.md §5)

type AvatarChangeReason = Literal["ready", "removed_by_owner", "admin_removed", "restored"]


class AvatarChanged(DomainEvent):
    """A person's live profile picture changed (D117): a new one became ready, the owner removed
    theirs, an admin removed it, or an admin restored it. ``avatar_ref`` is the opaque reference
    (``{photo_id}-{version}``, never a URL), or None when the person has no picture now.

    ``identity`` stores it in ``users.avatar_ref`` and keeps the newest ``changed_at``, so events
    handled out of order never overwrite a newer picture (§4.4)."""

    event_type = "media.avatar_changed"
    user_id: uuid.UUID
    avatar_ref: str | None
    changed_at: datetime
    reason: AvatarChangeReason


class AvatarModerationDecided(DomainEvent):
    """A moderator or admin kept, removed or restored a reported profile picture (D121). Avatar
    decisions emit this instead of ``PhotoModerationDecided`` (whose ``deed_id`` is required).
    ``notifications`` tells the owner why on ``remove`` (Q95); ``removal_reason`` is the report
    reason code (the text is built where it is shown)."""

    event_type = "media.avatar_moderation_decided"
    photo_id: uuid.UUID
    case_id: uuid.UUID | None
    user_id: uuid.UUID
    action: Literal["keep", "remove", "restore"]
    removal_reason: str | None
