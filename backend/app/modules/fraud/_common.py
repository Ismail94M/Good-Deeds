"""Small helpers shared by the fraud review files (errors, notes, time)."""

from datetime import UTC, datetime

from app.core.errors import AppError, NotFound
from app.modules.fraud._limits import FraudConfig


def utcnow() -> datetime:
    return datetime.now(UTC)


class Invalid(AppError):
    """422 for rules that depend on config (note length, per-deed actions)."""

    status_code = 422


def case_not_found() -> NotFound:
    """Also the answer for a conflicted admin (D90): they can't learn that a case exists."""
    return NotFound("Case not found.", code="case_not_found")


def user_not_found() -> NotFound:
    return NotFound("No one with that handle.", code="user_not_found")


def clean_note(note: str, config: FraudConfig) -> str:
    """The trimmed note, ``note_min_chars`` to ``note_max_chars`` long (D88)."""
    if "\x00" in note:
        raise Invalid("That text contains a character we can't accept.", code="invalid_text")
    text = note.strip()
    if len(text) < config.note_min_chars:
        raise Invalid(
            f"Add a note of at least {config.note_min_chars} characters.", code="note_required"
        )
    if len(text) > config.note_max_chars:
        raise Invalid("The note is too long.", code="note_too_long")
    return text
