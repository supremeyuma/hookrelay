"""Retry classification, backoff, and Retry-After parsing."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.retry import (
    Outcome,
    classify_status,
    compute_delay,
    parse_retry_after,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, Outcome.SUCCESS),
        (201, Outcome.SUCCESS),
        (204, Outcome.SUCCESS),
        (301, Outcome.PERMANENT),
        (400, Outcome.PERMANENT),
        (404, Outcome.PERMANENT),
        (408, Outcome.RETRYABLE),
        (425, Outcome.RETRYABLE),
        (429, Outcome.RETRYABLE),
        (500, Outcome.RETRYABLE),
        (503, Outcome.RETRYABLE),
    ],
)
def test_classify_status(status: int, expected: Outcome) -> None:
    assert classify_status(status) is expected


def test_backoff_grows_and_caps() -> None:
    first = compute_delay(1, rng=lambda: 0.5)
    third = compute_delay(3, rng=lambda: 0.5)
    capped = compute_delay(20, rng=lambda: 0.5)

    assert first == pytest.approx(2.0)
    assert third == pytest.approx(8.0)
    assert capped == pytest.approx(300.0)


def test_backoff_jitter_bounds() -> None:
    assert compute_delay(1, rng=lambda: 0.0) == pytest.approx(1.6)
    assert compute_delay(1, rng=lambda: 1.0) == pytest.approx(2.4)


def test_retry_after_overrides_when_larger() -> None:
    assert compute_delay(1, retry_after=7, rng=lambda: 0.5) == pytest.approx(7.0)


def test_retry_after_is_capped_at_one_hour() -> None:
    assert compute_delay(1, retry_after=99_999, rng=lambda: 0.5) == pytest.approx(3600.0)


def test_attempt_must_be_positive() -> None:
    with pytest.raises(ValueError):
        compute_delay(0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("7", 7.0),
        ("0", 0.0),
        ("-5", 0.0),
        (None, None),
        ("", None),
        ("garbage", None),
    ],
)
def test_parse_retry_after_seconds(value: str | None, expected: float | None) -> None:
    assert parse_retry_after(value, NOW) == expected


def test_parse_retry_after_http_date() -> None:
    retry_at = NOW + timedelta(seconds=30)
    header = retry_at.strftime("%a, %d %b %Y %H:%M:%S GMT")

    assert parse_retry_after(header, NOW) == pytest.approx(30.0, abs=1.0)
