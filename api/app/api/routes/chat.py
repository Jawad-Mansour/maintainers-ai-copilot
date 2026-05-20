"""Chat routes — POST /chat (JSON) and POST /chat/stream (SSE)."""

from __future__ import annotations

import uuid
from typing import Annotated

import redis.asyncio as aioredis
from dependencies import get_current_user, get_db, get_redis, get_secrets
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from langfuse import Langfuse
from minio import Minio
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import ChatRequest, ChatResponse, UserOut
from app.exceptions import PermissionDenied
from app.infra.modelserver_client import ModelServerClient
from app.infra.vault import VaultSecrets
from app.repositories import widget_repo
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


@router.post("/stream")
async def stream_chat(
    req: ChatRequest,
    db: DbDep,
    secrets: SecretsDep,
    redis: RedisDep,
    minio: MinioDep,
    modelserver: ModelServerDep,
    lf: LangfuseDep,
    user: CurrentUserDep,
    request: Request,
    widget_id: Annotated[uuid.UUID | None, Query()] = None,
) -> StreamingResponse:
    """Stream chat response as SSE tokens.

    If widget_id is provided, the request Origin must be in widget.allowed_origins.
    """
    if widget_id is not None:
        widget = await widget_repo.get(db, widget_id)
        if widget and widget.allowed_origins:
            origin = request.headers.get("origin", "")
            if origin and origin not in widget.allowed_origins:
                raise PermissionDenied(f"Origin '{origin}' not allowed for this widget")

    generator = await chat_service.stream_chat(
        db=db,
        req=req,
        user_id=user.id,
        api_key=secrets.openai_api_key,
        minio_client=minio,
        modelserver_client=modelserver,
        langfuse=lf,
        redis=redis,
    )

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
