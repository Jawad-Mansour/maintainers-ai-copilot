"""OpenAI embedding wrapper — async, no business logic."""

from __future__ import annotations

from openai import AsyncOpenAI

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


async def embed_texts(texts: list[str], api_key: str) -> list[list[float]]:
    client = AsyncOpenAI(api_key=api_key)
    resp = await client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in resp.data]


async def embed_one(text: str, api_key: str) -> list[float]:
    return (await embed_texts([text], api_key))[0]
