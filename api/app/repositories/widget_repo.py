"""Widget repository — CRUD for the widgets table."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models import Widget


async def create(
    db: AsyncSession,
    owner_id: uuid.UUID,
    name: str,
    allowed_origins: list[str],
    theme: dict | None = None,
    greeting: str = "How can I help?",
    enabled_tools: list[str] | None = None,
) -> Widget:
    widget = Widget(
        owner_id=owner_id,
        name=name,
        allowed_origins=allowed_origins,
        theme=theme,
        greeting=greeting,
        enabled_tools=enabled_tools or [],
    )
    db.add(widget)
    await db.flush()
    return widget


async def get(db: AsyncSession, widget_id: uuid.UUID) -> Widget | None:
    result = await db.execute(select(Widget).where(Widget.id == widget_id))
    return result.scalar_one_or_none()


async def list_all(db: AsyncSession) -> list[Widget]:
    result = await db.execute(select(Widget).order_by(Widget.created_at.desc()))
    return list(result.scalars().all())


async def update(db: AsyncSession, widget: Widget, **fields: object) -> Widget:
    for key, value in fields.items():
        setattr(widget, key, value)
    await db.flush()
    return widget


async def delete(db: AsyncSession, widget: Widget) -> None:
    await db.delete(widget)
    await db.flush()


async def get_all_allowed_origins(db: AsyncSession) -> list[str]:
    result = await db.execute(select(Widget.allowed_origins))
    origins: list[str] = []
    for (row_origins,) in result.fetchall():
        if row_origins:
            origins.extend(row_origins)
    return origins


async def get_first_active(db: AsyncSession) -> Widget | None:
    result = await db.execute(
        select(Widget).where(Widget.is_active == True).order_by(Widget.created_at.asc()).limit(1)  # noqa: E712
    )
    return result.scalar_one_or_none()
