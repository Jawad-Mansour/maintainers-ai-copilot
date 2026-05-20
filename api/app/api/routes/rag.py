"""RAG routes — ingest and search.

Routes do HTTP only: parse request, call service, return response.
No SQLAlchemy, no MinIO, no OpenAI directly here.
"""

from __future__ import annotations

from typing import Annotated

from dependencies import get_current_user, get_db, get_secrets, require_admin
from fastapi import APIRouter, Depends, Request
from minio import Minio
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import ChunkResult, IngestRequest, IngestResponse, SearchRequest, UserOut
from app.infra.modelserver_client import ModelServerClient
from app.infra.vault import VaultSecrets
from app.services import rag_service

router = APIRouter(prefix="/rag", tags=["rag"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
SecretsDep = Annotated[VaultSecrets, Depends(get_secrets)]
AdminDep = Annotated[UserOut, Depends(require_admin)]
CurrentUserDep = Annotated[UserOut, Depends(get_current_user)]


def _get_minio(request: Request) -> Minio:
    return request.app.state.minio_client  # type: ignore[no-any-return]


def _get_modelserver(request: Request) -> ModelServerClient:
    return request.app.state.modelserver_client  # type: ignore[no-any-return]


MinioDep = Annotated[Minio, Depends(_get_minio)]
ModelServerDep = Annotated[ModelServerClient, Depends(_get_modelserver)]


@router.post("/ingest", response_model=IngestResponse, status_code=201)
async def ingest(
    req: IngestRequest,
    db: DbDep,
    secrets: SecretsDep,
    _admin: AdminDep,
) -> IngestResponse:
    return await rag_service.ingest(db, req, secrets.openai_api_key)


@router.post("/search", response_model=list[ChunkResult])
async def search(
    req: SearchRequest,
    db: DbDep,
    secrets: SecretsDep,
    minio: MinioDep,
    modelserver: ModelServerDep,
    _user: CurrentUserDep,
) -> list[ChunkResult]:
    return await rag_service.search(db, req, secrets.openai_api_key, minio, modelserver)
