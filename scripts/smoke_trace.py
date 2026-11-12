"""Create a traced delivery and print the trace id for Jaeger.

Prereqs: compose stack (with Jaeger profile) and the local sink running.

Usage:
    python scripts/smoke_trace.py [trace_id_hex32]
"""

import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.core.config import get_settings
from app.core.security import compute_signature

BODY = b'{"type":"trace.smoke","amount":1}'


def main() -> int:
    trace_id = sys.argv[1] if len(sys.argv) > 1 else secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    traceparent = f"00-{trace_id}-{span_id}-01"
    api_base = "http://localhost:8000"
    sink_base = "http://127.0.0.1:9099"
    settings = get_settings()
    admin = {"X-API-Key": settings.admin_api_key}
    slug = f"trace-{int(time.time())}"

    with httpx.Client(timeout=10.0) as client:
        client.delete(f"{sink_base}/requests")
        source = client.post(
            f"{api_base}/admin/sources",
            headers=admin,
            json={"slug": slug, "name": "Trace smoke"},
        )
        source.raise_for_status()
        secret = source.json()["secret"]

        destination = client.post(
            f"{api_base}/admin/sources/{slug}/destinations",
            headers=admin,
            json={"url": "http://host.docker.internal:9099/hook"},
        )
        destination.raise_for_status()

        timestamp = int(time.time())
        ingest = client.post(
            f"{api_base}/ingest/{slug}",
            content=BODY,
            headers={
                "Content-Type": "application/json",
                "X-HookRelay-Signature": (
                    f"t={timestamp},v1={compute_signature(secret, timestamp, BODY)}"
                ),
                "X-Idempotency-Key": "trace-evt-1",
                "traceparent": traceparent,
            },
        )
        ingest.raise_for_status()
        event_id = ingest.json()["event_id"]

        deadline = time.time() + 30
        status = "pending"
        while time.time() < deadline:
            detail = client.get(f"{api_base}/events/{event_id}", headers=admin).json()
            status = detail["status"]
            if status in {"delivered", "partial", "dead"}:
                break
            time.sleep(0.5)

        print(f"event: {event_id} status={status}")
        print(f"trace: {trace_id}")
        print(f"jaeger: http://localhost:16686/trace/{trace_id}")
        if status != "delivered":
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
