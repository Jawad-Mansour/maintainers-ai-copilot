"""Make chunks.embedding nullable — parent chunks have no embedding by design.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-21

Context: Parent chunks (1024 tokens) are stored for LLM context retrieval only.
Only child chunks (256 tokens) are embedded. Migration 0002 incorrectly created
the embedding column with NOT NULL — this migration corrects that.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding DROP NOT NULL")


def downgrade() -> None:
    # Re-adding NOT NULL requires a default — fill NULLs with zero vector first.
    op.execute(
        "UPDATE chunks SET embedding = array_fill(0, ARRAY[1536])::vector WHERE embedding IS NULL"
    )
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding SET NOT NULL")
