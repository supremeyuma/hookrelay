"""FastAPI application factory and lifespan wiring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app import __version__
from app.api.admin import router as admin_router
from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.ingest import router as ingest_router
from app.api.replay import router as replay_router
from app.core.config import get_settings
from app.core.crypto import SecretCipher
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIDMiddleware
from app.db.session import Database

API_SUMMARY = (
    "Idempotent webhook ingestion and delivery gateway with retries, dead letters, and replay."
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open shared resources once per process and close them on shutdown."""
    settings = get_settings()
    app.state.db = Database(settings.database_url)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.cipher = SecretCipher(settings.secret_encryption_key)

    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.db.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    application = FastAPI(
        title="HookRelay",
        summary=API_SUMMARY,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    application.add_middleware(RequestIDMiddleware)
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(admin_router)
    application.include_router(ingest_router)
    application.include_router(events_router)
    application.include_router(replay_router)
    return application


app = create_app()
