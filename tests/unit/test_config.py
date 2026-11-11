"""Settings validation: fail fast and use safe defaults."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID_FERNET = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="

BASE = {
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "secret_encryption_key": VALID_FERNET,
    "admin_api_key": "a" * 32,
}


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    settings = Settings(_env_file=None, **BASE)

    assert settings.signature_tolerance_seconds == 300
    assert settings.max_body_bytes == 262_144
    assert settings.log_level == "INFO"
    assert settings.allow_private_destinations is False


def test_missing_database_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    values = {k: v for k, v in BASE.items() if k != "database_url"}

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None, **values)


def test_invalid_fernet_key_fails() -> None:
    values = {**BASE, "secret_encryption_key": "not-a-fernet-key"}

    with pytest.raises(ValidationError, match="Fernet"):
        Settings(_env_file=None, **values)


def test_short_admin_key_fails() -> None:
    values = {**BASE, "admin_api_key": "short"}

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_invalid_log_level_fails() -> None:
    values = {**BASE, "log_level": "verbose"}

    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        Settings(_env_file=None, **values)


def test_body_cap_cannot_be_tiny() -> None:
    values = {**BASE, "max_body_bytes": 10}

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)
