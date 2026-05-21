"""Widget service — business logic for widget CRUD."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import WidgetCreate, WidgetOut, WidgetUpdate
from app.exceptions import NotFoundError
from app.repositories import audit_repo, widget_repo


async def create_widget(db: AsyncSession, owner_id: uuid.UUID, req: WidgetCreate) -> WidgetOut:
    widget = await widget_repo.create(
        db,
        owner_id=owner_id,
        name=req.name,
        allowed_origins=req.allowed_origins,
        theme=req.theme,
        greeting=req.greeting,
        enabled_tools=req.enabled_tools,
    )
    await audit_repo.log(db, actor_id=owner_id, action="create_widget", target_id=widget.id)
    await db.commit()
    return WidgetOut.model_validate(widget)


async def get_widget(db: AsyncSession, widget_id: uuid.UUID) -> WidgetOut:
    widget = await widget_repo.get(db, widget_id)
    if not widget:
        raise NotFoundError("Widget not found")
    return WidgetOut.model_validate(widget)


async def list_widgets(db: AsyncSession) -> list[WidgetOut]:
    widgets = await widget_repo.list_all(db)
    return [WidgetOut.model_validate(w) for w in widgets]


async def update_widget(
    db: AsyncSession,
    widget_id: uuid.UUID,
    req: WidgetUpdate,
    actor_id: uuid.UUID | None = None,
) -> WidgetOut:
    widget = await widget_repo.get(db, widget_id)
    if not widget:
        raise NotFoundError("Widget not found")
    fields = req.model_dump(exclude_unset=True)
    widget = await widget_repo.update(db, widget, **fields)
    await audit_repo.log(
        db,
        actor_id=actor_id,
        action="update_widget",
        target_id=widget_id,
        diff=fields,
    )
    await db.commit()
    return WidgetOut.model_validate(widget)


async def delete_widget(
    db: AsyncSession,
    widget_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> None:
    widget = await widget_repo.get(db, widget_id)
    if not widget:
        raise NotFoundError("Widget not found")
    await widget_repo.delete(db, widget)
    await audit_repo.log(db, actor_id=actor_id, action="delete_widget", target_id=widget_id)
    await db.commit()
