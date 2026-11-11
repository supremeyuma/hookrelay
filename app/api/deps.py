"""Shared request dependencies."""

from typing import cast

from fastapi import Request
from redis.asyncio import Redis


def get_redis(request: Request) -> Redis:  # type: ignore[type-arg]
    """Return the process-wide async Redis client."""
    return cast(Redis, request.app.state.redis)  # type: ignore[type-arg]
