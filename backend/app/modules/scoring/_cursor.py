"""Opaque keyset cursors for the scoring reads (a copy of the feed pattern: feed's is private).

- Ledger (#3): ``(created_at, id)`` of the last row; the list is newest first.
- Boards (#6): ``(snapshot_id, rank, user_id)`` of the last row; a board is ordered by rank,
  then user id. A board cursor names its snapshot, so a page never mixes two snapshots: when
  the snapshot was pruned, the cursor is ``invalid_cursor``.
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


def encode_ledger(at: datetime, entry_id: uuid.UUID) -> str:
    return _b64(f"ledger|{at.isoformat()}|{entry_id}")


def decode_ledger(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        kind, at_text, id_text = _unb64(cursor).split("|")
        at = datetime.fromisoformat(at_text)
        if kind != "ledger" or at.tzinfo is None:
            raise ValueError("not a ledger cursor")
        return at, uuid.UUID(id_text)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise invalid_cursor() from exc


def encode_board(snapshot_id: uuid.UUID, rank: int, user_id: uuid.UUID) -> str:
    return _b64(f"board|{snapshot_id}|{rank}|{user_id}")


def decode_board(cursor: str) -> tuple[uuid.UUID, int, uuid.UUID]:
    try:
        kind, snapshot_text, rank_text, user_text = _unb64(cursor).split("|")
        rank = int(rank_text)
        if kind != "board" or not 1 <= rank <= 2**31 - 1:
            raise ValueError("not a board cursor")
        return uuid.UUID(snapshot_text), rank, uuid.UUID(user_text)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise invalid_cursor() from exc
