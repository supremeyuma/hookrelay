"""API-key authentication with scoped authorization."""

import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api_keys import ALL_SCOPES, Scope, hash_api_key
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.models.api_key import ApiKey

LAST_USED_RESOLUTION = timedelta(minutes=1)


@dataclass(frozen=True)
class ApiKeyIdentity:
    id: UUID | None
    name: str
    scopes: frozenset[str]

    def has(self, scope: str) -> bool:
        return scope in self.scopes


def require_scope(scope: str) -> Callable[..., Awaitable[ApiKeyIdentity]]:
    """Build a dependency that authenticates a key and checks one scope."""

    async def dependency(
        session: Annotated[AsyncSession, Depends(get_session)],
        settings: Annotated[Settings, Depends(get_settings)],
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> ApiKeyIdentity:
        if x_api_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        if hmac.compare_digest(x_api_key, settings.admin_api_key):
            return ApiKeyIdentity(
                id=None,
                name="bootstrap",
                scopes=ALL_SCOPES,
            )

        key_hash = hash_api_key(x_api_key)
        api_key = await session.scalar(
            select(ApiKey).where(
                ApiKey.key_hash == key_hash,
                ApiKey.active.is_(True),
            )
        )
        if api_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        if scope not in api_key.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"missing scope: {scope}",
            )

        now = datetime.now(UTC)
        if api_key.last_used_at is None or now - api_key.last_used_at >= LAST_USED_RESOLUTION:
            api_key.last_used_at = now
            await session.commit()

        return ApiKeyIdentity(
            id=api_key.id,
            name=api_key.name,
            scopes=frozenset(api_key.scopes),
        )

    return dependency


require_admin = require_scope(Scope.ADMIN_WRITE.value)
require_events_read = require_scope(Scope.EVENTS_READ.value)
require_replay = require_scope(Scope.REPLAY_WRITE.value)
