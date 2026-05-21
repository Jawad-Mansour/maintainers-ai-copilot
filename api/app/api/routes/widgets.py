"""Widget routes — CRUD (admin only).

The public /widget.js loader lives in app/api/routes/embed.py so it is
served at the root path, not under /widgets.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from dependencies import get_db, require_admin
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import UserOut, WidgetCreate, WidgetOut, WidgetUpdate
from app.services import widget_service

router = APIRouter(prefix="/widgets", tags=["widgets"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
AdminDep = Annotated[UserOut, Depends(require_admin)]


@router.post("", response_model=WidgetOut, status_code=201)
async def create_widget(
    req: WidgetCreate,
    db: DbDep,
    admin: AdminDep,
) -> WidgetOut:
    return await widget_service.create_widget(db, admin.id, req)


@router.get("", response_model=list[WidgetOut])
async def list_widgets(
    db: DbDep,
    _admin: AdminDep,
) -> list[WidgetOut]:
    return await widget_service.list_widgets(db)


@router.get("/{widget_id}", response_model=WidgetOut)
async def get_widget(
    widget_id: uuid.UUID,
    db: DbDep,
    _admin: AdminDep,
) -> WidgetOut:
    return await widget_service.get_widget(db, widget_id)


@router.put("/{widget_id}", response_model=WidgetOut)
async def update_widget(
    widget_id: uuid.UUID,
    req: WidgetUpdate,
    db: DbDep,
    admin: AdminDep,
) -> WidgetOut:
    return await widget_service.update_widget(db, widget_id, req, actor_id=admin.id)


@router.delete("/{widget_id}", status_code=204)
async def delete_widget(
    widget_id: uuid.UUID,
    db: DbDep,
    admin: AdminDep,
) -> None:
    await widget_service.delete_widget(db, widget_id, actor_id=admin.id)
