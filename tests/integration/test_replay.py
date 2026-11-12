"""Dead-letter and event replay, idempotency, and concurrency safety."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import SecretCipher
from app.models.dead_letter import DeadLetter
from app.models.replay import Replay
from app.services.delivery import deliver_once, publish_outbox
from tests.integration.test_delivery import (
    NOW,
    _deliveries,
    _event_status,
    create_source_with_destination,
    ingest_event,
)

pytestmark = pytest.mark.integration


async def _noop(delivery_id: UUID) -> None:
    return None


async def _exhaust(
    session: AsyncSession,
    cipher: SecretCipher,
    event_id: UUID,
) -> DeadLetter:
    await publish_outbox(session, _noop, now=NOW)
    delivery = (await _deliveries(session, event_id))[0]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await deliver_once(
            session,
            http,
            delivery.id,
            cipher,
            max_attempts=1,
            now=NOW,
        )

    return (await session.scalars(select(DeadLetter))).one()


async def test_dead_letter_replay_is_idempotent(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    cipher: SecretCipher,
) -> None:
    source, _ = create_source_with_destination(client, admin_headers)
    event_id = ingest_event(client, source)
    dead_letter = await _exhaust(db_session, cipher, event_id)

    url = f"/admin/dead-letters/{dead_letter.id}/replay"
    assert client.post(url, headers=admin_headers).status_code == 422

    headers = {**admin_headers, "Idempotency-Key": "dlq-replay-1"}
    first = client.post(url, headers=headers)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["deliveries_created"] == 1
    assert first_body["event_id"] == str(event_id)

    db_session.expire_all()
    await db_session.refresh(dead_letter)
    assert dead_letter.replayed_at is not None

    deliveries = await _deliveries(db_session, event_id)
    assert [delivery.attempt for delivery in deliveries] == [1, 2]
    assert deliveries[1].status == "pending"
    assert deliveries[1].delivery_key == deliveries[0].delivery_key

    duplicate = client.post(url, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["replay_id"] == first_body["replay_id"]

    db_session.expire_all()
    assert len(await _deliveries(db_session, event_id)) == 2

    pending = (await _deliveries(db_session, event_id))[1]

    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(ok_handler)) as http:
        await deliver_once(db_session, http, pending.id, cipher, now=NOW)

    assert await _event_status(db_session, event_id) == "delivered"
    attempts = client.get(f"/events/{event_id}", headers=admin_headers).json()["attempts"]
    assert [attempt["status"] for attempt in attempts] == ["dead", "success"]


async def test_event_replay_failed_only_then_all(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    cipher: SecretCipher,
) -> None:
    source, destinations = create_source_with_destination(
        client,
        admin_headers,
        urls=("http://127.0.0.1:9099/ok", "http://127.0.0.1:9099/fail"),
    )
    event_id = ingest_event(client, source)
    await publish_outbox(db_session, _noop, now=NOW)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ok":
            return httpx.Response(200, text="ok")
        return httpx.Response(400, text="nope")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        for delivery in await _deliveries(db_session, event_id):
            await deliver_once(db_session, http, delivery.id, cipher, now=NOW)

    assert await _event_status(db_session, event_id) == "partial"
    failed_destination = next(
        destination for destination in destinations if destination["url"].endswith("/fail")
    )

    failed_only = client.post(
        f"/events/{event_id}/replay",
        json={"failed_only": True},
        headers={**admin_headers, "Idempotency-Key": "event-replay-1"},
    )
    assert failed_only.status_code == 200
    assert failed_only.json()["deliveries_created"] == 1

    db_session.expire_all()
    deliveries = await _deliveries(db_session, event_id)
    pending = [delivery for delivery in deliveries if delivery.status == "pending"]
    assert len(pending) == 1
    assert str(pending[0].destination_id) == failed_destination["id"]
    assert await _event_status(db_session, event_id) == "delivering"

    all_targets = client.post(
        f"/events/{event_id}/replay",
        json={},
        headers={**admin_headers, "Idempotency-Key": "event-replay-2"},
    )
    assert all_targets.status_code == 200
    assert all_targets.json()["deliveries_created"] == 2

    db_session.expire_all()
    assert len(await _deliveries(db_session, event_id)) == 5


async def test_replay_without_destinations_conflicts(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    source = client.post(
        "/admin/sources",
        json={"slug": "no-dest", "name": "No destinations"},
        headers=admin_headers,
    ).json()
    event_id = ingest_event(client, source)
    await publish_outbox(db_session, _noop, now=NOW)

    response = client.post(
        f"/events/{event_id}/replay",
        json={},
        headers={**admin_headers, "Idempotency-Key": "no-dest-1"},
    )

    assert response.status_code == 409


async def test_replay_rejects_unknown_destination(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    source, _ = create_source_with_destination(client, admin_headers)
    event_id = ingest_event(client, source)

    response = client.post(
        f"/events/{event_id}/replay",
        json={"destination_ids": ["00000000-0000-0000-0000-000000000000"]},
        headers={**admin_headers, "Idempotency-Key": "unknown-dest-1"},
    )

    assert response.status_code == 422


async def test_concurrent_replays_with_same_key_create_one_attempt_set(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    cipher: SecretCipher,
) -> None:
    source, _ = create_source_with_destination(client, admin_headers)
    event_id = ingest_event(client, source)
    dead_letter = await _exhaust(db_session, cipher, event_id)
    url = f"/admin/dead-letters/{dead_letter.id}/replay"
    headers = {**admin_headers, "Idempotency-Key": "storm-same"}

    def call(_: int) -> str:
        response = client.post(url, headers=headers)
        assert response.status_code == 200, response.text
        return str(response.json()["replay_id"])

    with ThreadPoolExecutor(max_workers=8) as pool:
        replay_ids = list(pool.map(call, range(8)))

    db_session.expire_all()
    assert len(set(replay_ids)) == 1
    assert len(await _deliveries(db_session, event_id)) == 2
    assert (
        await db_session.scalar(select(Replay.id).where(Replay.idempotency_key == "storm-same"))
        is not None
    )


async def test_concurrent_replays_with_distinct_keys_serialize_attempts(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    cipher: SecretCipher,
) -> None:
    source, _ = create_source_with_destination(client, admin_headers)
    event_id = ingest_event(client, source)
    dead_letter = await _exhaust(db_session, cipher, event_id)
    url = f"/admin/dead-letters/{dead_letter.id}/replay"

    def call(index: int) -> int:
        response = client.post(
            url,
            headers={
                **admin_headers,
                "Idempotency-Key": f"storm-distinct-{index}",
            },
        )
        assert response.status_code == 200, response.text
        return int(response.json()["deliveries_created"])

    with ThreadPoolExecutor(max_workers=8) as pool:
        counts = list(pool.map(call, range(8)))

    assert counts == [1] * 8
    db_session.expire_all()
    deliveries = await _deliveries(db_session, event_id)
    assert len(deliveries) == 9
    assert sorted(delivery.attempt for delivery in deliveries) == list(range(1, 10))
    assert len({delivery.delivery_key for delivery in deliveries}) == 1
