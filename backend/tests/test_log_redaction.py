"""Security L1: invite and scan tokens never reach the access log (``app.core.log_redaction``)."""

import logging

import pytest

from app.core.log_redaction import (
    ACCESS_LOGGER,
    REDACTED,
    TokenRedactingFilter,
    install_access_log_redaction,
    redact_path,
)
from app.main import create_app

TOKEN = "q9Xc_2-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (f"/api/invites/by-token/{TOKEN}", f"/api/invites/by-token/{REDACTED}"),
        (f"/api/invites/by-token/{TOKEN}/accept", f"/api/invites/by-token/{REDACTED}/accept"),
        (f"/api/invites/by-token/{TOKEN}?x=1", f"/api/invites/by-token/{REDACTED}?x=1"),
        (f"/scan/{TOKEN}", f"/scan/{REDACTED}"),
        ("/scan/7K3M9Q0.59071234.ab12", f"/scan/{REDACTED}"),
        # Nothing else changes.
        ("/api/health", "/api/health"),
        (
            "/api/scans/0b9e6c1a-1111-2222-3333-444455556666",
            "/api/scans/0b9e6c1a-1111-2222-3333-444455556666",
        ),
        ("/api/me/invites?status=pending", "/api/me/invites?status=pending"),
    ],
)
def test_redact_path(path: str, expected: str) -> None:
    assert redact_path(path) == expected


def _access_record(path: str) -> logging.LogRecord:
    """A record shaped like uvicorn's access log: the path is one of the ``args``."""
    return logging.LogRecord(
        ACCESS_LOGGER,
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("172.18.0.1:40228", "POST", f"/api/invites/by-token/{TOKEN}/accept", "1.1", 200),
        None,
    )


def test_filter_redacts_uvicorn_access_records_and_keeps_them() -> None:
    record = _access_record("")
    assert TokenRedactingFilter().filter(record) is True
    message = record.getMessage()
    assert TOKEN not in message
    assert message == (
        f'172.18.0.1:40228 - "POST /api/invites/by-token/{REDACTED}/accept HTTP/1.1" 200'
    )


def test_filter_redacts_a_token_in_the_message_itself() -> None:
    record = logging.LogRecord(
        ACCESS_LOGGER, logging.INFO, __file__, 1, f"GET /scan/{TOKEN}", None, None
    )
    TokenRedactingFilter().filter(record)
    assert record.getMessage() == f"GET /scan/{REDACTED}"


def test_creating_the_app_installs_the_filter_once() -> None:
    logger = logging.getLogger(ACCESS_LOGGER)
    create_app()
    install_access_log_redaction()
    assert sum(isinstance(f, TokenRedactingFilter) for f in logger.filters) == 1


def test_a_logged_token_is_redacted_end_to_end(caplog: pytest.LogCaptureFixture) -> None:
    install_access_log_redaction()
    logger = logging.getLogger(ACCESS_LOGGER)
    # Alembic's fileConfig (run by earlier migration tests) disables existing loggers.
    was_disabled, logger.disabled = logger.disabled, False
    try:
        with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
            logger.handle(_access_record(""))
    finally:
        logger.disabled = was_disabled
    assert TOKEN not in caplog.text
    assert f"/api/invites/by-token/{REDACTED}/accept" in caplog.text
