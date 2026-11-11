from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-only-insecure-secret-change-me-0123456789"
_LOCAL_ENVS = frozenset({"development", "test"})


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables prefixed with GD_.

    Defaults are production-safe: running locally needs GD_ENV=development (set in
    docker-compose.yml and .env.example), and production refuses to start with dev values.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="GD_", extra="ignore")

    env: str = "production"
    database_url: str = "postgresql+psycopg://gooddeeds:gooddeeds@localhost:5432/gooddeeds"
    cors_origins: list[str] = ["http://localhost:5173"]
    # Public URL of the web app; QR codes link to it.
    web_base_url: str = "http://localhost:5173"

    # Signing key for JWTs, QR tokens, OTP and device hashes (domain-separated per use).
    secret_key: str = _DEV_SECRET
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    # Absolute session lifetime, however often the refresh token is rotated.
    session_max_days: int = 90
    # Two tabs refreshing with the same cookie within this window is not treated as theft.
    refresh_reuse_grace_seconds: int = 10
    cookie_secure: bool = True

    # Phone verification (Indian mobile numbers only in v1)
    default_phone_region: str = "IN"
    sms_provider: str = "console"
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
    otp_resend_seconds: int = 30
    otp_max_per_hour: int = 5

    # Rate limits: "<count>/<seconds>"
    rate_login_ip: str = "20/900"
    rate_login_email: str = "10/900"
    rate_signup_ip: str = "10/3600"
    rate_phone_start_ip: str = "10/3600"
    rate_sms_global: str = "500/3600"
    rate_phone_verify_user: str = "20/3600"
    rate_refresh_ip: str = "120/900"
    rate_oauth_ip: str = "30/900"

    # Rotating QR codes: a token is valid for its window plus `qr_grace_windows` earlier ones.
    qr_window_seconds: int = 30
    qr_grace_windows: int = 2

    # OAuth (sign-in buttons are hidden when these are empty)
    google_client_id: str = ""
    facebook_app_id: str = ""
    facebook_app_secret: str = ""

    @property
    def is_local(self) -> bool:
        return self.env in _LOCAL_ENVS

    @model_validator(mode="after")
    def _refuse_unsafe_production_config(self) -> "Settings":
        if self.is_local:
            return self
        problems = []
        if self.secret_key == _DEV_SECRET or len(self.secret_key) < 32:
            problems.append("GD_SECRET_KEY must be a random string of at least 32 characters")
        if not self.cookie_secure:
            problems.append("GD_COOKIE_SECURE must be true")
        if self.sms_provider == "console":
            problems.append("GD_SMS_PROVIDER must be a real provider, not 'console'")
        if any(o == "*" or not o.startswith("https://") for o in self.cors_origins):
            problems.append("GD_CORS_ORIGINS must list explicit https:// origins")
        if problems:
            raise ValueError(f"Unsafe configuration for env={self.env!r}: " + "; ".join(problems))
        return self

    @property
    def database_dsn(self) -> str:
        """Plain libpq DSN (no SQLAlchemy driver prefix), used by Procrastinate."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def parse_rate(spec: str) -> tuple[int, int]:
    """'20/900' -> (20 requests, 900 seconds)."""
    count, seconds = spec.split("/")
    return int(count), int(seconds)


@lru_cache
def get_settings() -> Settings:
    return Settings()
