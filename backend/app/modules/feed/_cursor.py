"""Opaque keyset-pagination cursors for the feed module (docs/M3-DESIGN.md §3.1, D40).

A copy of ``deeds/_cursor.py`` (that one is private to deeds): a cursor encodes the
``(timestamp, id)`` of the last item on a page; lists are newest first, so the next page is every
row strictly before that key. Top has its own cursor, ``(period, snapshot_id, rank)``, because its
order is a rank inside one snapshot.
"""

import base64
import binascii
import uuid
from datetime import datetime

from app.core.errors import AppError

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


def invalid_cursor() -> AppError:
    return AppError("That page link is no longer valid.", code="invalid_cursor")


def _b64(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _unb64(cursor: str) -> str:
    padded = cursor + "=" * (-len(cursor) % 4)
    return base64.urlsafe_b64decode(padded).decode()


def encode(at: datetime, item_id: uuid.UUID) -> str:
    return _b64(f"{at.isoformat()}|{item_id}")


def decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        at_text, id_text = _unb64(cursor).split("|")
        at = datetime.fromisoformat(at_text)
        if at.tzinfo is None:
            raise ValueError("naive timestamp")
        return at, uuid.UUID(id_text)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise invalid_cursor() from exc


def encode_top(period: str, snapshot_id: uuid.UUID, rank: int) -> str:
    return _b64(f"top|{period}|{snapshot_id}|{rank}")


def decode_top(cursor: str, period: str) -> tuple[uuid.UUID, int]:
    """A cursor from another period (or from any other list) is ``invalid_cursor``."""
    try:
        kind, cursor_period, snapshot_text, rank_text = _unb64(cursor).split("|")
        if kind != "top" or cursor_period != period:
            raise ValueError("not a cursor of this Top period")
        rank = int(rank_text)
        if rank < 1:
            raise ValueError("rank")
        return uuid.UUID(snapshot_text), rank
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise invalid_cursor() from exc


def encode_queue(queue: str, priority: int, at: datetime, item_id: uuid.UUID) -> str:
    """Admin comment-report queues: open by ``(priority, opened_at, id)``, resolved by
    ``(resolved_at DESC, id DESC)``. The cursor names its queue."""
    return _b64(f"queue|{queue}|{priority}|{at.isoformat()}|{item_id}")


def decode_queue(cursor: str, queue: str) -> tuple[int, datetime, uuid.UUID]:
    try:
        kind, cursor_queue, priority, at_text, id_text = _unb64(cursor).split("|")
        at = datetime.fromisoformat(at_text)
        if kind != "queue" or cursor_queue != queue or at.tzinfo is None:
            raise ValueError("not a cursor of this queue")
        return int(priority), at, uuid.UUID(id_text)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise invalid_cursor() from exc
