"""Tool executor — ToolContext dataclass + execute_tool() dispatch."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from minio import Minio
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ToolFailure
from app.infra.modelserver_client import ModelServerClient
from app.infra.prompts import load_prompt


@dataclass
class ToolContext:
    db: AsyncSession
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    api_key: str
    minio_client: Minio
    modelserver_client: ModelServerClient
    history: list[dict] = field(default_factory=list)


async def execute_tool(name: str, args: dict, ctx: ToolContext) -> str:
    """Dispatch a tool call by name and return its string result."""
    if name == "classify_issue":
        labels = await ctx.modelserver_client.classify([args["text"]])
        return labels[0] if labels else "unknown"

    if name == "search_knowledge_base":
        from app.domain.models import SearchRequest
        from app.services.rag_service import search as rag_search

        req = SearchRequest(
            query=args["query"],
            conversation_id=ctx.conversation_id,
            label=args.get("label"),
            top_k=int(args.get("top_k", 5)),
        )
        results = await rag_search(
            ctx.db, req, ctx.api_key, ctx.minio_client, ctx.modelserver_client
        )
        return json.dumps(
            [{"text": r.text, "source": r.source, "score": round(r.score, 4)} for r in results]
        )

    if name == "extract_entities":
        entities = await ctx.modelserver_client.ner(args["text"])
        return json.dumps(entities)

    if name == "summarize_thread":
        focus = args.get("focus", "")
        thread_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in ctx.history
            if isinstance(m.get("content"), str)
        )
        prompt = load_prompt("summarize").format(focus=focus, conversation=thread_text)
        client = AsyncOpenAI(api_key=ctx.api_key)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
            temperature=0.3,
        )
        return resp.choices[0].message.content or ""

    if name == "write_memory":
        from app.services.memory_service import save_memory

        await save_memory(ctx.db, ctx.user_id, args["summary"], ctx.api_key)
        return f"Memory saved: {args['summary'][:80]}"

    raise ToolFailure(f"Unknown tool: {name}")
