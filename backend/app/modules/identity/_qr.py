"""Short IDs and rotating, signed QR tokens.

Token format: ``<short_id>.<window>.<signature>``, where ``window`` is the current
time bucket (qr_window_seconds long). A token is accepted for its own window and a
few earlier ones, so a screenshot stops working within about a minute and a half.
"""

import re
import secrets
import time
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.security import constant_time_equal, keyed_hash

# Crockford base32: no I, L, O, U, so IDs are easy to read aloud and type.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
SHORT_ID_LENGTH = 7
SHORT_ID_PREFIX = "GD-"
_SIGNATURE_LENGTH = 22
_LOOKALIKES = str.maketrans("OIL", "011")


def new_short_id() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(SHORT_ID_LENGTH))


def normalize_short_id(raw: str) -> str | None:
    """Accept user-typed IDs like 'gd-7k3m 9q0' or 'GD7K3M9Q0'. Returns None if malformed."""
    value = raw.strip().upper().replace(" ", "").replace("-", "")
    prefix = SHORT_ID_PREFIX.replace("-", "")
    if len(value) == SHORT_ID_LENGTH + len(prefix) and value.startswith(prefix):
        value = value[len(prefix) :]
    value = value.translate(_LOOKALIKES)
    if len(value) != SHORT_ID_LENGTH or any(c not in _ALPHABET for c in value):
        return None
    return value


def current_window(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // get_settings().qr_window_seconds)


def _signature(short_id: str, key_version: int, window: int) -> str:
    return keyed_hash("qr", short_id, str(key_version), str(window))[:_SIGNATURE_LENGTH]


def make_token(short_id: str, key_version: int, window: int) -> str:
    return f"{short_id}.{window}.{_signature(short_id, key_version, window)}"


def window_expiry(window: int) -> float:
    """Unix time when a token minted in `window` stops being accepted."""
    settings = get_settings()
    return float((window + 1 + settings.qr_grace_windows) * settings.qr_window_seconds)


@dataclass(frozen=True)
class ParsedToken:
    short_id: str
    window: int
    signature: str


_TOKEN_RE = re.compile(
    rf"([{_ALPHABET}]{{{SHORT_ID_LENGTH}}})\.([0-9]{{1,12}})\.([0-9a-f]{{{_SIGNATURE_LENGTH}}})",
    re.ASCII,
)


def parse_token(token: str) -> ParsedToken | None:
    """Strictly parse a token; anything malformed (incl. non-ASCII digits) is None."""
    match = _TOKEN_RE.fullmatch(token.strip()) if len(token) <= 64 else None
    if match is None:
        return None
    return ParsedToken(short_id=match[1], window=int(match[2]), signature=match[3])


def signature_valid(parsed: ParsedToken, key_version: int, now: float | None = None) -> bool:
    now_window = current_window(now)
    if not (now_window - get_settings().qr_grace_windows <= parsed.window <= now_window):
        return False
    expected = _signature(parsed.short_id, key_version, parsed.window)
    return constant_time_equal(expected, parsed.signature)
