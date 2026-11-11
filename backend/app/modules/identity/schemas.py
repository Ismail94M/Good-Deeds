import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.modules.identity.models import Role

RESERVED_HANDLES = frozenset(
    {
        "admin",
        "api",
        "app",
        "gooddeeds",
        "help",
        "login",
        "logout",
        "me",
        "moderator",
        "root",
        "settings",
        "signup",
        "support",
        "system",
    }
)


def _check_handle(value: str) -> str:
    value = value.lower()
    if value in RESERVED_HANDLES:
        raise ValueError("This handle is reserved")
    if value.startswith(".") or value.endswith(".") or ".." in value:
        raise ValueError("Dots can't be at the start, end, or next to each other")
    return value


Handle = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=30, pattern=r"^[A-Za-z0-9_.]+$"
    ),
    AfterValidator(_check_handle),
]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Password = Annotated[str, StringConstraints(min_length=10, max_length=128)]


class AgeAndConsent(BaseModel):
    date_of_birth: date
    accept_terms: Literal[True] = Field(description="Must be true")
    accept_privacy: Literal[True] = Field(description="Must be true")


class SignupRequest(AgeAndConsent):
    email: EmailStr
    password: Password
    display_name: DisplayName
    handle: Handle


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class OAuthLoginRequest(BaseModel):
    token: Annotated[str, StringConstraints(min_length=10, max_length=8192)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class OnboardingStatus(BaseModel):
    adult: bool
    consents: bool
    phone: bool
    complete: bool


class Me(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None
    display_name: str
    handle: str
    city: str
    area: str | None
    bio: str | None
    phone_e164: str | None
    role: Role
    short_id: str | None
    created_at: datetime
    onboarding: OnboardingStatus


class PublicProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    handle: str
    display_name: str
    city: str
    area: str | None
    bio: str | None
    joined_at: datetime


class ProfileUpdate(BaseModel):
    display_name: DisplayName | None = None
    handle: Handle | None = None
    area: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    bio: Annotated[str, StringConstraints(strip_whitespace=True, max_length=280)] | None = None


class PhoneStartRequest(BaseModel):
    phone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=25)]


class PhoneVerifyRequest(PhoneStartRequest):
    code: Annotated[str, StringConstraints(pattern=r"^\d{6}$")]


class PhoneStartResponse(BaseModel):
    phone_e164: str
    expires_at: datetime
    resend_after_seconds: int


class QrCodeResponse(BaseModel):
    short_id: str
    display_id: str
    token: str
    scan_url: str
    expires_at: datetime
    refresh_after_seconds: int


class AuthConfig(BaseModel):
    google_client_id: str | None
    facebook_app_id: str | None
