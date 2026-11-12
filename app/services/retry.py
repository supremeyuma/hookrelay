"""Retry policy: classification, backoff with jitter, and Retry-After parsing."""

import random
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum

RETRYABLE_STATUSES = {408, 425, 429}
BASE_DELAY_SECONDS = 2.0
BACKOFF_FACTOR = 2.0
MAX_DELAY_SECONDS = 300.0
MAX_RETRY_AFTER_SECONDS = 3600.0
JITTER_MIN = 0.8
JITTER_MAX = 1.2


class Outcome(StrEnum):
    SUCCESS = "success"
    RETRYABLE = "retryable"
    PERMANENT = "permanent"


def classify_status(status_code: int) -> Outcome:
    """Map an HTTP status to delivery behaviour."""
    if 200 <= status_code < 300:
        return Outcome.SUCCESS
    if status_code in RETRYABLE_STATUSES or 500 <= status_code < 600:
        return Outcome.RETRYABLE
    return Outcome.PERMANENT


def error_class_for_status(status_code: int) -> str:
    return f"http_{status_code}"


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Parse Retry-After as seconds or an HTTP date; return seconds from now."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None

    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        current = now or datetime.now(UTC)
        return max(0.0, (retry_at - current).total_seconds())

    return max(0.0, seconds)


def compute_delay(
    attempt: int,
    *,
    retry_after: float | None = None,
    rng: Callable[[], float] = random.random,
    base: float = BASE_DELAY_SECONDS,
    factor: float = BACKOFF_FACTOR,
    cap: float = MAX_DELAY_SECONDS,
) -> float:
    """Exponential backoff for the next attempt, with +/-20% jitter.

    ``attempt`` is the attempt that just failed (1-based).
    """
    if attempt < 1:
        msg = "attempt must be >= 1"
        raise ValueError(msg)

    capped = min(cap, base * factor ** (attempt - 1))
    jittered = capped * (JITTER_MIN + (JITTER_MAX - JITTER_MIN) * rng())

    if retry_after is not None:
        jittered = max(jittered, min(retry_after, MAX_RETRY_AFTER_SECONDS))

    return jittered
