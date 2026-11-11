"""Liveness and readiness endpoints."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_redis
from app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

READINESS_TIMEOUT_SECONDS = 1.0


async def database_ready(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> bool:
    try:
        async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("readiness_db_failed", extra={"error": type(exc).__name__})
        return False
    return True


async def redis_ready(
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> bool:
    try:
        async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
            await redis.ping()
    except Exception as exc:
        logger.warning("readiness_redis_failed", extra={"error": type(exc).__name__})
        return False
    return True


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe (Postgres + Redis)")
async def readyz(
    response: Response,
    db_ok: Annotated[bool, Depends(database_ready)],
    redis_ok: Annotated[bool, Depends(redis_ready)],
) -> dict[str, str]:
    ready = db_ok and redis_ok
    response.status_code = 200 if ready else 503
    return {
        "status": "ok" if ready else "degraded",
        "db": "ok" if db_ok else "fail",
        "redis": "ok" if redis_ok else "fail",
    }
