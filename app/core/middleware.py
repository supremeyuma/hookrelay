"""Request correlation id middleware and access logging."""

import logging
import time
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import request_id_ctx
from app.core.metrics import INGEST_DURATION, INGEST_TOTAL

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Bind a request id for the lifetime of the request and echo it back."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        token = request_id_ctx.set(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed",
                extra={"method": request.method, "path": request.url.path},
            )
            raise
        else:
            duration = time.perf_counter() - started
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request_completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round(duration * 1000, 2),
                },
            )
            if request.url.path.startswith("/ingest/"):
                if response.status_code == 202:
                    result = "accepted"
                elif response.status_code == 200:
                    result = "duplicate"
                else:
                    result = "rejected"
                INGEST_TOTAL.labels(result=result).inc()
                INGEST_DURATION.observe(duration)
            return response
        finally:
            request_id_ctx.reset(token)
