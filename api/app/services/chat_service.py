"""Chat service — classify → retrieve → generate → store."""

from __future__ import annotations

import json
import uuid

import redis.asyncio as aioredis
from langfuse import Langfuse
from minio import Minio
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import ChatRequest, ChatResponse, SearchRequest
from app.exceptions import NotFoundError, PermissionDenied, ToolFailure
from app.infra.modelserver_client import ModelServerClient
from app.infra.observability import get_logger
from app.infra.prompts import load_prompt
from app.infra.redis_client import CONVERSATION_TTL
from app.repositories import conversation_repo, message_repo
from app.services import memory_service
from app.services.rag_service import search as rag_search

logger = get_logger(__name__)

_HISTORY_KEY = "conversation:{}"


async def _get_history(
    redis: aioredis.Redis,
    db: AsyncSession,
    conversation_id: uuid.UUID,
) -> list[dict[str, str]]:
    key = _HISTORY_KEY.format(conversation_id)
    cached = await redis.get(key)
    if cached:
        return json.loads(cached)  # type: ignore[return-value]
    msgs = await message_repo.list_by_conversation(db, conversation_id)
    history = [{"role": m.role, "content": m.content} for m in msgs]
    if history:
        await redis.set(key, json.dumps(history), ex=CONVERSATION_TTL)
    return history


async def chat(
    db: AsyncSession,
    req: ChatRequest,
    user_id: uuid.UUID,
    api_key: str,
    minio_client: Minio,
    modelserver_client: ModelServerClient,
    langfuse: Langfuse,
    redis: aioredis.Redis,
) -> ChatResponse:
    # 0. Verify conversation ownership
    conv = await conversation_repo.get(db, req.conversation_id)
    if not conv:
        raise NotFoundError("Conversation not found")
    if conv.user_id != user_id:
        raise PermissionDenied("Not your conversation")

    # 1. Classify issue type — fall back gracefully if modelserver is down
    try:
        labels = await modelserver_client.classify([req.message])
        label = labels[0] if labels else "unknown"
    except ToolFailure:
        logger.warning("classify_failed_falling_back", conversation_id=str(req.conversation_id))
        label = "unknown"

    # 2. Retrieve relevant past memories
    memories = await memory_service.get_relevant_memories(db, user_id, req.message, api_key)

    # 3. RAG search filtered by label — fall back to empty on tool failure
    try:
        chunks = await rag_search(
            db,
            SearchRequest(
                query=req.message,
                conversation_id=req.conversation_id,
                label=label if label != "unknown" else None,
                top_k=5,
            ),
            api_key,
            minio_client,
            modelserver_client,
        )
    except ToolFailure:
        logger.warning("rag_search_failed_falling_back", conversation_id=str(req.conversation_id))
        chunks = []

    # 4. Fetch recent conversation history (Redis-backed, last 10 turns)
    history = await _get_history(redis, db, req.conversation_id)

    # 5. Build prompt
    memories_text = "\n".join(f"- {m}" for m in memories) or "None"
    chunks_text = (
        "\n".join(f"[{c.label or 'general'}] {c.parent_text or c.text}" for c in chunks) or "None"
    )
    system = load_prompt("system").format(label=label, memories=memories_text, chunks=chunks_text)

    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for msg in history[-10:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": req.message})

    # 6. LLM call — traced in Langfuse
    trace = langfuse.trace(
        name="chat",
        user_id=str(user_id),
        metadata={"conversation_id": str(req.conversation_id), "label": label},
    )
    generation = trace.generation(
        name="gpt-4o-mini",
        model="gpt-4o-mini",
        model_parameters={"max_tokens": 512, "temperature": 0.3},
        input=messages,
    )

    client = AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,  # type: ignore[arg-type]
        max_tokens=512,
        temperature=0.3,
    )
    reply = resp.choices[0].message.content or ""
    generation.end(output=reply)

    # 7. Persist user message + assistant reply
    await message_repo.create(db, req.conversation_id, "user", req.message)
    await message_repo.create(db, req.conversation_id, "assistant", reply)

    # 8. Update Redis history cache
    new_history = history + [
        {"role": "user", "content": req.message},
        {"role": "assistant", "content": reply},
    ]
    await redis.set(
        _HISTORY_KEY.format(req.conversation_id),
        json.dumps(new_history),
        ex=CONVERSATION_TTL,
    )

    await db.commit()

    logger.info("chat_complete", label=label, conversation_id=str(req.conversation_id))
    return ChatResponse(
        reply=reply,
        label=label,
        sources=list({c.source for c in chunks if c.source}),
    )
