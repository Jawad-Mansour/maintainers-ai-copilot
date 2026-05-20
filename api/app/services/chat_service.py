"""Chat service — classify → retrieve → generate → store → memory."""

from __future__ import annotations

import uuid

from langfuse import Langfuse
from minio import Minio
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import ChatRequest, ChatResponse, SearchRequest
from app.infra.modelserver_client import ModelServerClient
from app.infra.observability import get_logger
from app.repositories import message_repo
from app.services import memory_service
from app.services.rag_service import search as rag_search

logger = get_logger(__name__)

_SYSTEM_PROMPT = """\
You are a GitHub maintainer copilot helping with {label} issues.

Relevant memories from past conversations:
{memories}

Relevant knowledge base:
{chunks}

Answer concisely and accurately. If unsure, say so.\
"""


async def chat(
    db: AsyncSession,
    req: ChatRequest,
    user_id: uuid.UUID,
    api_key: str,
    minio_client: Minio,
    modelserver_client: ModelServerClient,
    langfuse: Langfuse,
) -> ChatResponse:
    # 1. Classify issue type
    labels = await modelserver_client.classify([req.message])
    label = labels[0] if labels else "unknown"

    # 2. Retrieve relevant past memories
    memories = await memory_service.get_relevant_memories(db, user_id, req.message, api_key)

    # 3. RAG search filtered by label
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
    )

    # 4. Fetch recent conversation history (last 10 turns)
    history = await message_repo.list_by_conversation(db, req.conversation_id)

    # 5. Build prompt
    memories_text = "\n".join(f"- {m}" for m in memories) or "None"
    chunks_text = (
        "\n".join(f"[{c.label or 'general'}] {c.parent_text or c.text}" for c in chunks) or "None"
    )
    system = _SYSTEM_PROMPT.format(label=label, memories=memories_text, chunks=chunks_text)

    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for msg in history[-10:]:
        messages.append({"role": msg.role, "content": msg.content})
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

    # 8. Save this exchange as a memory for future conversations
    await memory_service.save_memory(db, user_id, req.message[:500], api_key)
    await db.commit()

    logger.info("chat_complete", label=label, conversation_id=str(req.conversation_id))
    return ChatResponse(
        reply=reply,
        label=label,
        sources=list({c.source for c in chunks if c.source}),
    )
