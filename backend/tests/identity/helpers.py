"""Shared helpers for identity tests (typed, so mypy strict covers them)."""

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from http.cookies import Morsel, SimpleCookie
from typing import Any
from zoneinfo import ZoneInfo

import phonenumbers
from httpx import AsyncClient, Response

from app.core.security import decode_access_token
from app.core.sms import ConsoleSmsProvider
from app.modules.identity._oauth import OAuthError, OAuthIdentity, OAuthVerifier

PASSWORD = "correct-horse-battery"
REFRESH_COOKIE = "gd_refresh"


IST = ZoneInfo("Asia/Kolkata")


def ist_today() -> date:
    """The service decides age with today's date in Bangalore."""
    return datetime.now(IST).date()


def years_ago(years: int, today: date | None = None) -> date:
    today = today or ist_today()
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29 February
        return today.replace(year=today.year - years, day=28)


def adult_dob() -> str:
    return years_ago(30).isoformat()


def unique_handle(prefix: str = "u") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:10]}@example.com"


def valid_phone(index: int) -> str:
    """A valid Indian mobile number in E.164 (index 0-99)."""
    number = f"+9198765432{index:02d}"
    parsed = phonenumbers.parse(number, "IN")
    assert phonenumbers.is_valid_number(parsed), number
    return number


def signup_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "email": unique_email(),
        "password": PASSWORD,
        "display_name": "Asha Rao",
        "handle": unique_handle(),
        "date_of_birth": adult_dob(),
        "accept_terms": True,
        "accept_privacy": True,
    }
    payload.update(overrides)
    return payload


def set_cookie(response: Response, name: str = REFRESH_COOKIE) -> Morsel[str] | None:
    """The Set-Cookie morsel for `name` in this response, if any."""
    for header in response.headers.get_list("set-cookie"):
        jar: SimpleCookie = SimpleCookie()
        jar.load(header)
        if name in jar:
            return jar[name]
    return None


@dataclass
class Session:
    user_id: uuid.UUID
    access: str
    refresh: str
    email: str | None
    handle: str | None

    @property
    def headers(self) -> dict[str, str]:
        return bearer(self.access)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def session_from(
    response: Response, email: str | None = None, handle: str | None = None
) -> Session:
    body = response.json()
    cookie = set_cookie(response)
    assert cookie is not None, response.headers
    claims = decode_access_token(body["access_token"])
    return Session(
        user_id=uuid.UUID(claims["sub"]),
        access=body["access_token"],
        refresh=cookie.value,
        email=email,
        handle=handle,
    )


async def signup(client: AsyncClient, device_id: str | None = None, **overrides: Any) -> Session:
    payload = signup_payload(**overrides)
    headers = {"X-Device-Id": device_id} if device_id else {}
    response = await client.post("/api/auth/signup", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return session_from(response, payload["email"].lower(), payload["handle"].lower())


async def login(
    client: AsyncClient, email: str, password: str = PASSWORD, device_id: str | None = None
) -> Response:
    headers = {"X-Device-Id": device_id} if device_id else {}
    client.cookies.clear()
    return await client.post(
        "/api/auth/login", json={"email": email, "password": password}, headers=headers
    )


async def refresh(client: AsyncClient, raw: str, device_id: str | None = None) -> Response:
    client.cookies.clear()
    headers = {"Cookie": f"{REFRESH_COOKIE}={raw}"}
    if device_id:
        headers["X-Device-Id"] = device_id
    return await client.post("/api/auth/refresh", headers=headers)


def otp_from(sms: ConsoleSmsProvider, phone_e164: str) -> str:
    match = re.search(r"\b(\d{6})\b", sms.sent[phone_e164])
    assert match is not None
    return match.group(1)


async def start_phone(client: AsyncClient, s: Session, phone: str) -> Response:
    return await client.post("/api/users/me/phone/start", json={"phone": phone}, headers=s.headers)


async def verify_phone(client: AsyncClient, s: Session, phone: str, code: str) -> Response:
    return await client.post(
        "/api/users/me/phone/verify", json={"phone": phone, "code": code}, headers=s.headers
    )


async def onboard_phone(
    client: AsyncClient, sms: ConsoleSmsProvider, s: Session, phone: str
) -> None:
    started = await start_phone(client, s, phone)
    assert started.status_code == 200, started.text
    verified = await verify_phone(client, s, phone, otp_from(sms, phone))
    assert verified.status_code == 200, verified.text


async def onboarded_user(
    client: AsyncClient, sms: ConsoleSmsProvider, phone_index: int, **overrides: Any
) -> Session:
    s = await signup(client, **overrides)
    await onboard_phone(client, sms, s, valid_phone(phone_index))
    return s


class FakeVerifier:
    """OAuth verifier that knows a fixed set of tokens. Never calls a provider."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.identities: dict[str, OAuthIdentity] = {}

    def add(
        self,
        subject: str,
        email: str | None = None,
        email_verified: bool = True,
        name: str | None = "Ravi Kumar",
    ) -> str:
        token = f"{self.provider}-token-{uuid.uuid4().hex}"
        self.identities[token] = OAuthIdentity(
            provider=self.provider,
            subject=subject,
            email=email,
            email_verified=email_verified,
            name=name,
        )
        return token

    async def verify(self, token: str) -> OAuthIdentity:
        identity = self.identities.get(token)
        if identity is None:
            raise OAuthError("Invalid token")
        return identity


class FakeOAuth:
    def __init__(self) -> None:
        self.google = FakeVerifier("google")
        self.facebook = FakeVerifier("facebook")

    def verifiers(self) -> dict[str, OAuthVerifier]:
        return {"google": self.google, "facebook": self.facebook}
