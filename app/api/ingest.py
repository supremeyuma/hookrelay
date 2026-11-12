"""Signed, idempotent webhook ingestion."""

import json
import logging
from typing import Annotated, Any

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_cipher, get_redis
from app.api.schemas import IngestResponse
from app.core.body import BodyTooLargeError, read_body_with_limit
from app.core.config import Settings, get_settings
from app.core.crypto import SecretCipher
from app.core.rate_limit import TokenBucket
from app.core.security import SignatureError, verify_signature
from app.db.session import get_session
from app.models.event import Event
from app.models.outbox import Outbox
from app.models.source import Source

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"])

_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "x-admin-key",
    "x-api-key",
    "x-hookrelay-signature",
}


def _stored_headers(request: Request) -> dict[str, str]:
    return {
        name.lower(): value
        for name, value in request.headers.items()
        if name.lower() not in _SENSITIVE_HEADERS
    }


@router.post(
    "/ingest/{source_slug}",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"description": "Duplicate event; already accepted"}},
    summary="Accept a signed provider event",
)
async def ingest(
    source_slug: str,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    cipher: Annotated[SecretCipher, Depends(get_cipher)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> IngestResponse:
    source = await session.scalar(select(Source).where(Source.slug == source_slug))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source not found")

    try:
        raw_body = await read_body_with_limit(request, settings.max_body_bytes)
    except BodyTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="request body too large",
        ) from exc

    signature_header = request.headers.get("X-HookRelay-Signature")
    if signature_header is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid signature",
        )

    try:
        secret = cipher.decrypt(source.secret_encrypted)
    except InvalidToken as exc:
        logger.exception("source_secret_decrypt_failed", extra={"source": source.slug})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="source configuration error",
        ) from exc

    try:
        verify_signature(
            secret=secret,
            header=signature_header,
            body=raw_body,
            tolerance_seconds=settings.signature_tolerance_seconds,
        )
    except SignatureError as exc:
        logger.info(
            "signature_rejected",
            extra={"source": source.slug, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid signature",
        ) from exc

    try:
        payload: Any = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="body must be valid JSON",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="body must be a JSON object",
        )

    idempotency_key = request.headers.get("X-Idempotency-Key")
    if not idempotency_key or len(idempotency_key) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="X-Idempotency-Key is required (max 255 characters)",
        )

    external_id = request.headers.get("X-External-Id")
    if external_id is not None and len(external_id) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="X-External-Id exceeds 255 characters",
        )

    decision = await TokenBucket(redis).consume(
        f"rl:{source.id}",
        rate_per_second=float(source.rate_limit_per_second),
        burst=source.burst,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers={"Retry-After": str(max(1, decision.retry_after_seconds))},
        )

    insert_statement = (
        pg_insert(Event)
        .values(
            source_id=source.id,
            external_id=external_id,
            idempotency_key=idempotency_key,
            payload=payload,
            headers=_stored_headers(request),
            status="pending",
        )
        .on_conflict_do_nothing(index_elements=["source_id", "idempotency_key"])
        .returning(Event.id)
    )
    event_id = (await session.execute(insert_statement)).scalar_one_or_none()

    if event_id is None:
        existing_id = await session.scalar(
            select(Event.id).where(
                Event.source_id == source.id,
                Event.idempotency_key == idempotency_key,
            )
        )
        if existing_id is None:
            logger.error(
                "idempotency_conflict_without_row",
                extra={"source": source.slug},
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="event conflict",
            )

        response.status_code = status.HTTP_200_OK
        response.headers["Location"] = f"/events/{existing_id}"
        logger.info(
            "event_duplicate",
            extra={"source": source.slug, "event_id": str(existing_id)},
        )
        return IngestResponse(status="duplicate", event_id=existing_id)

    session.add(Outbox(event_id=event_id, traceparent=request.headers.get("traceparent")))
    await session.commit()

    response.status_code = status.HTTP_202_ACCEPTED
    response.headers["Location"] = f"/events/{event_id}"
    logger.info(
        "event_accepted",
        extra={"source": source.slug, "event_id": str(event_id)},
    )
    return IngestResponse(status="accepted", event_id=event_id)
