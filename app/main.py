"""FastAPI application factory and lifespan wiring."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    generate_latest,
)
from prometheus_client.multiprocess import MultiProcessCollector
from prometheus_fastapi_instrumentator import Instrumentator
from redis.asyncio import Redis
from sqlalchemy import text

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
from app.core.metrics import EVENTS_BY_STATUS, OUTBOX_LAG
from app.core.middleware import RequestIDMiddleware
from app.core.tracing import configure_tracing
from app.db.session import Database
from app.models.event import EVENT_STATUSES

logger = logging.getLogger(__name__)

API_SUMMARY = (
    "Idempotent webhook ingestion and delivery gateway with retries, dead letters, and replay."
)


def _metrics_response() -> Response:
    """Serve aggregated metrics, including all uvicorn workers when enabled."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        return Response(
            content=generate_latest(registry),
            media_type=CONTENT_TYPE_LATEST,
        )
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def refresh_runtime_metrics(database: Database, interval: float) -> None:
    """Refresh DB-backed gauges so the API's /metrics reflects shared state."""
    while True:
        try:
            async with database.session_factory() as session:
                lag = await session.scalar(
                    text(
                        "SELECT COALESCE("
                        "EXTRACT(EPOCH FROM now() - min(created_at)), 0"
                        ") FROM outbox WHERE published_at IS NULL"
                    )
                )
                OUTBOX_LAG.set(float(lag or 0.0))

                rows = (
                    await session.execute(
                        text("SELECT status, count(*) FROM events GROUP BY status")
                    )
                ).all()
                counts = {str(status): int(count) for status, count in rows}
                for status in EVENT_STATUSES:
                    EVENTS_BY_STATUS.labels(status=status).set(counts.get(status, 0))
        except Exception as exc:
            logger.warning(
                "metrics_refresh_failed",
                extra={"error": type(exc).__name__},
            )
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open shared resources once per process and close them on shutdown."""
    settings = get_settings()
    app.state.db = Database(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.cipher = SecretCipher(settings.secret_encryption_key)

    SQLAlchemyInstrumentor().instrument(engine=app.state.db.engine.sync_engine)
    metrics_task = asyncio.create_task(
        refresh_runtime_metrics(app.state.db, settings.metrics_refresh_seconds)
    )

    try:
        yield
    finally:
        metrics_task.cancel()
        with suppress(asyncio.CancelledError):
            await metrics_task
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

    provider = configure_tracing(
        service_name=settings.otel_service_name,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
        console=settings.otel_console_exporter,
    )
    FastAPIInstrumentor.instrument_app(
        application,
        tracer_provider=provider,
        excluded_urls="healthz,readyz,metrics",
    )

    application.add_middleware(RequestIDMiddleware)
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(admin_router)
    application.include_router(ingest_router)
    application.include_router(events_router)
    application.include_router(replay_router)

    Instrumentator(
        excluded_handlers=["/healthz", "/readyz", "/metrics"],
    ).instrument(application)
    application.add_api_route(
        "/metrics",
        _metrics_response,
        methods=["GET"],
        include_in_schema=False,
    )
    return application


app = create_app()
