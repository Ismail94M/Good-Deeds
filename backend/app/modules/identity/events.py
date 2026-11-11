import uuid

from app.core.events import DomainEvent


class UserRegistered(DomainEvent):
    event_type = "identity.user_registered"
    user_id: uuid.UUID
    method: str  # "password" | "google" | "facebook"


class PhoneVerified(DomainEvent):
    event_type = "identity.phone_verified"
    user_id: uuid.UUID
