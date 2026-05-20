"""Chat route — POST /chat.

Route does HTTP only: parse request, call service, return response.
No SQLAlchemy, no OpenAI, no Langfuse directly here.
"""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as aioredis
from dependencies import get_current_user, get_db, get_redis, get_secrets
from fastapi import APIRouter, Depends, Request
from langfuse import Langfuse
from minio import Minio
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import ChatRequest, ChatResponse, UserOut
from app.infra.modelserver_client import ModelServerClient
from app.infra.vault import VaultSecrets
from app.services import chat_service

router = APIRouter(prefix="/chat", tags=["chat"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
SecretsDep = Annotated[VaultSecrets, Depends(get_secrets)]
CurrentUserDep = Annotated[UserOut, Depends(get_current_user)]
RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]


def _get_minio(request: Request) -> Minio:
    return request.app.state.minio_client  # type: ignore[no-any-return]


def _get_modelserver(request: Request) -> ModelServerClient:
    return request.app.state.modelserver_client  # type: ignore[no-any-return]


def _get_langfuse(request: Request) -> Langfuse:
    return request.app.state.langfuse  # type: ignore[no-any-return]


MinioDep = Annotated[Minio, Depends(_get_minio)]
ModelServerDep = Annotated[ModelServerClient, Depends(_get_modelserver)]
LangfuseDep = Annotated[Langfuse, Depends(_get_langfuse)]


@router.post("", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    db: DbDep,
    secrets: SecretsDep,
    redis: RedisDep,
    minio: MinioDep,
    modelserver: ModelServerDep,
    lf: LangfuseDep,
    user: CurrentUserDep,
) -> ChatResponse:
    return await chat_service.chat(
        db=db,
        req=req,
        user_id=user.id,
        api_key=secrets.openai_api_key,
        minio_client=minio,
        modelserver_client=modelserver,
        langfuse=lf,
        redis=redis,
    )
