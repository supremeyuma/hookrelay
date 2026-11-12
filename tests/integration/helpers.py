"""Small helpers shared by integration tests."""

import asyncio
import os

import asyncpg


def asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def _scalar(query: str, *args: object) -> object:
    connection = await asyncpg.connect(asyncpg_dsn())
    try:
        return await connection.fetchval(query, *args)
    finally:
        await connection.close()


def db_scalar(query: str, *args: object) -> object:
    """Run a single-value query against the test database."""
    return asyncio.run(_scalar(query, *args))
