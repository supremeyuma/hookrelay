"""Health and readiness behaviour, including dependency failure paths."""

from fastapi.testclient import TestClient

from app.api.health import database_ready, redis_ready
from app.main import app


async def _ready() -> bool:
    return True


async def _not_ready() -> bool:
    return False


def test_liveness(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_ok(client: TestClient) -> None:
    app.dependency_overrides[database_ready] = _ready
    app.dependency_overrides[redis_ready] = _ready

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "redis": "ok"}


def test_readiness_fails_when_database_is_down(client: TestClient) -> None:
    app.dependency_overrides[database_ready] = _not_ready
    app.dependency_overrides[redis_ready] = _ready

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "fail", "redis": "ok"}


def test_readiness_fails_when_redis_is_down(client: TestClient) -> None:
    app.dependency_overrides[database_ready] = _ready
    app.dependency_overrides[redis_ready] = _not_ready

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "ok", "redis": "fail"}
