"""Opaque keyset-pagination cursors for the deeds module's list endpoints (§3.2 #5, #6, #11).

A cursor encodes the ``(timestamp, id)`` of the last item on a page. Lists are ordered newest
first, so the next page is every row strictly before that key.
"""

import base64
import binascii
import uuid
from datetime import datetime

from app.core.errors import AppError

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


def encode(at: datetime, item_id: uuid.UUID) -> str:
    raw = f"{at.isoformat()}|{item_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        at_text, id_text = base64.urlsafe_b64decode(padded).decode().split("|")
        at = datetime.fromisoformat(at_text)
        if at.tzinfo is None:
            raise ValueError("naive timestamp")
        return at, uuid.UUID(id_text)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AppError("That page link is no longer valid.", code="invalid_cursor") from exc
