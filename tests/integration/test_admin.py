"""Admin authentication, source lifecycle, and secret rotation."""

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.security import compute_signature
from tests.integration.helpers import db_scalar

pytestmark = pytest.mark.integration


def _create(client: TestClient, admin_headers: dict[str, str], slug: str = "stripe") -> Any:
    return client.post(
        "/admin/sources",
        json={"slug": slug, "name": "Stripe"},
        headers=admin_headers,
    )


def _ingest(client: TestClient, slug: str, secret: str, key: str) -> Any:
    body = b'{"ok":true}'
    timestamp = int(time.time())
    signature = compute_signature(secret, timestamp, body)
    return client.post(
        f"/ingest/{slug}",
        content=body,
        headers={
            "X-HookRelay-Signature": f"t={timestamp},v1={signature}",
            "X-Idempotency-Key": key,
        },
    )


def test_admin_requires_key(client: TestClient) -> None:
    assert client.get("/admin/sources").status_code == 401
    assert client.get("/admin/sources", headers={"X-Admin-Key": "wrong"}).status_code == 401


def test_create_source_returns_secret_once(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = _create(client, admin_headers)

    assert response.status_code == 201
    created = response.json()
    assert created["secret"]
    assert created["slug"] == "stripe"

    listing = client.get("/admin/sources", headers=admin_headers)
    assert listing.status_code == 200
    assert "secret" not in listing.json()[0]
    assert "secret_encrypted" not in listing.json()[0]


def test_duplicate_slug_conflicts(client: TestClient, admin_headers: dict[str, str]) -> None:
    assert _create(client, admin_headers).status_code == 201
    assert _create(client, admin_headers).status_code == 409


def test_invalid_slug_is_422(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.post(
        "/admin/sources",
        json={"slug": "Not Valid!", "name": "Bad"},
        headers=admin_headers,
    )

    assert response.status_code == 422


def test_rotation_invalidates_old_secret(client: TestClient, admin_headers: dict[str, str]) -> None:
    created = _create(client, admin_headers).json()
    old_secret = created["secret"]

    assert _ingest(client, "stripe", old_secret, "k1").status_code == 202

    rotated = client.post("/admin/sources/stripe/rotate-secret", headers=admin_headers)
    assert rotated.status_code == 200
    new_secret = rotated.json()["secret"]
    assert new_secret != old_secret

    assert _ingest(client, "stripe", old_secret, "k2").status_code == 401
    assert _ingest(client, "stripe", new_secret, "k3").status_code == 202
    assert db_scalar("SELECT count(*) FROM events") == 2
