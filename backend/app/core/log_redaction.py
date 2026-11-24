"""Keep bearer-style secrets in URL paths out of the access log (security review L1).

Invite links (``/api/invites/by-token/{token}``, also ``.../accept``) and QR scan links
(``/scan/{token}``) carry their secret in the path, and uvicorn's access log prints the full path.
``install_access_log_redaction()`` adds a filter to the ``uvicorn.access`` logger that replaces
the token segment with ``<redacted>``. It is called when ``app.main`` is imported, which uvicorn
does after configuring its loggers, so it also applies under docker-compose's ``uvicorn``
command (including ``--reload`` workers).
"""

import logging
import re

REDACTED = "<redacted>"
ACCESS_LOGGER = "uvicorn.access"

# The segment after the prefix, up to the next "/", "?", "#" or whitespace.
_TOKEN_PATHS = re.compile(r"(/api/invites/by-token/|/scan/)[^/?#\s]+")


def redact_path(text: str) -> str:
    return _TOKEN_PATHS.sub(rf"\g<1>{REDACTED}", text)


class TokenRedactingFilter(logging.Filter):
    """Rewrites the record's message arguments (uvicorn passes the path as one of them) and
    its message; never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_path(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_path(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                k: redact_path(v) if isinstance(v, str) else v for k, v in record.args.items()
            }
        return True


def install_access_log_redaction() -> None:
    """Idempotent: adds the filter to ``uvicorn.access`` once."""
    logger = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, TokenRedactingFilter) for f in logger.filters):
        logger.addFilter(TokenRedactingFilter())
