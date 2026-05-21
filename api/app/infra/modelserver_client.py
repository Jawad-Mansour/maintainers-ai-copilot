"""HTTP client for the modelserver — classify, rerank, NER."""

from __future__ import annotations

import httpx

from app.exceptions import ToolFailure


class ModelServerClient:
    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")

    async def classify(self, texts: list[str]) -> list[str]:
        """Return a predicted label for each text."""
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(f"{self._base}/classify", json={"texts": texts})
                resp.raise_for_status()
                return resp.json()["labels"]
            except httpx.HTTPError as exc:
                raise ToolFailure(f"modelserver /classify failed: {exc}") from exc

    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        """Return cross-encoder scores for each passage."""
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    f"{self._base}/rerank",
                    json={"query": query, "passages": passages},
                )
                resp.raise_for_status()
                return resp.json()["scores"]
            except httpx.HTTPError as exc:
                raise ToolFailure(f"modelserver /rerank failed: {exc}") from exc

    async def ner(self, text: str) -> list[dict]:
        """Return NER entities."""
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(f"{self._base}/ner", json={"text": text})
                resp.raise_for_status()
                return resp.json()["entities"]
            except httpx.HTTPError as exc:
                raise ToolFailure(f"modelserver /ner failed: {exc}") from exc

    async def summarize(self, thread: str) -> str:
        """Summarize a thread via the modelserver LLM summarizer."""
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                resp = await client.post(f"{self._base}/summarize", json={"thread": thread})
                resp.raise_for_status()
                return resp.json()["summary"]
            except httpx.HTTPError as exc:
                raise ToolFailure(f"modelserver /summarize failed: {exc}") from exc
