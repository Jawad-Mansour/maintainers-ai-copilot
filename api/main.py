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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langfuse import Langfuse

from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.conversations import router as conversations_router
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
    # ──────────────────────────────────────────────────────────────────

    app.state.secrets = secrets
    app.state.settings = settings
    app.state.session_factory = build_session_factory(secrets.db_url)
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

# CORS origins come from the CORS_ORIGINS env var (comma-separated).
# Wildcard "*" with allow_credentials=True is rejected by all browsers.
# In docker-compose, set CORS_ORIGINS to the widget + chatbot origins.
_cors_origins = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:8501,http://localhost:5173,http://localhost:3001,http://localhost:3000",
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
app.include_router(conversations_router)
app.include_router(memories_router)
app.include_router(rag_router)
app.include_router(chat_router)
app.include_router(widgets_router)
