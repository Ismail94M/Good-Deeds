"""GoogleVerifier against locally signed tokens (no network)."""

import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.modules.identity._oauth import GoogleVerifier, OAuthError

CLIENT_ID = "test-client.apps.googleusercontent.com"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _verifier() -> GoogleVerifier:
    verifier = GoogleVerifier(CLIENT_ID)
    signing_key = SimpleNamespace(key=_KEY.public_key())
    verifier._jwks = SimpleNamespace(  # type: ignore[assignment]
        get_signing_key_from_jwt=lambda _token: signing_key
    )
    return verifier


def _token(**overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "g-123",
        "email": "a@example.com",
        "email_verified": True,
        "name": "A",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, _KEY, algorithm="RS256")


async def test_accepts_valid_token() -> None:
    identity = await _verifier().verify(_token())
    assert (identity.provider, identity.subject, identity.email_verified) == (
        "google",
        "g-123",
        True,
    )


async def test_tolerates_server_clock_slightly_behind_google() -> None:
    ahead = int(time.time()) + 5
    identity = await _verifier().verify(_token(iat=ahead, nbf=ahead))
    assert identity.subject == "g-123"


@pytest.mark.parametrize(
    "overrides",
    [
        # Time offsets (seconds from now) are resolved inside the test, not at import time,
        # so a long test run can't turn the "future" token into a valid one.
        {"iat": 300, "nbf": 300},
        {"exp": -300},
        {"aud": "someone-else.apps.googleusercontent.com"},
        {"iss": "https://evil.example.com"},
    ],
)
async def test_rejects_bad_tokens(overrides: dict[str, Any]) -> None:
    now = int(time.time())
    claims = {k: now + v if k in ("iat", "nbf", "exp") else v for k, v in overrides.items()}
    with pytest.raises(OAuthError):
        await _verifier().verify(_token(**claims))
