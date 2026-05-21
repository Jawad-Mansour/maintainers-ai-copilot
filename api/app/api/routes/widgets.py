"""Widget routes — CRUD (admin only) + GET /widget.js loader script."""

from __future__ import annotations

import os
import uuid
from typing import Annotated

from dependencies import get_db, require_admin
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
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


_WIDGET_BASE_URL = os.environ.get("WIDGET_BASE_URL", "http://localhost:5173")


@router.get("/widget.js")
async def widget_loader_script(
    widget_id: Annotated[uuid.UUID, Query(description="Widget ID to embed")],
) -> Response:
    """Return the JavaScript loader snippet for embedding the chat widget."""
    js = f"""(function() {{
  var widgetId = "{widget_id}";
  var iframe = document.createElement("iframe");
  iframe.src = "{_WIDGET_BASE_URL}/?widget_id=" + widgetId;
  iframe.style.cssText = [
    "position:fixed", "bottom:20px", "right:20px",
    "width:400px", "height:600px", "border:none",
    "border-radius:12px", "box-shadow:0 4px 24px rgba(0,0,0,0.18)",
    "z-index:99999"
  ].join(";");
  iframe.allow = "microphone";
  document.body.appendChild(iframe);
}})();
"""
    return Response(content=js, media_type="application/javascript")


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
