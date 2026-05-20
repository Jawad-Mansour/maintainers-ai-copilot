"""Auth routes — register, login, me.

Routes do HTTP only: parse request, call service, return response.
No SQLAlchemy, no Redis, no external calls here.
"""

from __future__ import annotations

from typing import Annotated

from dependencies import get_current_user, get_db, get_secrets
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import LoginRequest, LoginResponse, RegisterRequest, UserOut
from app.infra.vault import VaultSecrets
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
SecretsDep = Annotated[VaultSecrets, Depends(get_secrets)]
CurrentUserDep = Annotated[UserOut, Depends(get_current_user)]


@router.post("/register", response_model=LoginResponse, status_code=201)
async def register(
    req: RegisterRequest,
    db: DbDep,
    secrets: SecretsDep,
) -> LoginResponse:
    return await auth_service.register(db, req, secrets.jwt_signing_key)


@router.post("/login", response_model=LoginResponse)
async def login(
    req: LoginRequest,
    db: DbDep,
    secrets: SecretsDep,
) -> LoginResponse:
    return await auth_service.login(db, req, secrets.jwt_signing_key)


@router.get("/me", response_model=UserOut)
async def me(current_user: CurrentUserDep) -> UserOut:
    return current_user
