"""End-to-end replay smoke check: ingest, deliver, replay, idempotency, scopes.

Prereqs: compose stack running, migrations applied, local sink on port 9099:

    uv run uvicorn tests.fixtures.sink:app --port 9099

Usage:
    python scripts/smoke_replay.py [api_base] [destination_url] [sink_base]
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.core.config import get_settings
from app.core.security import compute_signature

BODY = b'{"type":"charge.succeeded","amount":4200}'


def _wait_for_status(
    client: httpx.Client, base: str, event_id: str, headers: dict[str, str]
) -> str:
    deadline = time.time() + 30
    status = "pending"
    while time.time() < deadline:
        response = client.get(f"{base}/events/{event_id}", headers=headers)
        response.raise_for_status()
        status = str(response.json()["status"])
        if status in {"delivered", "partial", "dead"}:
            return status
        time.sleep(0.5)
    return status


def main() -> int:
    api_base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    destination_url = sys.argv[2] if len(sys.argv) > 2 else "http://host.docker.internal:9099/hook"
    sink_base = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:9099"
    settings = get_settings()
    admin = {"X-API-Key": settings.admin_api_key}
    slug = f"replay-smoke-{int(time.time())}"

    with httpx.Client(timeout=10.0) as client:
        client.delete(f"{sink_base}/requests")

        source = client.post(
            f"{api_base}/admin/sources",
            headers=admin,
            json={"slug": slug, "name": "Replay smoke"},
        )
        source.raise_for_status()
        source_secret = source.json()["secret"]

        destination = client.post(
            f"{api_base}/admin/sources/{slug}/destinations",
            headers=admin,
            json={"url": destination_url},
        )
        destination.raise_for_status()

        timestamp = int(time.time())
        ingest = client.post(
            f"{api_base}/ingest/{slug}",
            content=BODY,
            headers={
                "Content-Type": "application/json",
                "X-HookRelay-Signature": (
                    f"t={timestamp},v1={compute_signature(source_secret, timestamp, BODY)}"
                ),
                "X-Idempotency-Key": "evt_replay_1",
            },
        )
        ingest.raise_for_status()
        event_id = ingest.json()["event_id"]
        print(f"event accepted: {event_id}")

        status = _wait_for_status(client, api_base, event_id, admin)
        print(f"initial delivery: {status}")
        if status != "delivered":
            print("FAIL: initial delivery did not succeed")
            return 1

        missing_key = client.post(f"{api_base}/events/{event_id}/replay", json={}, headers=admin)
        if missing_key.status_code != 422:
            print(f"FAIL: replay without Idempotency-Key returned {missing_key.status_code}")
            return 1

        replay_headers = {**admin, "Idempotency-Key": "smoke-replay-1"}
        replay = client.post(
            f"{api_base}/events/{event_id}/replay", json={}, headers=replay_headers
        )
        replay.raise_for_status()
        replay_body = replay.json()
        print(f"replay: {replay_body}")
        if replay_body["deliveries_created"] != 1:
            print("FAIL: expected one replayed delivery")
            return 1

        status = _wait_for_status(client, api_base, event_id, admin)
        if status != "delivered":
            print(f"FAIL: replayed delivery ended as {status}")
            return 1

        sink_requests = client.get(f"{sink_base}/requests")
        sink_requests.raise_for_status()
        matching = [
            entry
            for entry in sink_requests.json()
            if entry["headers"].get("x-hookrelay-event-id") == event_id
        ]
        print(f"sink deliveries: {len(matching)}")
        if len(matching) != 2:
            print("FAIL: expected the sink to receive the event twice")
            return 1

        duplicate = client.post(
            f"{api_base}/events/{event_id}/replay", json={}, headers=replay_headers
        )
        duplicate.raise_for_status()
        if duplicate.json()["replay_id"] != replay_body["replay_id"]:
            print("FAIL: duplicate replay created a new audit record")
            return 1
        if len(client.get(f"{sink_base}/requests").json()) != 2:
            print("FAIL: duplicate replay sent another delivery")
            return 1

        read_key_response = client.post(
            f"{api_base}/admin/api-keys",
            headers=admin,
            json={"name": "smoke-read", "scopes": ["events:read"]},
        )
        read_key_response.raise_for_status()
        read_key = read_key_response.json()["key"]

        if (
            client.get(f"{api_base}/events/{event_id}", headers={"X-API-Key": read_key}).status_code
            != 200
        ):
            print("FAIL: events:read key could not read the event")
            return 1

        denied = client.post(
            f"{api_base}/events/{event_id}/replay",
            json={},
            headers={"X-API-Key": read_key, "Idempotency-Key": "scope-denied"},
        )
        print(f"events:read replay attempt: {denied.status_code}")
        if denied.status_code != 403:
            print("FAIL: events:read key was allowed to replay")
            return 1

    print("REPLAY SMOKE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
