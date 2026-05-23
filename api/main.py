"""FastAPI application entry point.

Lifespan enforces the refuse-to-boot contract:
  - Vault must be reachable and authenticated
  - eval_thresholds.yaml must exist with all values > 0
The container exits non-zero on any violation.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import yaml
from config import get_settings
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langfuse import Langfuse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.embed import router as embed_router
from app.api.routes.health import router as health_router
from app.api.routes.memories import router as memories_router
from app.api.routes.rag import router as rag_router
from app.api.routes.widgets import router as widgets_router
from app.exceptions import AppError
from app.infra.db.session import build_session_factory
from app.infra.minio_client import build_minio
from app.infra.modelserver_client import ModelServerClient
from app.infra.observability import configure_logging, get_logger
from app.infra.redis_client import build_redis
from app.infra.vault import VaultSecrets, fetch_vault_secrets
from app.repositories import widget_repo

THRESHOLDS_FILE = Path(__file__).parent.parent / "eval_thresholds.yaml"

logger = get_logger(__name__)


def _check_eval_thresholds() -> None:
    if not THRESHOLDS_FILE.exists():
        raise RuntimeError(
            f"eval_thresholds.yaml not found at {THRESHOLDS_FILE}. "
            "Cannot boot without committed evaluation thresholds."
        )
    data: dict[str, Any] = yaml.safe_load(THRESHOLDS_FILE.read_text())
    for section, metrics in data.items():
        if not isinstance(metrics, dict):
            continue
        for key, value in metrics.items():
            if isinstance(value, (int, float)) and value <= 0:
                raise RuntimeError(
                    f"eval_thresholds.yaml: [{section}] {key} = {value} is zero or "
                    "disabled. All thresholds must be > 0."
                )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    settings = get_settings()

    # ── Refuse-to-boot checks ──────────────────────────────────────────
    try:
        _check_eval_thresholds()
    except RuntimeError as exc:
        print(f"[BOOT FAILURE] {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        secrets: VaultSecrets = fetch_vault_secrets(settings.vault_addr, settings.vault_token)
    except RuntimeError as exc:
        print(f"[BOOT FAILURE] {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        async with httpx.AsyncClient(timeout=5) as hc:
            resp = await hc.get(f"http://{settings.modelserver_host}:8001/health")
            resp.raise_for_status()
            health = resp.json()
    except Exception as exc:
        print(f"[BOOT FAILURE] modelserver unreachable: {exc}", file=sys.stderr)
        sys.exit(1)

    # Phase 7-C: refuse to boot if modelserver is still serving mocks.
    # Set REQUIRE_REAL_MODELSERVER=false to skip this check during development.
    require_real = settings.require_real_modelserver
    if require_real and health.get("mode") != "real":
        print(
            "[BOOT FAILURE] modelserver is running in mock mode. "
            "Upload trained weights to MinIO first, then restart. "
            "Set REQUIRE_REAL_MODELSERVER=false to bypass during development.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Refuse to boot if tracing backend is misconfigured.
    try:
        _lf = Langfuse(
            public_key=secrets.langfuse_public_key,
            secret_key=secrets.langfuse_secret_key,
            host=secrets.langfuse_host,
        )
        if not _lf.auth_check():
            print(
                "[BOOT FAILURE] Langfuse auth_check failed. "
                "Verify langfuse public_key, secret_key, host in Vault at secret/langfuse.",
                file=sys.stderr,
            )
            sys.exit(1)
    except Exception as exc:
        print(f"[BOOT FAILURE] Langfuse misconfigured: {exc}", file=sys.stderr)
        sys.exit(1)
    # ──────────────────────────────────────────────────────────────────

    app.state.secrets = secrets
    app.state.settings = settings
    app.state.session_factory = build_session_factory(secrets.db_url)

    # Extend CORS origins with all widget allowed_origins from DB
    try:
        async with app.state.session_factory() as _db:
            _widget_origins = await widget_repo.get_all_allowed_origins(_db)
        _cors_origins.update(_widget_origins)
    except Exception as exc:
        print(f"[BOOT WARNING] Could not load widget CORS origins from DB: {exc}", file=sys.stderr)
    app.state.redis_client = build_redis(settings.redis_host)
    app.state.minio_client = build_minio(
        secrets.minio_endpoint,
        secrets.minio_access_key,
        secrets.minio_secret_key,
    )
    app.state.modelserver_client = ModelServerClient(f"http://{settings.modelserver_host}:8001")
    app.state.langfuse = Langfuse(
        public_key=secrets.langfuse_public_key,
        secret_key=secrets.langfuse_secret_key,
        host=secrets.langfuse_host,
    )

    yield

    app.state.langfuse.flush()
    await app.state.redis_client.aclose()


app = FastAPI(title="Maintainer's AI Copilot", version="0.1.0", lifespan=lifespan)

# Static CORS origins from env var — extended with widget DB allowed_origins at startup.
_static_cors: set[str] = {
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:8501,http://localhost:5173,http://localhost:3001,http://localhost:3000",
    ).split(",")
    if o.strip()
}
_cors_origins: set[str] = set(_static_cors)  # mutated in lifespan with widget DB origins


class DynamicCORSMiddleware:
    """Pure ASGI CORS middleware — origins = env var list ∪ DB widget allowed_origins."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from starlette.datastructures import Headers

        headers = Headers(scope=scope)
        origin = headers.get("origin", "")
        method = scope.get("method", "")
        allowed = bool(origin) and origin in _cors_origins

        if method == "OPTIONS" and allowed:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"access-control-allow-origin", origin.encode()),
                        (b"access-control-allow-credentials", b"true"),
                        (
                            b"access-control-allow-methods",
                            b"GET, POST, PUT, DELETE, OPTIONS, PATCH",
                        ),
                        (b"access-control-allow-headers", b"authorization, content-type, accept"),
                        (b"access-control-max-age", b"3600"),
                        (b"vary", b"origin"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message: dict) -> None:
            if message["type"] == "http.response.start" and allowed:
                extra = [
                    (b"access-control-allow-origin", origin.encode()),
                    (b"access-control-allow-credentials", b"true"),
                    (b"vary", b"origin"),
                ]
                message = {**message, "headers": list(message.get("headers", [])) + extra}
            await send(message)

        await self.app(scope, receive, send_with_cors)


app.add_middleware(DynamicCORSMiddleware)


# ── Request middleware: bind trace_id to every structlog context ───────────
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

from app.infra.observability import bind_trace_id, clear_trace_id  # noqa: E402


class TraceIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        trace_id = str(uuid4())
        bind_trace_id(trace_id)
        try:
            response = await call_next(request)
        finally:
            clear_trace_id()
        response.headers["X-Trace-ID"] = trace_id
        return response


app.add_middleware(TraceIDMiddleware)


# ── Exception handlers ─────────────────────────────────────────────────────


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    request_id = str(uuid4())
    logger.error("app_error", code=exc.code, message=exc.message, request_id=request_id)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "message": exc.message, "request_id": request_id},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = str(uuid4())
    logger.exception("unhandled_error", request_id=request_id)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An unexpected error occurred",
            "request_id": request_id,
        },
    )


# ── Routers ────────────────────────────────────────────────────────────────

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(conversations_router)
app.include_router(memories_router)
app.include_router(rag_router)
app.include_router(chat_router)
app.include_router(widgets_router)
app.include_router(embed_router)
