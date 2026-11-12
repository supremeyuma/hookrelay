"""Shared test configuration.

Environment defaults are installed before the application module is imported so
the app factory can boot without a developer-local `.env` file.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://hookrelay:hookrelay@localhost:5432/hookrelay"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault(
    "SECRET_ENCRYPTION_KEY",
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
)
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key-at-least-16-chars")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("ALLOW_PRIVATE_DESTINATIONS", "true")


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Run the real lifespan; dependency overrides are cleared afterwards."""
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
