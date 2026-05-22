"""Admin-only routes — audit log and user invite."""

from __future__ import annotations

from typing import Annotated

from dependencies import get_db, get_secrets, require_admin
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import AuditLogOut, InviteRequest, LoginResponse, UserOut
from app.infra.vault import VaultSecrets
from app.repositories import audit_repo
from app.services import auth_service

router = APIRouter(prefix="/admin", tags=["admin"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
AdminDep = Annotated[UserOut, Depends(require_admin)]
SecretsDep = Annotated[VaultSecrets, Depends(get_secrets)]


@router.get("/audit-log", response_model=list[AuditLogOut])
async def get_audit_log(
    db: DbDep,
    _admin: AdminDep,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[AuditLogOut]:
    entries = await audit_repo.list_all(db, limit=limit, offset=offset)
    return [AuditLogOut.model_validate(e) for e in entries]


@router.post("/invite", response_model=LoginResponse, status_code=201)
async def invite_user(
    req: InviteRequest,
    db: DbDep,
    _admin: AdminDep,
    secrets: SecretsDep,
) -> LoginResponse:
    return await auth_service.invite(db, req, secrets.jwt_signing_key, actor_id=_admin.id)
