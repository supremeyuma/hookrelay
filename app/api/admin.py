"""Admin authentication and source management."""

import hmac
import logging
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_cipher
from app.api.schemas import (
    DestinationCreate,
    DestinationCreated,
    DestinationSummary,
    DestinationUpdate,
    SourceCreate,
    SourceCreated,
    SourceSecret,
    SourceSummary,
)
from app.core.config import Settings, get_settings
from app.core.crypto import SecretCipher
from app.core.urls import InvalidDestinationUrl, validate_destination_url
from app.db.session import get_session
from app.models.destination import Destination
from app.models.source import Source

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(
    settings: Annotated[Settings, Depends(get_settings)],
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> None:
    """Reject admin requests that do not carry the bootstrap key."""
    if x_admin_key is None or not hmac.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid admin key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


@router.post(
    "/sources",
    response_model=SourceCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a webhook source",
    dependencies=[Depends(require_admin)],
)
async def create_source(
    payload: SourceCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    cipher: Annotated[SecretCipher, Depends(get_cipher)],
) -> SourceCreated:
    plaintext_secret = secrets.token_urlsafe(32)
    source = Source(
        slug=payload.slug,
        name=payload.name,
        secret_encrypted=cipher.encrypt(plaintext_secret),
        rate_limit_per_second=payload.rate_limit_per_second,
        burst=payload.burst,
    )
    session.add(source)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="source slug already exists",
        ) from exc

    await session.refresh(source)
    return SourceCreated(
        id=source.id,
        slug=source.slug,
        name=source.name,
        rate_limit_per_second=source.rate_limit_per_second,
        burst=source.burst,
        created_at=source.created_at,
        secret=plaintext_secret,
    )


@router.get(
    "/sources",
    response_model=list[SourceSummary],
    summary="List sources without secrets",
    dependencies=[Depends(require_admin)],
)
async def list_sources(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SourceSummary]:
    rows = (await session.scalars(select(Source).order_by(Source.created_at))).all()
    return [SourceSummary.model_validate(row) for row in rows]


@router.post(
    "/sources/{slug}/rotate-secret",
    response_model=SourceSecret,
    summary="Rotate a source signing secret",
    dependencies=[Depends(require_admin)],
)
async def rotate_secret(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    cipher: Annotated[SecretCipher, Depends(get_cipher)],
) -> SourceSecret:
    source = await session.scalar(select(Source).where(Source.slug == slug))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source not found")

    plaintext_secret = secrets.token_urlsafe(32)
    source.secret_encrypted = cipher.encrypt(plaintext_secret)
    await session.commit()
    logger.info("source_secret_rotated", extra={"source": source.slug})
    return SourceSecret(slug=source.slug, secret=plaintext_secret)


async def _validated_url(url: str, settings: Settings) -> str:
    try:
        return await validate_destination_url(
            url, allow_private=settings.allow_private_destinations
        )
    except InvalidDestinationUrl as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.post(
    "/sources/{slug}/destinations",
    response_model=DestinationCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a destination for a source",
    dependencies=[Depends(require_admin)],
)
async def create_destination(
    slug: str,
    payload: DestinationCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    cipher: Annotated[SecretCipher, Depends(get_cipher)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DestinationCreated:
    source = await session.scalar(select(Source).where(Source.slug == slug))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source not found")

    url = await _validated_url(payload.url, settings)
    plaintext_secret = payload.secret or secrets.token_urlsafe(32)
    destination = Destination(
        source_id=source.id,
        url=url,
        secret_encrypted=cipher.encrypt(plaintext_secret),
        active=payload.active,
    )
    session.add(destination)
    await session.commit()
    await session.refresh(destination)

    return DestinationCreated(
        id=destination.id,
        source_id=destination.source_id,
        url=destination.url,
        active=destination.active,
        created_at=destination.created_at,
        secret=plaintext_secret,
    )


@router.get(
    "/sources/{slug}/destinations",
    response_model=list[DestinationSummary],
    summary="List destinations without secrets",
    dependencies=[Depends(require_admin)],
)
async def list_destinations(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[DestinationSummary]:
    source = await session.scalar(select(Source).where(Source.slug == slug))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source not found")

    rows = (
        await session.scalars(
            select(Destination)
            .where(Destination.source_id == source.id)
            .order_by(Destination.created_at)
        )
    ).all()
    return [DestinationSummary.model_validate(row) for row in rows]


@router.patch(
    "/destinations/{destination_id}",
    response_model=DestinationSummary,
    summary="Update a destination URL or active flag",
    dependencies=[Depends(require_admin)],
)
async def update_destination(
    destination_id: UUID,
    payload: DestinationUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DestinationSummary:
    destination = await session.get(Destination, destination_id)
    if destination is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="destination not found")

    if payload.url is not None:
        destination.url = await _validated_url(payload.url, settings)
    if payload.active is not None:
        destination.active = payload.active

    await session.commit()
    await session.refresh(destination)
    return DestinationSummary.model_validate(destination)
