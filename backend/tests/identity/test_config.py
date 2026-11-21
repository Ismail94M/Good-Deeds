"""Production refuses to start with unsafe settings. Settings are built directly
(never through the cached get_settings())."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import (
    _DEV_SECRET,
    DEV_STORAGE_ACCESS_KEY,
    DEV_STORAGE_SECRET_KEY,
    Settings,
)

GOOD_SECRET = "k" * 32


def _production(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": "production",
        "secret_key": GOOD_SECRET,
        "cookie_secure": True,
        "sms_provider": "msg91",
        "cors_origins": ["https://gooddeeds.in", "https://www.gooddeeds.in"],
        "storage_endpoint_url": "https://storage.googleapis.com",
        "storage_access_key_id": "GOOG1EXAMPLEHMACKEY",
        "storage_secret_access_key": "hmac-secret",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_valid_production_config_constructs() -> None:
    settings = _production()
    assert not settings.is_local
    assert settings.cookie_secure is True
    # Browsers get URLs for the same endpoint unless a public one is set.
    assert settings.storage_signing_endpoint_url == "https://storage.googleapis.com"
    public = _production(storage_public_endpoint_url="https://media.gooddeeds.in")
    assert public.storage_signing_endpoint_url == "https://media.gooddeeds.in"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"secret_key": _DEV_SECRET}, "GD_SECRET_KEY"),
        ({"secret_key": "k" * 31}, "GD_SECRET_KEY"),
        ({"cookie_secure": False}, "GD_COOKIE_SECURE"),
        ({"sms_provider": "console"}, "GD_SMS_PROVIDER"),
        ({"cors_origins": ["*"]}, "GD_CORS_ORIGINS"),
        ({"cors_origins": ["https://gooddeeds.in", "http://gooddeeds.in"]}, "GD_CORS_ORIGINS"),
        ({"cors_origins": ["http://localhost:5173"]}, "GD_CORS_ORIGINS"),
        ({"storage_endpoint_url": "http://storage:8333"}, "GD_STORAGE_ENDPOINT_URL"),
        ({"storage_public_endpoint_url": "http://localhost:8333"}, "GD_STORAGE_PUBLIC"),
        ({"storage_access_key_id": DEV_STORAGE_ACCESS_KEY}, "GD_STORAGE_ACCESS_KEY_ID"),
        ({"storage_access_key_id": ""}, "GD_STORAGE_ACCESS_KEY_ID"),
        ({"storage_secret_access_key": ""}, "GD_STORAGE_SECRET_ACCESS_KEY"),
        ({"storage_secret_access_key": DEV_STORAGE_SECRET_KEY}, "GD_STORAGE_SECRET_ACCESS_KEY"),
    ],
)
def test_unsafe_production_config_rejected(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _production(**overrides)


@pytest.mark.parametrize("env", ["production", "staging", "prod"])
def test_any_non_local_env_is_checked(env: str) -> None:
    with pytest.raises(ValidationError):
        _production(env=env, sms_provider="console")


def test_all_problems_reported_together() -> None:
    with pytest.raises(ValidationError) as exc:
        _production(secret_key=_DEV_SECRET, cookie_secure=False, sms_provider="console")
    text = str(exc.value)
    for name in ("GD_SECRET_KEY", "GD_COOKIE_SECURE", "GD_SMS_PROVIDER"):
        assert name in text


@pytest.mark.parametrize("env", ["development", "test"])
def test_local_envs_allow_dev_defaults(env: str) -> None:
    settings = Settings(
        _env_file=None,
        env=env,
        secret_key=_DEV_SECRET,
        cookie_secure=False,
        sms_provider="console",
        cors_origins=["http://localhost:5173"],
    )
    assert settings.is_local


def test_defaults_are_production_and_refuse_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GD_ENV",
        "GD_COOKIE_SECURE",
        "GD_SECRET_KEY",
        "GD_SMS_PROVIDER",
        "GD_CORS_ORIGINS",
        "GD_STORAGE_ACCESS_KEY_ID",
        "GD_STORAGE_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError, match="env='production'"):
        Settings(_env_file=None)
