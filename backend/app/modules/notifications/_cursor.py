"""Opaque keyset cursors for the notification list (docs/M6-DESIGN.md §3.2 N1).

A copy of the module-local pattern (each module keeps its own): ``(updated_at, id)`` descending,
newest activity first. The cursor names its tab (``all`` or ``unread``), so a cursor from the
other tab is ``invalid_cursor``.
"""

import base64
import binascii
import uuid
from datetime import datetime

from app.core.errors import AppError


def invalid_cursor() -> AppError:
    return AppError("That page link is no longer valid.", code="invalid_cursor")


def _b64(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _unb64(cursor: str) -> str:
    padded = cursor + "=" * (-len(cursor) % 4)
    return base64.urlsafe_b64decode(padded).decode()


def encode(tab: str, at: datetime, item_id: uuid.UUID) -> str:
    return _b64(f"n|{tab}|{at.isoformat()}|{item_id}")


def decode(cursor: str, tab: str) -> tuple[datetime, uuid.UUID]:
    try:
        kind, cursor_tab, at_text, id_text = _unb64(cursor).split("|")
        if kind != "n" or cursor_tab != tab:
            raise ValueError("not a cursor of this list")
        at = datetime.fromisoformat(at_text)
        if at.tzinfo is None:
            raise ValueError("naive timestamp")
        return at, uuid.UUID(id_text)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise invalid_cursor() from exc
