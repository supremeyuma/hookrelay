"""ARQ worker configuration."""

import logging
from typing import Any, ClassVar

import httpx
from arq.connections import RedisSettings
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from prometheus_client import start_http_server
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.crypto import SecretCipher
from app.core.logging import configure_logging
from app.core.tracing import configure_tracing
from app.db.session import Database
from app.workers.tasks import deliver

logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(
        service_name=f"{settings.otel_service_name}-worker",
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
        console=settings.otel_console_exporter,
    )
    ctx["db"] = Database(settings.database_url)
    SQLAlchemyInstrumentor().instrument(engine=ctx["db"].engine.sync_engine)
    ctx["cipher"] = SecretCipher(settings.secret_encryption_key)
    ctx["redis"] = Redis.from_url(settings.redis_url, decode_responses=True)
    ctx["http"] = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.delivery_timeout_seconds, connect=3.0),
        follow_redirects=False,
    )
    ctx["max_attempts"] = settings.delivery_max_attempts
    try:
        start_http_server(settings.worker_metrics_port)
    except OSError:
        logger.warning(
            "worker_metrics_server_failed",
            extra={"error": "port_in_use"},
        )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http"].aclose()
    await ctx["redis"].aclose()
    await ctx["db"].dispose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [deliver]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 20
    job_timeout = 120
    keep_result = 3600
    max_tries = 1
