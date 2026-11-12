"""Print an X-HookRelay-Signature header value for a payload.

Usage:
    python scripts/sign.py <secret> '<json body>' [--timestamp <unix seconds>]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.security import compute_signature


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("secret", help="Source signing secret")
    parser.add_argument("body", help="Raw request body")
    parser.add_argument("--timestamp", type=int, default=None)
    args = parser.parse_args()

    timestamp = int(time.time()) if args.timestamp is None else args.timestamp
    digest = compute_signature(args.secret, timestamp, args.body.encode())
    print(f"t={timestamp},v1={digest}")


if __name__ == "__main__":
    main()
