"""Hashed API keys and their scopes."""

import hashlib
import secrets
from dataclasses import dataclass
from enum import StrEnum

KEY_PREFIX = "hr_live_"


class Scope(StrEnum):
    ADMIN_WRITE = "admin:write"
    EVENTS_READ = "events:read"
    REPLAY_WRITE = "replay:write"


ALL_SCOPES = frozenset(scope.value for scope in Scope)


@dataclass(frozen=True)
class GeneratedApiKey:
    raw: str
    key_hash: str
    key_prefix: str


def hash_api_key(raw: str) -> str:
    """Return the SHA-256 hex digest stored for an API key."""
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_api_key() -> GeneratedApiKey:
    raw = f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return GeneratedApiKey(
        raw=raw,
        key_hash=hash_api_key(raw),
        key_prefix=raw[: len(KEY_PREFIX) + 6],
    )
