"""Ingest, HMAC, idempotency, and rate-limit behaviour against real Postgres/Redis."""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.security import compute_signature
from tests.integration.helpers import db_scalar

pytestmark = pytest.mark.integration

BODY = b'{"type":"charge.succeeded","amount":4200}'


def create_source(
    client: TestClient,
    admin_headers: dict[str, str],
    *,
    slug: str = "stripe",
    **overrides: Any,
) -> dict[str, Any]:
    response = client.post(
        "/admin/sources",
        json={"slug": slug, "name": "Stripe", **overrides},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    return f"t={ts},v1={compute_signature(secret, ts, body)}"


def post(
    client: TestClient,
    source: dict[str, Any],
    *,
    body: bytes = BODY,
    key: str | None = "idem-1",
    timestamp: int | None = None,
    secret: str | None = None,
    signature: str | None = None,
    include_signature: bool = True,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-Idempotency-Key"] = key
    if include_signature:
        headers["X-HookRelay-Signature"] = signature or sign(
            secret or source["secret"], body, timestamp
        )
    if extra_headers:
        headers.update(extra_headers)
    return client.post(f"/ingest/{source['slug']}", content=body, headers=headers)


def test_valid_event_is_accepted(client: TestClient, admin_headers: dict[str, str]) -> None:
    source = create_source(client, admin_headers)

    response = post(client, source)

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "accepted"
    assert response.headers["Location"] == f"/events/{payload['event_id']}"

    detail = client.get(f"/events/{payload['event_id']}", headers=admin_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["payload"]["amount"] == 4200
    assert body["headers"]["x-idempotency-key"] == "idem-1"
    assert "x-hookrelay-signature" not in body["headers"]
    assert body["attempts"] == []

    assert db_scalar("SELECT count(*) FROM events") == 1
    assert db_scalar("SELECT count(*) FROM outbox") == 1


def test_duplicate_returns_original_event(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    source = create_source(client, admin_headers)

    first = post(client, source)
    second = post(client, source)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert second.json()["event_id"] == first.json()["event_id"]
    assert db_scalar("SELECT count(*) FROM events") == 1
    assert db_scalar("SELECT count(*) FROM outbox") == 1


def test_external_id_is_stored(client: TestClient, admin_headers: dict[str, str]) -> None:
    source = create_source(client, admin_headers)

    response = post(client, source, extra_headers={"X-External-Id": "evt_ext_1"})

    assert response.status_code == 202
    detail = client.get(f"/events/{response.json()['event_id']}", headers=admin_headers).json()
    assert detail["external_id"] == "evt_ext_1"


def test_wrong_signature_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    source = create_source(client, admin_headers)

    response = post(client, source, secret="wrong-secret")

    assert response.status_code == 401
    assert db_scalar("SELECT count(*) FROM events") == 0


def test_stale_timestamp_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    source = create_source(client, admin_headers)

    response = post(client, source, timestamp=int(time.time()) - 301)

    assert response.status_code == 401
    assert db_scalar("SELECT count(*) FROM events") == 0


def test_missing_signature_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    source = create_source(client, admin_headers)

    response = post(client, source, include_signature=False)

    assert response.status_code == 401
    assert db_scalar("SELECT count(*) FROM events") == 0


def test_malformed_signature_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    source = create_source(client, admin_headers)

    response = post(client, source, signature="garbage")

    assert response.status_code == 401


def test_unknown_source_is_404(client: TestClient) -> None:
    response = client.post(
        "/ingest/nope",
        content=BODY,
        headers={
            "X-HookRelay-Signature": sign("s", BODY),
            "X-Idempotency-Key": "k",
        },
    )

    assert response.status_code == 404


def test_oversized_body_is_413_before_hmac(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    source = create_source(client, admin_headers)
    body = b'{"x":"' + b"a" * 300_000 + b'"}'

    response = post(client, source, body=body)

    assert response.status_code == 413
    assert db_scalar("SELECT count(*) FROM events") == 0


def test_malformed_json_is_400(client: TestClient, admin_headers: dict[str, str]) -> None:
    source = create_source(client, admin_headers)

    response = post(client, source, body=b"{")

    assert response.status_code == 400


def test_non_object_json_is_422(client: TestClient, admin_headers: dict[str, str]) -> None:
    source = create_source(client, admin_headers)

    response = post(client, source, body=b"[1,2,3]")

    assert response.status_code == 422


def test_missing_idempotency_key_is_422(client: TestClient, admin_headers: dict[str, str]) -> None:
    source = create_source(client, admin_headers)

    response = post(client, source, key=None)

    assert response.status_code == 422


def test_rate_limit_returns_retry_after(client: TestClient, admin_headers: dict[str, str]) -> None:
    source = create_source(client, admin_headers, rate_limit_per_second=1, burst=1)

    first = post(client, source, key="k1")
    second = post(client, source, key="k2")

    assert first.status_code == 202
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert db_scalar("SELECT count(*) FROM events") == 1


def test_100_concurrent_duplicates_create_one_event(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    source = create_source(client, admin_headers, burst=1000)

    def call(_: int) -> int:
        return post(client, source, key="concurrent-key").status_code

    with ThreadPoolExecutor(max_workers=20) as pool:
        statuses = list(pool.map(call, range(100)))

    assert statuses.count(202) == 1
    assert statuses.count(200) == 99
    assert db_scalar("SELECT count(*) FROM events") == 1
    assert db_scalar("SELECT count(*) FROM outbox") == 1


def test_unknown_event_is_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.get("/events/00000000-0000-0000-0000-000000000000", headers=admin_headers)

    assert response.status_code == 404
