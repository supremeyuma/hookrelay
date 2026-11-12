"""ARQ job functions."""

import logging
from typing import Any
from uuid import UUID

from app.services.delivery import deliver_once

logger = logging.getLogger(__name__)


async def deliver(ctx: dict[str, Any], delivery_id: str) -> None:
    """Execute one delivery attempt. Unexpected failures leave the row pending."""
    try:
        async with ctx["db"].session_factory() as session:
            result = await deliver_once(
                session,
                ctx["http"],
                UUID(delivery_id),
                ctx["cipher"],
                max_attempts=ctx["max_attempts"],
            )
    except Exception:
        logger.exception("delivery_job_failed", extra={"delivery_id": delivery_id})
        return

    if result is not None:
        logger.info(
            "delivery_attempt",
            extra={
                "delivery_id": str(result.delivery_id),
                "event_id": str(result.event_id),
                "destination_id": str(result.destination_id),
                "attempt": result.attempt,
                "status_code": result.response_code,
                "error": result.error_class,
            },
        )
