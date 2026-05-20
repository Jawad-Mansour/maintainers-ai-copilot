"""FastAPI application entry point.

Lifespan enforces the refuse-to-boot contract:
  - Vault must be reachable and authenticated
  - eval_thresholds.yaml must exist with all values > 0
The container exits non-zero on any violation.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from config import get_settings
from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.infra.vault import VaultSecrets, fetch_vault_secrets

THRESHOLDS_FILE = Path(__file__).parent.parent / "eval_thresholds.yaml"


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
    # ──────────────────────────────────────────────────────────────────

    app.state.secrets = secrets
    app.state.settings = settings

    yield


app = FastAPI(title="Maintainer's AI Copilot", version="0.1.0", lifespan=lifespan)

app.include_router(health_router)
