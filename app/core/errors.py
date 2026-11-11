"""Consistent JSON error envelopes."""

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_request_id

logger = logging.getLogger(__name__)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return a stable 422 body that includes the request id."""
    return JSONResponse(
        status_code=422,
        content={
            "detail": jsonable_encoder(exc.errors()),
            "request_id": get_request_id(),
            "path": request.url.path,
        },
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the exception server-side but never leak its text to the client."""
    logger.exception(
        "unhandled_error",
        extra={"method": request.method, "path": request.url.path},
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "internal server error",
            "request_id": get_request_id(),
            "path": request.url.path,
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach validation and catch-all handlers to the application."""
    app.add_exception_handler(
        RequestValidationError,
        validation_error_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(Exception, unhandled_error_handler)
