"""HMAC header parsing and verification."""

import pytest

from app.core.security import (
    SignatureError,
    compute_signature,
    parse_signature_header,
    verify_signature,
)

SECRET = "test-secret"
BODY = b'{"type":"charge.succeeded"}'


def _header(timestamp: int, secret: str = SECRET, body: bytes = BODY) -> str:
    return f"t={timestamp},v1={compute_signature(secret, timestamp, body)}"


def test_valid_signature_is_accepted() -> None:
    parts = verify_signature(
        secret=SECRET,
        header=_header(1_000),
        body=BODY,
        tolerance_seconds=300,
        now=1_000,
    )

    assert parts.timestamp == 1_000


def test_wrong_secret_is_rejected() -> None:
    with pytest.raises(SignatureError, match="mismatch"):
        verify_signature(
            secret=SECRET,
            header=_header(1_000, secret="other"),
            body=BODY,
            tolerance_seconds=300,
            now=1_000,
        )


def test_tampered_body_is_rejected() -> None:
    with pytest.raises(SignatureError, match="mismatch"):
        verify_signature(
            secret=SECRET,
            header=_header(1_000),
            body=BODY + b" ",
            tolerance_seconds=300,
            now=1_000,
        )


def test_stale_timestamp_is_rejected() -> None:
    with pytest.raises(SignatureError, match="tolerance"):
        verify_signature(
            secret=SECRET,
            header=_header(600),
            body=BODY,
            tolerance_seconds=300,
            now=1_000,
        )


def test_future_timestamp_is_rejected() -> None:
    with pytest.raises(SignatureError, match="tolerance"):
        verify_signature(
            secret=SECRET,
            header=_header(1_400),
            body=BODY,
            tolerance_seconds=300,
            now=1_000,
        )


def test_tolerance_boundary_is_inclusive() -> None:
    verify_signature(
        secret=SECRET,
        header=_header(700),
        body=BODY,
        tolerance_seconds=300,
        now=1_000,
    )


@pytest.mark.parametrize(
    "header",
    [
        "",
        "t=1000",
        "v1=" + "a" * 64,
        "t=abc,v1=" + "a" * 64,
        "t=1000,v1=not-hex",
        "t=1000,v1=" + "A" * 64,
        "nonsense",
    ],
)
def test_malformed_headers_are_rejected(header: str) -> None:
    with pytest.raises(SignatureError):
        parse_signature_header(header)


def test_compute_signature_is_stable() -> None:
    assert compute_signature(SECRET, 1_000, BODY) == compute_signature(SECRET, 1_000, BODY)
