"""Typed application settings loaded from the environment.

Every value is validated at import/boot time so a misconfigured deployment fails
immediately with a readable error instead of failing on the first request.
"""

from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Environment variable names are the upper-case field names (case-insensitive).
    Values passed to the constructor take precedence over the environment, which
    keeps tests explicit and deterministic.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(description="SQLAlchemy async DSN")
    redis_url: str = Field(description="Redis DSN")
    secret_encryption_key: str = Field(description="Fernet key used to encrypt source secrets")
    admin_api_key: str = Field(min_length=16, description="Bootstrap admin API key")

    signature_tolerance_seconds: int = Field(default=300, ge=0, le=3600)
    max_body_bytes: int = Field(default=262_144, ge=1_024)
    log_level: str = Field(default="INFO")
    allow_private_destinations: bool = Field(default=False)

    otel_exporter_otlp_endpoint: str | None = Field(default=None)
    otel_service_name: str = Field(default="hookrelay")

    @field_validator("secret_encryption_key")
    @classmethod
    def _validate_fernet_key(cls, value: str) -> str:
        try:
            Fernet(value.encode())
        except (ValueError, TypeError) as exc:
            msg = "SECRET_ENCRYPTION_KEY must be a valid Fernet key"
            raise ValueError(msg) from exc
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        normalized = value.upper()
        if normalized not in allowed:
            msg = f"LOG_LEVEL must be one of {sorted(allowed)}"
            raise ValueError(msg)
        return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, cached after the first successful load."""
    return Settings()
