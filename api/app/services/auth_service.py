"""Auth service — registration, login, current-user lookup.

Password hashing: bcrypt via passlib.
JWT signing: HS256, key from Vault (passed in, never read here directly).
"""

from __future__ import annotations

import uuid

from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import LoginRequest, LoginResponse, RegisterRequest, UserOut
from app.exceptions import AuthenticationError, ConflictError
from app.infra.jwt_handler import create_access_token
from app.repositories import user_repo

_pwd: CryptContext = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def register(db: AsyncSession, req: RegisterRequest, signing_key: str) -> LoginResponse:
    existing = await user_repo.get_by_email(db, req.email)
    if existing:
        raise ConflictError("Email already registered")
    hashed = _pwd.hash(req.password)
    user = await user_repo.create(db, req.email, hashed)
    await db.commit()
    await db.refresh(user)
    token = create_access_token(str(user.id), user.role, signing_key)
    return LoginResponse(access_token=token)


async def login(db: AsyncSession, req: LoginRequest, signing_key: str) -> LoginResponse:
    user = await user_repo.get_by_email(db, req.email)
    if not user or not _pwd.verify(req.password, user.hashed_password):
        raise AuthenticationError("Invalid email or password")
    if not user.is_active:
        raise AuthenticationError("Account is disabled")
    token = create_access_token(str(user.id), user.role, signing_key)
    return LoginResponse(access_token=token)


async def get_me(db: AsyncSession, user_id: str) -> UserOut:
    user = await user_repo.get_by_id(db, uuid.UUID(user_id))
    if not user:
        raise AuthenticationError("User not found")
    return UserOut.model_validate(user)
