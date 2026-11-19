"""Domain events emitted by the media module (docs/M3-DESIGN.md §5).

Payloads are IDs and enums only: no text, no coordinates, no image data or URLs. Nothing
subscribes in M3 (M6 adds notifications).
"""

import uuid
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
