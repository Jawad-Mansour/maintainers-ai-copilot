"""FastAPI dependency injection.

All dependencies are defined here and imported by route handlers.
Routes never instantiate clients directly.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import redis.asyncio as aioredis
from config import Settings, get_settings
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import UserOut
from app.exceptions import AuthenticationError, PermissionDenied
from app.infra.jwt_handler import decode_access_token
from app.infra.vault import VaultSecrets

_bearer = HTTPBearer(auto_error=False)


@lru_cache
def get_cached_settings() -> Settings:
    return get_settings()


def get_secrets(request: Request) -> VaultSecrets:
    return request.app.state.secrets  # type: ignore[no-any-return]


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.session_factory() as session:
        yield session


def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis_client  # type: ignore[no-any-return]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    secrets: Annotated[VaultSecrets, Depends(get_secrets)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserOut:
    if not credentials:
        raise AuthenticationError("Missing Authorization header")
    payload = decode_access_token(credentials.credentials, secrets.jwt_signing_key)
    user_id = payload.get("sub", "")
    if not user_id or not isinstance(user_id, str):
        raise AuthenticationError("Invalid token payload")

    from app.repositories import user_repo

    user = await user_repo.get_by_id(db, UUID(user_id))
    if not user or not user.is_active:
        raise AuthenticationError("User not found or inactive")
    return UserOut.model_validate(user)


def require_admin(user: Annotated[UserOut, Depends(get_current_user)]) -> UserOut:
    if user.role != "admin":
        raise PermissionDenied("Admin access required")
    return user
