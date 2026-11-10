from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables prefixed with GD_."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="GD_", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://gooddeeds:gooddeeds@localhost:5432/gooddeeds"
    cors_origins: list[str] = ["http://localhost:5173"]

    @property
    def database_dsn(self) -> str:
        """Plain libpq DSN (no SQLAlchemy driver prefix), used by Procrastinate."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
