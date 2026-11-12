"""Export the OpenAPI document, or check that the committed copy is current.

Usage:
    python scripts/export_openapi.py           # write docs/openapi.json
    python scripts/export_openapi.py --check   # fail if the file would change
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# App import needs settings; the OpenAPI document does not depend on real values.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault(
    "SECRET_ENCRYPTION_KEY",
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
)
os.environ.setdefault("ADMIN_API_KEY", "openapi-export-key-000000")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from app.main import app

TARGET = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    document = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"

    if args.check:
        if not TARGET.exists():
            print(f"missing {TARGET}")
            return 1
        if TARGET.read_text(encoding="utf-8") != document:
            print("docs/openapi.json is stale; run python scripts/export_openapi.py")
            return 1
        print("openapi.json is current")
        return 0

    TARGET.write_text(document, encoding="utf-8")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
