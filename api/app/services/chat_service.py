"""Chat service — tool-calling agent loop."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

import redis.asyncio as aioredis
from langfuse import Langfuse
from minio import Minio
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import ChatRequest, ChatResponse
from app.exceptions import NotFoundError, PermissionDenied
from app.infra.modelserver_client import ModelServerClient
from app.infra.observability import get_logger
from app.infra.prompts import load_prompt
from app.infra.redis_client import CONVERSATION_TTL
from app.repositories import conversation_repo, message_repo
from app.services import memory_service
from app.tools.definitions import ALL_TOOLS
from app.tools.executor import ToolContext, execute_tool

logger = get_logger(__name__)

_HISTORY_KEY = "conversation:{}"
_MAX_TOOL_ITERS = 5


async def _get_history(
    redis: aioredis.Redis,
    db: AsyncSession,
    conversation_id: uuid.UUID,
) -> list[dict]:
    key = _HISTORY_KEY.format(conversation_id)
    cached = await redis.get(key)
    if cached:
        return json.loads(cached)  # type: ignore[return-value]
    msgs = await message_repo.list_by_conversation(db, conversation_id)
    history = [{"role": m.role, "content": m.content} for m in msgs]
    if history:
        await redis.set(key, json.dumps(history), ex=CONVERSATION_TTL)
    return history


async def _run_tool_loop(
    messages: list[dict],
    ctx: ToolContext,
) -> tuple[str, str, list[str]]:
    """Execute tool-calling loop.

    Returns (reply, label, sources).
    label is set if the LLM called classify_issue.
    sources is populated if the LLM called search_knowledge_base.
    """
    client = AsyncOpenAI(api_key=ctx.api_key)
    label = "unknown"
    sources: list[str] = []

    for _ in range(_MAX_TOOL_ITERS):
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,  # type: ignore[arg-type]
            tools=ALL_TOOLS,  # type: ignore[arg-type]
            max_tokens=512,
            temperature=0.3,
        )
        msg = resp.choices[0].message

        assistant_msg: dict = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_msg)

        if not msg.tool_calls:
            return msg.content or "", label, sources

        for tc in msg.tool_calls:
            try:
                result = await execute_tool(
                    tc.function.name, json.loads(tc.function.arguments), ctx
                )
            except Exception as exc:
                result = f"Tool error: {exc}"

            # Track classify and search results for ChatResponse metadata
            if tc.function.name == "classify_issue":
                label = result
            elif tc.function.name == "search_knowledge_base":
                try:
                    items = json.loads(result)
                    sources.extend(s["source"] for s in items if s.get("source"))
                except Exception:
                    pass

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    # Max iterations reached — force a final response without tools
    final = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,  # type: ignore[arg-type]
        max_tokens=512,
        temperature=0.3,
    )
    reply = final.choices[0].message.content or ""
    messages.append({"role": "assistant", "content": reply})
    return reply, label, sources


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
    # 0. Verify ownership
    conv = await conversation_repo.get(db, req.conversation_id)
    if not conv:
        raise NotFoundError("Conversation not found")
    if conv.user_id != user_id:
        raise PermissionDenied("Not your conversation")

    # 1. Retrieve past memories for system context
    memories = await memory_service.get_relevant_memories(db, user_id, req.message, api_key)

    # 2. Get conversation history
    history = await _get_history(redis, db, req.conversation_id)

    # 3. Build messages
    memories_text = "\n".join(f"- {m}" for m in memories) or "None"
    system = load_prompt("system").format(memories=memories_text)

    messages: list[dict] = [{"role": "system", "content": system}]
    for msg in history[-10:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": req.message})

    # 4. Tool context
    ctx = ToolContext(
        db=db,
        user_id=user_id,
        conversation_id=req.conversation_id,
        api_key=api_key,
        minio_client=minio_client,
        modelserver_client=modelserver_client,
        history=history,
    )

    # 5. Langfuse trace
    trace = langfuse.trace(
        name="chat",
        user_id=str(user_id),
        metadata={"conversation_id": str(req.conversation_id)},
    )

    # 6. Tool-calling loop
    reply, label, sources = await _run_tool_loop(messages, ctx)
    trace.generation(name="gpt-4o-mini", model="gpt-4o-mini", input=messages, output=reply)

    # 7. Persist messages
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
    return ChatResponse(reply=reply, label=label, sources=list(set(sources)))


async def stream_chat(
    db: AsyncSession,
    req: ChatRequest,
    user_id: uuid.UUID,
    api_key: str,
    minio_client: Minio,
    modelserver_client: ModelServerClient,
    langfuse: Langfuse,
    redis: aioredis.Redis,
) -> AsyncGenerator[str, None]:
    """SSE generator: resolve tool calls, then stream the final LLM response token-by-token."""
    conv = await conversation_repo.get(db, req.conversation_id)
    if not conv:
        raise NotFoundError("Conversation not found")
    if conv.user_id != user_id:
        raise PermissionDenied("Not your conversation")

    memories = await memory_service.get_relevant_memories(db, user_id, req.message, api_key)
    history = await _get_history(redis, db, req.conversation_id)

    memories_text = "\n".join(f"- {m}" for m in memories) or "None"
    system = load_prompt("system").format(memories=memories_text)

    messages: list[dict] = [{"role": "system", "content": system}]
    for msg in history[-10:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": req.message})

    ctx = ToolContext(
        db=db,
        user_id=user_id,
        conversation_id=req.conversation_id,
        api_key=api_key,
        minio_client=minio_client,
        modelserver_client=modelserver_client,
        history=history,
    )

    langfuse.trace(
        name="chat_stream",
        user_id=str(user_id),
        metadata={"conversation_id": str(req.conversation_id)},
    )

    # Resolve tool calls (non-streaming) — only stream the final LLM generation
    _reply, label, sources = await _run_tool_loop(messages, ctx)

    # Stream the final response
    client = AsyncOpenAI(api_key=api_key)
    reply_parts: list[str] = []

    async def _generate() -> AsyncGenerator[str, None]:
        async with client.chat.completions.stream(
            model="gpt-4o-mini",
            messages=messages,  # type: ignore[arg-type]
            max_tokens=512,
            temperature=0.3,
        ) as stream:
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    reply_parts.append(delta)
                    yield f"data: {json.dumps({'type': 'token', 'content': delta})}\n\n"

        reply = "".join(reply_parts)
        await message_repo.create(db, req.conversation_id, "user", req.message)
        await message_repo.create(db, req.conversation_id, "assistant", reply)

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

        done_payload = json.dumps({"type": "done", "label": label, "sources": list(set(sources))})
        yield f"data: {done_payload}\n\n"

    return _generate()
