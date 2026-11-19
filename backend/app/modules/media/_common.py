"""Small helpers shared by the media module's private files."""

from datetime import UTC, datetime

from app.core.errors import AppError, NotFound


def utcnow() -> datetime:
    return datetime.now(UTC)


class Invalid(AppError):
    """422 for rules that depend on config (blur regions, photo counts in one request)."""

    status_code = 422


def photo_not_found() -> NotFound:
    return NotFound("Photo not found.", code="photo_not_found")


def deed_not_found() -> NotFound:
    return NotFound("Deed not found.", code="deed_not_found")


def self_log_not_found() -> NotFound:
    return NotFound("Self-log not found.", code="self_log_not_found")
