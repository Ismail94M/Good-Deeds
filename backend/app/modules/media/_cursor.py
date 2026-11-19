"""Opaque keyset cursors for the admin photo-report queues (§3.3 #11, D40).

A copy of the deeds/feed pattern (each module keeps its own). Open cases are ordered by
``(priority, opened_at, id)``; resolved ones by ``(resolved_at DESC, id)``. The cursor holds the
last item's key and which queue it belongs to.
"""

import base64
import binascii
import uuid
from datetime import datetime

from app.core.errors import AppError

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


def _invalid() -> AppError:
    return AppError("That page link is no longer valid.", code="invalid_cursor")


def encode(queue: str, priority: int, at: datetime, item_id: uuid.UUID) -> str:
    raw = f"{queue}|{priority}|{at.isoformat()}|{item_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode(cursor: str, queue: str) -> tuple[int, datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        q, priority, at_text, id_text = base64.urlsafe_b64decode(padded).decode().split("|")
        at = datetime.fromisoformat(at_text)
        if q != queue or at.tzinfo is None:
            raise ValueError("wrong queue or naive timestamp")
        return int(priority), at, uuid.UUID(id_text)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise _invalid() from exc
