"""Scoped API key lifecycle and enforcement."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_delivery import (
    create_source_with_destination,
    ingest_event,
)

pytestmark = pytest.mark.integration


def _create_key(
    client: TestClient,
    admin_headers: dict[str, str],
    scopes: list[str],
    *,
    name: str = "test key",
) -> dict[str, Any]:
    response = client.post(
        "/admin/api-keys",
        json={"name": name, "scopes": scopes},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_list_and_revoke(client: TestClient, admin_headers: dict[str, str]) -> None:
    created = _create_key(client, admin_headers, ["events:read"])
    assert created["key"].startswith("hr_live_")
    assert created["scopes"] == ["events:read"]

    listing = client.get("/admin/api-keys", headers=admin_headers)
    assert listing.status_code == 200
    serialized = listing.text
    assert created["key"] not in serialized
    assert "key_hash" not in serialized

    revoked = client.delete(f"/admin/api-keys/{created['id']}", headers=admin_headers)
    assert revoked.status_code == 200
    assert revoked.json()["active"] is False

    rejected = client.get(
        "/events/00000000-0000-0000-0000-000000000000",
        headers={"X-API-Key": created["key"]},
    )
    assert rejected.status_code == 401


def test_scope_enforcement(client: TestClient, admin_headers: dict[str, str]) -> None:
    source, _ = create_source_with_destination(client, admin_headers)
    event_id = ingest_event(client, source)

    read_key = _create_key(client, admin_headers, ["events:read"])["key"]
    read_headers = {"X-API-Key": read_key}

    assert client.get(f"/events/{event_id}", headers=read_headers).status_code == 200
    assert (
        client.post(
            "/admin/sources",
            json={"slug": "blocked", "name": "Blocked"},
            headers=read_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/events/{event_id}/replay",
            json={},
            headers={**read_headers, "Idempotency-Key": "scope-1"},
        ).status_code
        == 403
    )

    replay_key = _create_key(client, admin_headers, ["replay:write"])["key"]
    replayed = client.post(
        f"/events/{event_id}/replay",
        json={},
        headers={"X-API-Key": replay_key, "Idempotency-Key": "scope-2"},
    )
    assert replayed.status_code == 200
    assert replayed.json()["deliveries_created"] == 1

    assert client.get("/admin/sources", headers={"X-API-Key": replay_key}).status_code == 403


def test_missing_unknown_and_invalid_scope(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    assert client.get("/admin/api-keys").status_code == 401
    assert client.get("/admin/api-keys", headers={"X-API-Key": "not-a-real-key"}).status_code == 401

    invalid = client.post(
        "/admin/api-keys",
        json={"name": "bad", "scopes": ["root:everything"]},
        headers=admin_headers,
    )
    assert invalid.status_code == 422
