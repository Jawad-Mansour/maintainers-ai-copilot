"""FastAPI dependency injection.

All dependencies are defined here and imported by route handlers.
Routes never instantiate clients directly.
"""

from functools import lru_cache

from config import Settings, get_settings
from fastapi import Request

from app.infra.vault import VaultSecrets


@lru_cache
def get_cached_settings() -> Settings:
    return get_settings()


def get_secrets(request: Request) -> VaultSecrets:
    return request.app.state.secrets  # type: ignore[no-any-return]


# Placeholders — fully implemented in Phase 2
def get_db() -> None:
    raise NotImplementedError("DB session — implemented in Phase 2")


def get_redis() -> None:
    raise NotImplementedError("Redis client — implemented in Phase 2")
