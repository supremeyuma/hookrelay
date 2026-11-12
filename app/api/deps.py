"""Shared request dependencies."""

from typing import cast

from fastapi import Request
from redis.asyncio import Redis

from app.core.crypto import SecretCipher


def get_redis(request: Request) -> Redis:  # type: ignore[type-arg]
    """Return the process-wide async Redis client."""
    return cast(Redis, request.app.state.redis)  # type: ignore[type-arg]


def get_cipher(request: Request) -> SecretCipher:
    """Return the process-wide secret cipher built from the Fernet key."""
    return cast(SecretCipher, request.app.state.cipher)
