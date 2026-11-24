"""Verify sign-in tokens from Google and Facebook.

The browser obtains the token with the provider's own SDK; the server only verifies it.
"""

import asyncio
import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Protocol

import httpx
import jwt

from app.core.config import get_settings

# httpx logs full request URLs at INFO; keep tokens out of logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class OAuthError(Exception):
    pass


@dataclass(frozen=True)
class OAuthIdentity:
    provider: str
    subject: str
    email: str | None
    email_verified: bool
    name: str | None


class OAuthVerifier(Protocol):
    async def verify(self, token: str) -> OAuthIdentity: ...


class GoogleVerifier:
    _ISSUERS = ("accounts.google.com", "https://accounts.google.com")
    _CLOCK_LEEWAY_SECONDS = 30

    def __init__(self, client_id: str) -> None:
        self._client_id = client_id
        self._jwks = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs", cache_keys=True)

    async def verify(self, token: str) -> OAuthIdentity:
        if not self._client_id:
            raise OAuthError("Google sign-in is not configured")
        try:
            key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=self._ISSUERS,
                # Google's `iat` is "now"; a server clock a second or two behind
                # (common in Docker Desktop) would otherwise reject every token.
                leeway=self._CLOCK_LEEWAY_SECONDS,
            )
        except jwt.PyJWTError as exc:
            # Log the reason only, never the token.
            logger.warning("Google token rejected: %s: %s", type(exc).__name__, exc)
            raise OAuthError("Invalid Google token") from exc
        return OAuthIdentity(
            provider="google",
            subject=str(claims["sub"]),
            email=claims.get("email"),
            email_verified=claims.get("email_verified") is True,
            name=claims.get("name"),
        )


class FacebookVerifier:
    _GRAPH = "https://graph.facebook.com/v21.0"

    def __init__(self, app_id: str, app_secret: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret

    async def verify(self, token: str) -> OAuthIdentity:
        if not (self._app_id and self._app_secret):
            raise OAuthError("Facebook sign-in is not configured")
        app_token = f"{self._app_id}|{self._app_secret}"
        proof = hmac.new(self._app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
        async with httpx.AsyncClient(timeout=10) as client:
            debug = await client.get(
                f"{self._GRAPH}/debug_token",
                params={"input_token": token},
                headers={"Authorization": f"Bearer {app_token}"},
            )
            data = debug.json().get("data", {}) if debug.is_success else {}
            if (
                not data.get("is_valid")
                or str(data.get("app_id")) != self._app_id
                or data.get("type") != "USER"
            ):
                raise OAuthError("Invalid Facebook token")
            me = await client.get(
                f"{self._GRAPH}/me",
                params={"fields": "id,name,email", "appsecret_proof": proof},
                headers={"Authorization": f"Bearer {token}"},
            )
            if not me.is_success:
                raise OAuthError("Could not read Facebook profile")
            profile = me.json()
        if str(profile.get("id")) != str(data.get("user_id")):
            raise OAuthError("Facebook token does not match the profile")
        return OAuthIdentity(
            provider="facebook",
            subject=str(profile["id"]),
            email=profile.get("email"),
            # Facebook only returns confirmed email addresses.
            email_verified=profile.get("email") is not None,
            name=profile.get("name"),
        )


_verifiers: dict[str, OAuthVerifier] | None = None


def get_oauth_verifiers() -> dict[str, OAuthVerifier]:
    """FastAPI dependency (overridden in tests)."""
    global _verifiers
    if _verifiers is None:
        s = get_settings()
        _verifiers = {
            "google": GoogleVerifier(s.google_client_id),
            "facebook": FacebookVerifier(s.facebook_app_id, s.facebook_app_secret),
        }
    return _verifiers
