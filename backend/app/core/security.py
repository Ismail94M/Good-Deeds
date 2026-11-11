"""Password hashing, JWT access tokens and keyed hashes. No business rules here."""

import asyncio
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

_hasher = PasswordHasher()
_JWT_ALGORITHM = "HS256"
# argon2 uses ~64 MiB per hash; cap concurrent hashing per worker so a login flood can't
# exhaust memory, and run it off the event loop so other requests keep flowing.
_hash_slots = asyncio.Semaphore(4)
# Verified against when the account doesn't exist, so response time doesn't reveal it.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


async def hash_password(password: str) -> str:
    async with _hash_slots:
        return await asyncio.to_thread(_hasher.hash, password)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


async def verify_password(password_hash: str | None, password: str) -> bool:
    """Check a password. Pass None for unknown accounts: it still does the same work."""
    async with _hash_slots:
        ok = await asyncio.to_thread(_verify, password_hash or _DUMMY_HASH, password)
    return ok and password_hash is not None


def create_access_token(user_id: uuid.UUID, role: str, token_version: int) -> tuple[str, datetime]:
    settings = get_settings()
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.access_token_minutes)
    claims = {
        "sub": str(user_id),
        "role": role,
        "ver": token_version,
        "type": "access",
        "iat": now,
        "exp": expires,
    }
    return jwt.encode(claims, settings.secret_key, algorithm=_JWT_ALGORITHM), expires


def decode_access_token(token: str) -> dict[str, Any]:
    """Return the claims, or raise jwt.InvalidTokenError."""
    claims: dict[str, Any] = jwt.decode(
        token,
        get_settings().secret_key,
        algorithms=[_JWT_ALGORITHM],
        options={"require": ["exp", "sub"]},
    )
    if claims.get("type") != "access":
        raise jwt.InvalidTokenError("Not an access token")
    return claims


def new_opaque_token() -> str:
    """A random, URL-safe token for refresh tokens, invite links, etc."""
    return secrets.token_urlsafe(32)


def sha256_hex(value: str) -> str:
    """Unkeyed hash for high-entropy tokens stored at rest."""
    return hashlib.sha256(value.encode()).hexdigest()


def keyed_hash(*parts: str) -> str:
    """HMAC-SHA256 with the app secret, for low-entropy secrets (OTP codes, device IDs).

    The first part names the purpose ("qr", "otp", "device", ...) for domain separation.
    """
    message = "\x1f".join(parts).encode()
    return hmac.new(get_settings().secret_key.encode(), message, hashlib.sha256).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
