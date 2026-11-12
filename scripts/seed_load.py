"""Create a source (and optionally a destination) for load testing.

Usage:
    python scripts/seed_load.py [--rate 2000] [--destination-url URL]

Prints JSON with the slug and signing secret; no secrets are written to disk.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.core.config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--rate", type=int, default=2000)
    parser.add_argument("--destination-url", default=None)
    args = parser.parse_args()

    settings = get_settings()
    headers = {"X-API-Key": settings.admin_api_key}
    slug = f"load-seed-{int(time.time())}"

    with httpx.Client(timeout=10.0) as client:
        source = client.post(
            f"{args.base_url}/admin/sources",
            headers=headers,
            json={
                "slug": slug,
                "name": "Load seed",
                "rate_limit_per_second": args.rate,
                "burst": args.rate,
            },
        )
        source.raise_for_status()
        result = {
            "slug": slug,
            "secret": source.json()["secret"],
            "destination": None,
        }

        if args.destination_url:
            destination = client.post(
                f"{args.base_url}/admin/sources/{slug}/destinations",
                headers=headers,
                json={"url": args.destination_url},
            )
            destination.raise_for_status()
            result["destination"] = destination.json()["url"]

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
