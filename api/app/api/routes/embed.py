"""Embed routes — public endpoints for widget embedding.

No auth required on any route here.

  GET /widget.js              — loader script the host page includes via <script>
  GET /widget-config/{id}     — public widget config the React iframe reads at boot
"""

from __future__ import annotations

import os
import uuid
from typing import Annotated

from dependencies import get_db
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import WidgetPublicOut
from app.exceptions import NotFoundError, PermissionDenied
from app.repositories import widget_repo
from app.services import widget_service

router = APIRouter(tags=["embed"])

DbDep = Annotated[AsyncSession, Depends(get_db)]

_WIDGET_BASE_URL = os.environ.get("WIDGET_BASE_URL", "http://localhost:5173")
_API_PUBLIC_URL = os.environ.get("API_PUBLIC_URL", "http://localhost:8000")


@router.get("/widget.js", include_in_schema=False)
async def widget_loader() -> Response:
    """Public loader script — no auth required.

    The host site pastes:
        <script src="/widget.js" data-widget-id="<uuid>"></script>

    This script reads data-widget-id from document.currentScript and injects
    the chat iframe.  The iframe URL is WIDGET_BASE_URL (the React bundle
    server), which is where the React app lives and reads its widget config
    from the API.
    """
    js = f"""(function () {{
  var script = document.currentScript;
  var widgetId = script && script.getAttribute('data-widget-id');
  if (!widgetId) {{ console.error('[Copilot] Missing data-widget-id'); return; }}
  var iframe = document.createElement('iframe');
  iframe.src = '{_WIDGET_BASE_URL}/?widget_id='
    + encodeURIComponent(widgetId) + '&api_url={_API_PUBLIC_URL}';
  iframe.setAttribute('allowtransparency', 'true');
  iframe.setAttribute('frameborder', '0');
  iframe.id = 'copilot-widget-' + widgetId;
  iframe.style.cssText = 'position:fixed;bottom:0;right:0;width:400px;'
    + 'height:80px;border:none;background:transparent;'
    + 'z-index:2147483647;transition:height 0.25s cubic-bezier(0.4,0,0.2,1)';
  document.body.appendChild(iframe);
  window.addEventListener('message', function (e) {{
    if (e.data && e.data.type === 'copilot-resize' && e.data.widgetId === widgetId) {{
      iframe.style.height = e.data.height + 'px';
    }}
  }});
}})();
"""
    return Response(
        content=js,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/widget-default")
async def get_default_widget(db: DbDep) -> JSONResponse:
    """Public — returns the ID of the first active widget for host-page demo setup."""
    widget = await widget_repo.get_first_active(db)
    if not widget:
        raise NotFoundError("No active widget found. Create one via the Admin panel.")
    return JSONResponse({"id": str(widget.id)})


@router.get("/widget-config/{widget_id}", response_model=WidgetPublicOut)
async def get_widget_public_config(
    widget_id: uuid.UUID,
    request: Request,
    db: DbDep,
) -> Response:
    """Public widget config — no auth required.

    Enforces allowed_origins from the DB:
    - If allowed_origins is non-empty, only requests from listed origins are served.
    - Sets Content-Security-Policy: frame-ancestors so browsers block unauthorized hosts.

    The React widget iframe calls this at boot to read theme, greeting, and
    enabled_tools.  allowed_origins is intentionally omitted from the response body.
    """
    full = await widget_service.get_widget(db, widget_id)

    origin = request.headers.get("origin", "")
    allowed = full.allowed_origins  # list[str] from DB

    # Enforce per-widget origin allowlist when it is configured.
    if allowed and origin and origin not in allowed:
        raise PermissionDenied(f"Origin '{origin}' is not in this widget's allowed_origins.")

    # Build CSP frame-ancestors from DB — not from a hardcoded env var.
    csp = "frame-ancestors " + " ".join(allowed) if allowed else "frame-ancestors *"

    public = WidgetPublicOut.model_validate(full)
    return JSONResponse(
        content=public.model_dump(mode="json"),
        headers={"Content-Security-Policy": csp},
    )
