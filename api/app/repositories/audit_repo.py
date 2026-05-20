"""Audit log repository — append only, never updates or deletes."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models import AuditLog


async def log(
    db: AsyncSession,
    actor_id: uuid.UUID | None,
    action: str,
    target_id: uuid.UUID | None = None,
    diff: dict[str, object] | None = None,
) -> None:
    entry = AuditLog(
        id=uuid.uuid4(),
        actor_id=actor_id,
        action=action,
        target_id=target_id,
        diff=diff,
    )
    db.add(entry)
