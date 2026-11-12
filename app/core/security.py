"""HMAC signature parsing and verification for provider webhooks.

Wire format::

    X-HookRelay-Signature: t=<unix-seconds>,v1=<hex hmac-sha256>

The signed payload is ``f"{t}.{raw_body}"``. Signatures outside the configured tolerance
window are rejected, which bounds replay of a captured request.
"""

import hashlib
import hmac
import re
import time
from dataclasses import dataclass

_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SignatureError(ValueError):
    """Raised when a signature header is malformed, stale, or does not match."""


@dataclass(frozen=True)
class SignatureParts:
    timestamp: int
    digest: str


def parse_signature_header(header: str) -> SignatureParts:
    """Parse ``t=...,v1=...`` into typed parts, rejecting malformed input."""
    fields: dict[str, str] = {}
    for segment in header.split(","):
        key, separator, value = segment.strip().partition("=")
        if not separator or not key or not value:
            msg = "malformed signature header"
            raise SignatureError(msg)
        fields[key] = value

    timestamp_raw = fields.get("t")
    digest = fields.get("v1")
    if timestamp_raw is None or digest is None:
        msg = "signature header requires t and v1"
        raise SignatureError(msg)

    try:
        timestamp = int(timestamp_raw)
    except ValueError as exc:
        msg = "signature timestamp must be an integer"
        raise SignatureError(msg) from exc

    if not _HEX_SHA256.fullmatch(digest):
        msg = "signature digest must be lowercase hex sha256"
        raise SignatureError(msg)

    return SignatureParts(timestamp=timestamp, digest=digest)


def compute_signature(secret: str, timestamp: int, body: bytes) -> str:
    """Return the hex digest HookRelay expects for the given secret and body."""
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_signature(
    *,
    secret: str,
    header: str,
    body: bytes,
    tolerance_seconds: int,
    now: float | None = None,
) -> SignatureParts:
    """Verify the header, raising :class:`SignatureError` on any failure."""
    parts = parse_signature_header(header)
    current = time.time() if now is None else now

    if abs(current - parts.timestamp) > tolerance_seconds:
        msg = "signature timestamp outside tolerance"
        raise SignatureError(msg)

    expected = compute_signature(secret, parts.timestamp, body)
    if not hmac.compare_digest(expected, parts.digest):
        msg = "signature mismatch"
        raise SignatureError(msg)

    return parts
