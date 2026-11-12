"""Integration fixtures: migrate the schema and reset state between tests."""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.crypto import SecretCipher
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
    return {"X-API-Key": os.environ["ADMIN_API_KEY"]}


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def cipher() -> SecretCipher:
    return SecretCipher(os.environ["SECRET_ENCRYPTION_KEY"])
