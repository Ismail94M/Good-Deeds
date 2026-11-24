"""Checks shared by every free-text input of the feed (security review L8).

A NUL character (``\\x00``) can't be stored in a Postgres ``text`` value, so it would surface as
a 500, and it has no place in a comment, report or note. It is refused with 422 ``invalid_text``.
"""

from app.core.errors import AppError


class InvalidText(AppError):
    status_code = 422


def ensure_clean(*texts: str | None) -> None:
    if any(text is not None and "\x00" in text for text in texts):
        raise InvalidText("That text contains a character we can't accept.", code="invalid_text")
