"""Admin authentication and source management."""

import logging
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_cipher
from app.api.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeySummary,
    DestinationCreate,
    DestinationCreated,
    DestinationSummary,
    DestinationUpdate,
    SourceCreate,
    SourceCreated,
    SourceSecret,
    SourceSummary,
)
from app.core.api_keys import Scope, generate_api_key
from app.core.auth import require_admin
from app.core.config import Settings, get_settings
from app.core.crypto import SecretCipher
from app.core.urls import InvalidDestinationUrl, validate_destination_url
from app.db.session import get_session
from app.models.api_key import ApiKey
from app.models.destination import Destination
from app.models.source import Source

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _scope_values(scopes: list[Scope]) -> list[str]:
    return list(dict.fromkeys(scope.value for scope in scopes))


@router.post(
    "/sources",
    response_model=SourceCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a webhook source",
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


@router.post(
    "/api-keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a scoped API key",
)
async def create_api_key(
    payload: ApiKeyCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApiKeyCreated:
    generated = generate_api_key()
    api_key = ApiKey(
        name=payload.name,
        key_hash=generated.key_hash,
        key_prefix=generated.key_prefix,
        scopes=_scope_values(payload.scopes),
        active=True,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    return ApiKeyCreated(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        scopes=api_key.scopes,
        active=api_key.active,
        last_used_at=api_key.last_used_at,
        created_at=api_key.created_at,
        key=generated.raw,
    )


@router.get(
    "/api-keys",
    response_model=list[ApiKeySummary],
    summary="List API keys without their secrets",
)
async def list_api_keys(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ApiKeySummary]:
    rows = (await session.scalars(select(ApiKey).order_by(ApiKey.created_at.desc()))).all()
    return [ApiKeySummary.model_validate(row) for row in rows]


@router.delete(
    "/api-keys/{api_key_id}",
    response_model=ApiKeySummary,
    summary="Revoke an API key",
)
async def revoke_api_key(
    api_key_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApiKeySummary:
    api_key = await session.get(ApiKey, api_key_id)
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="api key not found")

    api_key.active = False
    await session.commit()
    await session.refresh(api_key)
    logger.info("api_key_revoked", extra={"source": api_key.name})
    return ApiKeySummary.model_validate(api_key)
