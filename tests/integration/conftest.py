"""Integration fixtures: migrate the schema and reset state between tests."""

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis

from tests.integration.helpers import asyncpg_dsn

ROOT = Path(__file__).resolve().parents[2]


async def _truncate() -> None:
    connection = await asyncpg.connect(asyncpg_dsn())
    try:
        await connection.execute("TRUNCATE TABLE outbox, events, sources RESTART IDENTITY CASCADE")
    finally:
        await connection.close()


async def _flush_redis() -> None:
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    try:
        await redis.flushdb()
    finally:
        await redis.aclose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """Apply migrations once per integration session."""
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


@pytest.fixture(autouse=True)
def clean_state(migrated_database: None) -> Iterator[None]:
    asyncio.run(_truncate())
    asyncio.run(_flush_redis())
    yield


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"X-Admin-Key": os.environ["ADMIN_API_KEY"]}
