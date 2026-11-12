"""End-to-end delivery smoke check: API -> outbox -> poller -> ARQ -> sink.

Prereqs: compose stack running (api, worker, poller), migrations applied, and the local sink
running on the host:

    uv run uvicorn tests.fixtures.sink:app --port 9099

Usage:
    python scripts/smoke_delivery.py [api_base] [destination_url] [sink_base]
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.core.config import get_settings
from app.core.security import compute_signature, verify_signature

BODY = b'{"type":"charge.succeeded","amount":4200}'


def main() -> int:
    api_base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    destination_url = sys.argv[2] if len(sys.argv) > 2 else "http://host.docker.internal:9099/hook"
    sink_base = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:9099"
    settings = get_settings()
    slug = f"delivery-smoke-{int(time.time())}"

    with httpx.Client(timeout=10.0) as client:
        client.delete(f"{sink_base}/requests")

        created = client.post(
            f"{api_base}/admin/sources",
            headers={"X-API-Key": settings.admin_api_key},
            json={"slug": slug, "name": "Delivery smoke"},
        )
        created.raise_for_status()
        source_secret = created.json()["secret"]

        destination = client.post(
            f"{api_base}/admin/sources/{slug}/destinations",
            headers={"X-API-Key": settings.admin_api_key},
            json={"url": destination_url},
        )
        destination.raise_for_status()
        destination_secret = destination.json()["secret"]

        timestamp = int(time.time())
        ingest = client.post(
            f"{api_base}/ingest/{slug}",
            content=BODY,
            headers={
                "Content-Type": "application/json",
                "X-HookRelay-Signature": (
                    f"t={timestamp},v1={compute_signature(source_secret, timestamp, BODY)}"
                ),
                "X-Idempotency-Key": "evt_delivery_1",
            },
        )
        ingest.raise_for_status()
        event_id = ingest.json()["event_id"]
        print(f"event accepted: {event_id}")

        deadline = time.time() + 30
        status = "pending"
        detail: dict[str, object] = {}
        while time.time() < deadline:
            response = client.get(
                f"{api_base}/events/{event_id}",
                headers={"X-API-Key": settings.admin_api_key},
            )
            response.raise_for_status()
            detail = response.json()
            status = str(detail["status"])
            if status in {"delivered", "partial", "dead"}:
                break
            time.sleep(0.5)

        print(f"event status: {status}")
        if status != "delivered":
            print(f"FAIL: expected delivered, got {status}")
            print(json.dumps(detail, indent=2))
            return 1

        sink_requests = client.get(f"{sink_base}/requests")
        sink_requests.raise_for_status()
        matching = [
            entry
            for entry in sink_requests.json()
            if entry["headers"].get("x-hookrelay-event-id") == event_id
        ]
        if not matching:
            print("FAIL: sink received no request for the event")
            return 1

        received = matching[0]
        header = received["headers"]["x-hookrelay-signature"]
        body = received["body"].encode()
        verify_signature(
            secret=destination_secret,
            header=header,
            body=body,
            tolerance_seconds=300,
        )
        print(
            "sink: signature valid, "
            f"attempt={received['headers']['x-hookrelay-attempt']} "
            f"delivery_key={received['headers']['x-hookrelay-delivery-key']}"
        )
        attempts = detail["attempts"]
        print(f"attempts recorded: {len(attempts)} -> {[a['status'] for a in attempts]}")

        if len(matching) != 1:
            print(f"FAIL: expected one successful delivery, sink saw {len(matching)}")
            return 1

    print("DELIVERY SMOKE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
