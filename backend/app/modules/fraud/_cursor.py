"""Opaque keyset cursors for the fraud admin lists (docs/M5-DESIGN.md §3.1).

A copy of the deeds/feed/media pattern (each module keeps its own). Each cursor names its list,
so a cursor from another list or tab is ``invalid_cursor``:

- live queue (``open`` / ``parked``): ``(review_by NULLS LAST, opened_at, id)`` ascending;
- resolved queue: ``(resolved_at, id)`` descending;
- audit view: ``(created_at, id)`` descending.
"""

import base64
import binascii
import uuid
from datetime import datetime

from app.core.errors import AppError

_NULL = "-"


def invalid_cursor() -> AppError:
    return AppError("That page link is no longer valid.", code="invalid_cursor")


def _b64(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _unb64(cursor: str) -> str:
    padded = cursor + "=" * (-len(cursor) % 4)
    return base64.urlsafe_b64decode(padded).decode()


def _aware(text: str) -> datetime:
    at = datetime.fromisoformat(text)
    if at.tzinfo is None:
        raise ValueError("naive timestamp")
    return at


def encode_live(
    queue: str, review_by: datetime | None, opened_at: datetime, case_id: uuid.UUID
) -> str:
    due = review_by.isoformat() if review_by is not None else _NULL
    return _b64(f"live|{queue}|{due}|{opened_at.isoformat()}|{case_id}")


def decode_live(cursor: str, queue: str) -> tuple[datetime | None, datetime, uuid.UUID]:
    try:
        kind, cursor_queue, due, opened, id_text = _unb64(cursor).split("|")
        if kind != "live" or cursor_queue != queue:
            raise ValueError("not a cursor of this queue")
        return (None if due == _NULL else _aware(due)), _aware(opened), uuid.UUID(id_text)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise invalid_cursor() from exc


def encode_desc(kind: str, at: datetime, item_id: uuid.UUID) -> str:
    return _b64(f"{kind}|{at.isoformat()}|{item_id}")


def decode_desc(cursor: str, kind: str) -> tuple[datetime, uuid.UUID]:
    try:
        cursor_kind, at_text, id_text = _unb64(cursor).split("|")
        if cursor_kind != kind:
            raise ValueError("not a cursor of this list")
        return _aware(at_text), uuid.UUID(id_text)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise invalid_cursor() from exc
