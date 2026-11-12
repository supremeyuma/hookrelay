"""Outbox expansion and delivery attempts against real Postgres."""

import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import SecretCipher
from app.core.security import compute_signature, parse_signature_header, verify_signature
from app.models.dead_letter import DeadLetter
from app.models.delivery import Delivery
from app.models.event import Event
from app.models.outbox import Outbox
from app.services.delivery import (
    deliver_once,
    enqueue_due_deliveries,
    publish_outbox,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
INGEST_BODY = b'{"type":"charge.succeeded","amount":4200}'


def create_source_with_destination(
    client: TestClient,
    admin_headers: dict[str, str],
    *,
    urls: tuple[str, ...] = ("http://127.0.0.1:9099/ok",),
    slug: str = "stripe",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = client.post(
        "/admin/sources",
        json={"slug": slug, "name": "Stripe"},
        headers=admin_headers,
    )
    assert source.status_code == 201, source.text

    destinations = []
    for url in urls:
        response = client.post(
            f"/admin/sources/{slug}/destinations",
            json={"url": url},
            headers=admin_headers,
        )
        assert response.status_code == 201, response.text
        destinations.append(response.json())
    return source.json(), destinations


def ingest_event(client: TestClient, source: dict[str, Any], *, key: str = "evt_1") -> UUID:
    timestamp = int(time.time())
    signature = compute_signature(source["secret"], timestamp, INGEST_BODY)
    response = client.post(
        f"/ingest/{source['slug']}",
        content=INGEST_BODY,
        headers={
            "Content-Type": "application/json",
            "X-HookRelay-Signature": f"t={timestamp},v1={signature}",
            "X-Idempotency-Key": key,
        },
    )
    assert response.status_code == 202, response.text
    return UUID(response.json()["event_id"])


async def _deliveries(session: AsyncSession, event_id: UUID) -> list[Delivery]:
    return list(
        (
            await session.scalars(
                select(Delivery).where(Delivery.event_id == event_id).order_by(Delivery.attempt)
            )
        ).all()
    )


async def _event_status(session: AsyncSession, event_id: UUID) -> str:
    event = await session.get(Event, event_id)
    assert event is not None
    return event.status


async def test_successful_delivery_and_signed_headers(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    cipher: SecretCipher,
) -> None:
    source, destinations = create_source_with_destination(client, admin_headers)
    event_id = ingest_event(client, source)

    enqueued: list[UUID] = []

    async def enqueue(delivery_id: UUID) -> None:
        enqueued.append(delivery_id)

    assert await publish_outbox(db_session, enqueue, now=NOW) == 1
    assert len(enqueued) == 1
    assert await _event_status(db_session, event_id) == "delivering"

    deliveries = await _deliveries(db_session, event_id)
    assert len(deliveries) == 1
    delivery = deliveries[0]

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, text="ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await deliver_once(db_session, http, delivery.id, cipher, now=NOW, rng=lambda: 0.5)

    assert result is not None
    assert result.status == "success"
    assert await _event_status(db_session, event_id) == "delivered"

    detail = client.get(f"/events/{event_id}")
    assert detail.status_code == 200
    attempts = detail.json()["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["status"] == "success"
    assert attempts[0]["response_code"] == 200

    request = captured[0]
    body = request.content
    assert json.loads(body)["amount"] == 4200
    assert request.headers["X-HookRelay-Event-Id"] == str(event_id)
    assert request.headers["X-HookRelay-Delivery-Key"] == str(delivery.delivery_key)
    assert request.headers["X-HookRelay-Attempt"] == "1"

    parts = parse_signature_header(request.headers["X-HookRelay-Signature"])
    verify_signature(
        secret=destinations[0]["secret"],
        header=request.headers["X-HookRelay-Signature"],
        body=body,
        tolerance_seconds=300,
        now=parts.timestamp,
    )

    # Idempotent: a second attempt for the same row does nothing.
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await deliver_once(db_session, http, delivery.id, cipher, now=NOW) is None
    await db_session.refresh(delivery)
    assert delivery.status == "success"


async def test_retry_then_success(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    cipher: SecretCipher,
) -> None:
    source, _ = create_source_with_destination(client, admin_headers)
    event_id = ingest_event(client, source)

    async def enqueue(delivery_id: UUID) -> None:
        return None

    await publish_outbox(db_session, enqueue, now=NOW)
    statuses = [500, 500, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(statuses.pop(0), text="upstream error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        deliveries = await _deliveries(db_session, event_id)
        first = await deliver_once(
            db_session, http, deliveries[0].id, cipher, now=NOW, rng=lambda: 0.5
        )
        assert first is not None
        assert first.status == "failed"
        assert first.next_attempt_at is not None
        assert (first.next_attempt_at - NOW).total_seconds() == pytest.approx(2.0)

        deliveries = await _deliveries(db_session, event_id)
        assert [d.attempt for d in deliveries] == [1, 2]
        second = await deliver_once(
            db_session,
            http,
            deliveries[1].id,
            cipher,
            now=first.next_attempt_at,
            rng=lambda: 0.5,
        )
        assert second is not None
        assert second.next_attempt_at is not None
        assert (second.next_attempt_at - first.next_attempt_at).total_seconds() == pytest.approx(
            4.0
        )

        deliveries = await _deliveries(db_session, event_id)
        third = await deliver_once(
            db_session,
            http,
            deliveries[2].id,
            cipher,
            now=second.next_attempt_at,
            rng=lambda: 0.5,
        )

    assert third is not None
    assert third.status == "success"
    assert statuses == []
    deliveries = await _deliveries(db_session, event_id)
    assert [d.status for d in deliveries] == ["failed", "failed", "success"]
    assert await _event_status(db_session, event_id) == "delivered"


async def test_permanent_failure_is_not_retried(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    cipher: SecretCipher,
) -> None:
    source, _ = create_source_with_destination(client, admin_headers)
    event_id = ingest_event(client, source)

    async def enqueue(delivery_id: UUID) -> None:
        return None

    await publish_outbox(db_session, enqueue, now=NOW)
    delivery = (await _deliveries(db_session, event_id))[0]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad payload")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await deliver_once(db_session, http, delivery.id, cipher, now=NOW)

    assert result is not None
    assert result.status == "failed"
    assert result.error_class == "http_400"
    assert len(await _deliveries(db_session, event_id)) == 1
    assert await _event_status(db_session, event_id) == "dead"
    assert (await db_session.scalars(select(DeadLetter))).all() == []


async def test_retry_after_is_respected(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    cipher: SecretCipher,
) -> None:
    source, _ = create_source_with_destination(client, admin_headers)
    event_id = ingest_event(client, source)

    async def enqueue(delivery_id: UUID) -> None:
        return None

    await publish_outbox(db_session, enqueue, now=NOW)
    delivery = (await _deliveries(db_session, event_id))[0]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"}, text="slow down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await deliver_once(db_session, http, delivery.id, cipher, now=NOW, rng=lambda: 0.5)

    assert result is not None
    assert result.next_attempt_at is not None
    assert (result.next_attempt_at - NOW).total_seconds() >= 7.0


async def test_timeout_is_retryable(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    cipher: SecretCipher,
) -> None:
    source, _ = create_source_with_destination(client, admin_headers)
    event_id = ingest_event(client, source)

    async def enqueue(delivery_id: UUID) -> None:
        return None

    await publish_outbox(db_session, enqueue, now=NOW)
    delivery = (await _deliveries(db_session, event_id))[0]

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await deliver_once(db_session, http, delivery.id, cipher, now=NOW)

    assert result is not None
    assert result.status == "failed"
    assert result.error_class == "timeout"
    assert len(await _deliveries(db_session, event_id)) == 2


async def test_exhaustion_creates_dead_letter(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    cipher: SecretCipher,
) -> None:
    source, _ = create_source_with_destination(client, admin_headers)
    event_id = ingest_event(client, source)

    async def enqueue(delivery_id: UUID) -> None:
        return None

    await publish_outbox(db_session, enqueue, now=NOW)
    delivery = (await _deliveries(db_session, event_id))[0]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="still down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await deliver_once(db_session, http, delivery.id, cipher, max_attempts=1, now=NOW)

    assert result is not None
    assert result.status == "dead"
    dead_letters = list((await db_session.scalars(select(DeadLetter))).all())
    assert len(dead_letters) == 1
    assert dead_letters[0].reason == "http_500"
    assert await _event_status(db_session, event_id) == "dead"


async def test_inactive_destination_gets_no_delivery(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    source, destinations = create_source_with_destination(client, admin_headers)
    disabled = client.patch(
        f"/admin/destinations/{destinations[0]['id']}",
        json={"active": False},
        headers=admin_headers,
    )
    assert disabled.status_code == 200
    event_id = ingest_event(client, source)

    enqueued: list[UUID] = []

    async def enqueue(delivery_id: UUID) -> None:
        enqueued.append(delivery_id)

    assert await publish_outbox(db_session, enqueue, now=NOW) == 0
    assert enqueued == []
    assert await _deliveries(db_session, event_id) == []
    outbox = (await db_session.scalars(select(Outbox))).one()
    assert outbox.published_at is not None
    assert await _event_status(db_session, event_id) == "pending"


async def test_partial_delivery_status(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    cipher: SecretCipher,
) -> None:
    source, _ = create_source_with_destination(
        client,
        admin_headers,
        urls=("http://127.0.0.1:9099/ok", "http://127.0.0.1:9099/fail"),
    )
    event_id = ingest_event(client, source)

    async def enqueue(delivery_id: UUID) -> None:
        return None

    await publish_outbox(db_session, enqueue, now=NOW)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ok":
            return httpx.Response(200, text="ok")
        return httpx.Response(400, text="nope")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        for delivery in await _deliveries(db_session, event_id):
            await deliver_once(db_session, http, delivery.id, cipher, now=NOW)

    assert await _event_status(db_session, event_id) == "partial"
    statuses = sorted(d.status for d in await _deliveries(db_session, event_id))
    assert statuses == ["failed", "success"]


async def test_publish_is_idempotent_and_recovery_reenqueues(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    source, _ = create_source_with_destination(client, admin_headers)
    event_id = ingest_event(client, source)

    async def failing_enqueue(delivery_id: UUID) -> None:
        raise RuntimeError("redis down")

    assert await publish_outbox(db_session, failing_enqueue, now=NOW) == 1
    deliveries = await _deliveries(db_session, event_id)
    assert len(deliveries) == 1
    assert deliveries[0].status == "pending"

    # A second publish does not duplicate the delivery (outbox is marked published).
    async def enqueue(delivery_id: UUID) -> None:
        return None

    assert await publish_outbox(db_session, enqueue, now=NOW) == 0

    recovered: list[UUID] = []

    async def collect(delivery_id: UUID) -> None:
        recovered.append(delivery_id)

    assert await enqueue_due_deliveries(db_session, collect, now=NOW) == 1
    assert recovered == [deliveries[0].id]
