from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-only-insecure-secret-change-me-0123456789"
_LOCAL_ENVS = frozenset({"development", "test"})
# Access key of the local SeaweedFS identity (infra/seaweedfs/s3.json); refused outside dev.
DEV_STORAGE_ACCESS_KEY = "gooddeeds-dev"
DEV_STORAGE_SECRET_KEY = "gooddeeds-dev-secret-not-for-production"


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables prefixed with GD_.

    Only deployment settings live here (secrets, URLs, token lifetimes, cookie flag, providers).
    Product limits are versioned config rows (app.core.runtime_config).

    Defaults are production-safe: running locally needs GD_ENV=development (set in
    docker-compose.yml and .env.example), and production refuses to start with dev values.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="GD_", extra="ignore")

    env: str = "production"
    database_url: str = "postgresql+psycopg://gooddeeds:gooddeeds@127.0.0.1:5432/gooddeeds"
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
    # Product limits (OTP and rate limits) are not here: they live in versioned config rows
    # (app.core.runtime_config), e.g. the ``identity`` namespace.

    # OAuth (sign-in buttons are hidden when these are empty)
    google_client_id: str = ""
    facebook_app_id: str = ""
    facebook_app_secret: str = ""

    # Object storage (photos), S3-compatible API only (app.core.storage). Production: Google
    # Cloud Storage's XML API with HMAC keys; development: SeaweedFS (docker-compose).
    # Endpoint the API and worker call.
    storage_endpoint_url: str = "https://storage.googleapis.com"
    # Endpoint put into signed URLs for browsers (signing is offline). Empty: same as above.
    storage_public_endpoint_url: str = ""
    # "auto" for GCS (Google's boto3 sample); "us-east-1" for SeaweedFS.
    storage_region: str = "auto"
    storage_bucket: str = "gooddeeds-media"
    storage_access_key_id: str = ""
    storage_secret_access_key: str = ""
    storage_addressing_style: Literal["path", "virtual"] = "path"

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
        if not all(
            url.startswith("https://")
            for url in (self.storage_endpoint_url, self.storage_public_endpoint_url)
            if url
        ):
            problems.append("GD_STORAGE_ENDPOINT_URL and GD_STORAGE_PUBLIC_ENDPOINT_URL need https")
        if self.storage_access_key_id in ("", DEV_STORAGE_ACCESS_KEY):
            problems.append("GD_STORAGE_ACCESS_KEY_ID must be a real key, not the development one")
        if self.storage_secret_access_key in ("", DEV_STORAGE_SECRET_KEY):
            problems.append(
                "GD_STORAGE_SECRET_ACCESS_KEY must be a real secret, not the development one"
            )
        if problems:
            raise ValueError(f"Unsafe configuration for env={self.env!r}: " + "; ".join(problems))
        return self

    @property
    def storage_signing_endpoint_url(self) -> str:
        """Endpoint used in URLs signed for browsers."""
        return self.storage_public_endpoint_url or self.storage_endpoint_url

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
