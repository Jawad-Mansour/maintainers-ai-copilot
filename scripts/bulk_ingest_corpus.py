#!/usr/bin/env python3
"""Bulk ingest data/rag_corpus.jsonl into the RAG vector store.

Uses asyncio + aiohttp with a concurrency limit so we don't hammer the API
or exhaust OpenAI rate limits.

Usage:
    ADMIN_TOKEN=<token> python scripts/bulk_ingest_corpus.py
    ADMIN_TOKEN=<token> python scripts/bulk_ingest_corpus.py --workers 20
    ADMIN_TOKEN=<token> python scripts/bulk_ingest_corpus.py --limit 500
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from pathlib import Path

import aiohttp

ROOT = Path(__file__).parent.parent
CORPUS = ROOT / "data" / "rag_corpus.jsonl"

API_URL = os.environ.get("API_URL", "http://localhost:8000")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


def load_corpus(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                with contextlib.suppress(json.JSONDecodeError):
                    records.append(json.loads(line))
    return records


async def ingest_one(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    record: dict,
    idx: int,
    total: int,
) -> tuple[bool, str]:
    source = record.get("html_url") or f"pandas-issue-{record['number']}"
    text = record.get("text", "").strip()
    if not text:
        return False, source

    payload = {"text": text, "source": source, "label": "docs"}
    async with sem:
        try:
            async with session.post(
                f"{API_URL}/rag/ingest",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 201:
                    return True, source
                body = await resp.text()
                print(f"\n  [WARN] #{idx}/{total} HTTP {resp.status}: {body[:120]}")
                return False, source
        except Exception as exc:
            print(f"\n  [WARN] #{idx}/{total} {source}: {exc}")
            return False, source


async def run(workers: int, limit: int | None) -> int:
    if not ADMIN_TOKEN:
        print("[ERROR] ADMIN_TOKEN env var is required.")
        return 1

    if not CORPUS.exists():
        print(f"[ERROR] {CORPUS} not found.")
        return 1

    records = load_corpus(CORPUS)
    if limit:
        records = records[:limit]

    total = len(records)
    print("=" * 60)
    print("BULK INGEST RAG CORPUS")
    print("=" * 60)
    print(f"  Corpus:   {CORPUS.name}")
    print(f"  Records:  {total:,}")
    print(f"  Workers:  {workers}")
    print(f"  API:      {API_URL}")
    print()

    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    sem = asyncio.Semaphore(workers)

    ok = 0
    failed = 0
    t0 = time.time()

    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = [ingest_one(session, sem, rec, i + 1, total) for i, rec in enumerate(records)]

        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            success, source = await coro
            if success:
                ok += 1
            else:
                failed += 1

            # Progress line (overwrite in place)
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else 0
            print(
                f"\r  [{i:>6}/{total}]  ok={ok}  fail={failed}  {rate:.1f}/s  ETA {eta:.0f}s   ",
                end="",
                flush=True,
            )

    elapsed = time.time() - t0
    print(f"\n\nDone in {elapsed:.1f}s — {ok}/{total} ingested, {failed} failed.")
    return 0 if ok > 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=10, help="Concurrent requests (default 10)")
    parser.add_argument(
        "--limit", type=int, default=None, help="Ingest only first N records (for testing)"
    )
    args = parser.parse_args()
    return asyncio.run(run(args.workers, args.limit))


if __name__ == "__main__":
    sys.exit(main())
