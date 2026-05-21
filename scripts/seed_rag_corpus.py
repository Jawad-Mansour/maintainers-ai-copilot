#!/usr/bin/env python3
"""Seed a minimal RAG corpus for CI from golden_rag.json.

Steps:
    1. Register eval user (idempotent)
    2. Promote user to admin via DB env-var (requires DB access or pre-promotion)
    3. Login to get JWT
    4. POST /rag/ingest for each golden triple's ideal_answer as a document

Usage:
    python scripts/seed_rag_corpus.py

Environment variables:
    API_URL          default: http://localhost:8000
    EVAL_EMAIL       default: eval@example.com
    EVAL_PASSWORD    default: eval_password_123
    ADMIN_TOKEN      if set, skip register/promote and use this token directly
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
GOLDEN = ROOT / "evals" / "golden_rag.json"

API_URL = os.environ.get("API_URL", "http://localhost:8000")
EVAL_EMAIL = os.environ.get("EVAL_EMAIL", "eval@example.com")
EVAL_PASSWORD = os.environ.get("EVAL_PASSWORD", "eval_password_123")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


def _register() -> None:
    resp = requests.post(
        f"{API_URL}/auth/register",
        json={"email": EVAL_EMAIL, "password": EVAL_PASSWORD, "role": "user"},
        timeout=15,
    )
    if resp.status_code in (200, 201):
        print(f"  Registered {EVAL_EMAIL}")
    elif resp.status_code == 400:
        print(f"  {EVAL_EMAIL} already exists")
    else:
        resp.raise_for_status()


def _login() -> str:
    resp = requests.post(
        f"{API_URL}/auth/login",
        json={"email": EVAL_EMAIL, "password": EVAL_PASSWORD},
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    print(f"  Logged in as {EVAL_EMAIL}")
    return token


def _ingest(token: str, text: str, source: str, label: str) -> None:
    resp = requests.post(
        f"{API_URL}/rag/ingest",
        json={"text": text, "source": source, "label": label},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()


def main() -> int:
    print("=" * 60)
    print("SEED RAG CORPUS")
    print("=" * 60)

    if not GOLDEN.exists():
        print(f"[ERROR] {GOLDEN} not found")
        return 1

    triples: list[dict] = json.loads(GOLDEN.read_text())
    print(f"Golden set: {len(triples)} triples")

    if ADMIN_TOKEN:
        token = ADMIN_TOKEN
        print("  Using ADMIN_TOKEN from environment")
    else:
        print("\n[1/2] Register + login ...")
        _register()
        token = _login()
        print(
            "\n  NOTE: user needs admin role to ingest."
            "\n  If ingest fails with 403, run:"
            "\n    docker compose exec db psql -U postgres -d maintainers_copilot"
            f"\n    UPDATE users SET role='admin' WHERE email='{EVAL_EMAIL}';"
        )

    print(f"\n[2/2] Ingesting {len(triples)} documents ...")
    ok = 0
    for triple in triples:
        qid = triple["id"]
        text = triple["ideal_answer"]
        source = f"golden_rag_{qid}"
        label = "docs"
        try:
            _ingest(token, text, source, label)
            print(f"  [{qid:02d}] OK", end="\r", flush=True)
            ok += 1
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 403:
                print(
                    f"\n  [ERROR] 403 Forbidden — promote {EVAL_EMAIL} to admin first:"
                    "\n    docker compose exec db psql -U postgres -d maintainers_copilot"
                    f"\n    UPDATE users SET role='admin' WHERE email='{EVAL_EMAIL}';"
                )
                return 1
            print(f"\n  [WARN] #{qid}: {exc}")
        except Exception as exc:
            print(f"\n  [WARN] #{qid}: {exc}")

    print(f"\nIngested {ok}/{len(triples)} documents")

    if ok == 0:
        return 1

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
