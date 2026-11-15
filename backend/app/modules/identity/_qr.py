"""Short IDs, rotating signed QR tokens and the 6-digit manual codes.

Token format: ``<short_id>.<window>.<signature>``, where ``window`` is the current
time bucket (``window_seconds`` long). A token, and the manual code for the same window,
is accepted for its own window and ``grace_windows`` earlier ones.

Everything here is pure: the window length and grace come from the ``qr`` config
namespace (:class:`QrConfig`), which the caller loads and passes in.
"""

import re
import secrets
import time
from dataclasses import dataclass

from app.core.runtime_config import RuntimeConfig
from app.core.security import constant_time_equal, keyed_hash


class QrConfig(RuntimeConfig):
    """Config namespace ``qr`` (seeded by migration 0004)."""

    namespace = "qr"
    window_seconds: int
    grace_windows: int


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


def current_window(window_seconds: int, now: float | None = None) -> int:
    return int((time.time() if now is None else now) // window_seconds)


def _signature(short_id: str, key_version: int, window: int) -> str:
    return keyed_hash("qr", short_id, str(key_version), str(window))[:_SIGNATURE_LENGTH]


def make_token(short_id: str, key_version: int, window: int) -> str:
    return f"{short_id}.{window}.{_signature(short_id, key_version, window)}"


def manual_code(short_id: str, key_version: int, window: int) -> str:
    """The 6-digit code shown under the QR code. The ``qr-code`` purpose keeps it
    domain-separated from the token signature; rotating ``key_version`` changes it too."""
    digest = keyed_hash("qr-code", short_id, str(key_version), str(window))
    return f"{int(digest[:12], 16) % 1_000_000:06d}"


def accepted_windows(window_seconds: int, grace_windows: int, now: float | None = None) -> range:
    """The windows accepted at ``now``: the current one and ``grace_windows`` earlier ones."""
    now_window = current_window(window_seconds, now)
    return range(now_window - grace_windows, now_window + 1)


def window_expiry(window: int, window_seconds: int, grace_windows: int) -> float:
    """Unix time when a token (or code) minted in `window` stops being accepted."""
    return float((window + 1 + grace_windows) * window_seconds)


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


def signature_valid(
    parsed: ParsedToken,
    key_version: int,
    window_seconds: int,
    grace_windows: int,
    now: float | None = None,
) -> bool:
    if parsed.window not in accepted_windows(window_seconds, grace_windows, now):
        return False
    expected = _signature(parsed.short_id, key_version, parsed.window)
    return constant_time_equal(expected, parsed.signature)
