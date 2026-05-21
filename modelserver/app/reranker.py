"""Cross-encoder reranker using ms-marco-MiniLM-L-6-v2."""

from __future__ import annotations

from sentence_transformers import CrossEncoder

_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class Reranker:
    def __init__(self) -> None:
        # Downloads from HuggingFace Hub on first boot; cached under ~/.cache afterwards
        self._model = CrossEncoder(_MODEL_NAME)

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        """Return a relevance score per passage (higher = more relevant)."""
        if not passages:
            return []
        pairs = [(query, p) for p in passages]
        scores = self._model.predict(pairs)
        return [float(s) for s in scores]
