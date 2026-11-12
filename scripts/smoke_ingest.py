"""End-to-end ingest smoke check against a running API (default localhost:8000).

Creates a uniquely named source, posts a signed event twice, verifies the duplicate and
event detail, then confirms a stale signature is rejected. Exits non-zero on failure.

Usage:
    python scripts/smoke_ingest.py [base_url]
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.core.config import get_settings
from app.core.security import compute_signature

BODY = b'{"type":"charge.succeeded","amount":4200}'


def _signature(secret: str, timestamp: int) -> str:
    return f"t={timestamp},v1={compute_signature(secret, timestamp, BODY)}"


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    settings = get_settings()
    slug = f"smoke-{int(time.time())}"

    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        created = client.post(
            "/admin/sources",
            headers={"X-API-Key": settings.admin_api_key},
            json={"slug": slug, "name": "Smoke test"},
        )
        created.raise_for_status()
        secret = created.json()["secret"]
        print(f"source: {slug} created")

        timestamp = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "X-HookRelay-Signature": _signature(secret, timestamp),
            "X-Idempotency-Key": "evt_001",
        }

        first = client.post(f"/ingest/{slug}", content=BODY, headers=headers)
        second = client.post(f"/ingest/{slug}", content=BODY, headers=headers)
        print(f"first:  {first.status_code} {first.json()}")
        print(f"second: {second.status_code} {second.json()}")

        if first.status_code != 202 or first.json()["status"] != "accepted":
            print("FAIL: first request was not accepted")
            return 1
        if second.status_code != 200 or second.json()["status"] != "duplicate":
            print("FAIL: duplicate was not detected")
            return 1
        if second.json()["event_id"] != first.json()["event_id"]:
            print("FAIL: duplicate returned a different event id")
            return 1

        event_id = first.json()["event_id"]
        detail = client.get(f"/events/{event_id}", headers={"X-API-Key": settings.admin_api_key})
        detail.raise_for_status()
        payload = detail.json()
        print(
            f"event:  status={payload['status']} amount={payload['payload']['amount']} "
            f"signature_stored={'x-hookrelay-signature' in payload['headers']}"
        )
        if payload["status"] != "pending":
            print("FAIL: unexpected event status")
            return 1
        if "x-hookrelay-signature" in payload["headers"]:
            print("FAIL: signature header was persisted")
            return 1

        stale = client.post(
            f"/ingest/{slug}",
            content=BODY,
            headers={
                "Content-Type": "application/json",
                "X-HookRelay-Signature": _signature(secret, timestamp - 301),
                "X-Idempotency-Key": "evt_002",
            },
        )
        print(f"stale:  {stale.status_code} {stale.json()}")
        if stale.status_code != 401:
            print("FAIL: stale signature was not rejected")
            return 1

    print("SMOKE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
