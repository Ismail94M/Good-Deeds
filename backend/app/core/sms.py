"""SMS sending behind a small interface. Only the console provider exists until launch."""

import logging
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger("app.sms")


class SmsProvider(Protocol):
    async def send(self, phone_e164: str, message: str) -> None: ...


def mask_phone(phone_e164: str) -> str:
    return phone_e164[:3] + "*" * max(0, len(phone_e164) - 7) + phone_e164[-4:]


class ConsoleSmsProvider:
    """Development only: logs messages (number masked) and keeps the last one per number."""

    def __init__(self) -> None:
        self.sent: dict[str, str] = {}

    async def send(self, phone_e164: str, message: str) -> None:
        self.sent[phone_e164] = message
        logger.warning("DEV SMS to %s: %s", mask_phone(phone_e164), message)


_console = ConsoleSmsProvider()


def get_sms_provider() -> SmsProvider:
    """FastAPI dependency. Add real providers (MSG91, Twilio) here before launch."""
    settings = get_settings()
    if settings.sms_provider == "console":
        if not settings.is_local:  # also enforced by Settings validation
            raise RuntimeError("The console SMS provider is for development only")
        return _console
    raise RuntimeError(f"Unknown SMS provider {settings.sms_provider!r}")
