"""Typed values of the ``identity`` config namespace (docs/M2-DESIGN.md §6, task B10).

Every key is required and has no default: the ``config_versions`` row (seeded as ``identity`` v1
by migration 0006) is the only source of these values. Change them by publishing a new version.
Secrets, URLs, token lifetimes and the cookie flag are deployment settings and stay in env
(``app.core.config.Settings``).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import RuntimeConfig, load


class IdentityLimits(RuntimeConfig):
    """Config namespace ``identity``."""

    namespace = "identity"

    # Phone verification codes
    otp_ttl_minutes: int
    otp_max_attempts: int
    otp_resend_seconds: int
    otp_max_per_hour: int
    # Rate limits ("<count>/<seconds>")
    rate_login_ip: str
    rate_login_email: str
    rate_signup_ip: str
    rate_phone_start_ip: str
    rate_sms_global: str
    rate_phone_verify_user: str
    rate_refresh_ip: str
    rate_oauth_ip: str


async def load_limits(session: AsyncSession) -> IdentityLimits:
    """The active ``identity`` config. Fails closed if it is missing or invalid."""
    _, limits = await load(session, IdentityLimits)
    return limits
