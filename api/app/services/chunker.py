"""Hierarchical parent-child chunker.

parent: ~1024 tokens — returned to the LLM for context
child:  ~256 tokens  — embedded and used for retrieval
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import tiktoken

PARENT_TOKENS = 1024
CHILD_TOKENS = 256

_enc = tiktoken.get_encoding("cl100k_base")


@dataclass
class ChunkRecord:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    text: str = ""
    chunk_type: str = ""  # "parent" | "child"
    parent_id: uuid.UUID | None = None
    label: str | None = None
    source: str | None = None


def _split_by_tokens(text: str, max_tokens: int) -> list[str]:
    tokens = _enc.encode(text)
    chunks = []
    for i in range(0, len(tokens), max_tokens):
        chunk_tokens = tokens[i : i + max_tokens]
        chunks.append(_enc.decode(chunk_tokens))
    return chunks if chunks else [text]


def make_chunks(text: str, source: str, label: str | None = None) -> list[ChunkRecord]:
    """Split text into parent and child ChunkRecords."""
    records: list[ChunkRecord] = []
    for parent_text in _split_by_tokens(text, PARENT_TOKENS):
        parent = ChunkRecord(text=parent_text, chunk_type="parent", source=source, label=label)
        records.append(parent)
        for child_text in _split_by_tokens(parent_text, CHILD_TOKENS):
            records.append(
                ChunkRecord(
                    text=child_text,
                    chunk_type="child",
                    parent_id=parent.id,
                    source=source,
                    label=label,
                )
            )
    return records
