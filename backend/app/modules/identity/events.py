import uuid

from app.core.events import DomainEvent


class UserRegistered(DomainEvent):
    event_type = "identity.user_registered"
    user_id: uuid.UUID
    method: str  # "password" | "google" | "facebook"


class PhoneVerified(DomainEvent):
    event_type = "identity.phone_verified"
    user_id: uuid.UUID


class PasswordChanged(DomainEvent):
    """The account's password was changed (M6A A26): an in-app "Your password was changed" row
    now, the email in M6B. ``method`` is how: ``phone_otp`` (forgot-password reset)."""

    event_type = "identity.password_changed"
    user_id: uuid.UUID
    method: str


class UserAnonymised(DomainEvent):
    """The account was deleted and anonymised (M2 D8). Class only in M6A: it gets an emitter
    when account deletion is built, and then media removes the picture and notifications the
    person's rows (D115, D122)."""

    event_type = "identity.user_anonymised"
    user_id: uuid.UUID
