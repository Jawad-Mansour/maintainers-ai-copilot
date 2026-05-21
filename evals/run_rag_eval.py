#!/usr/bin/env python3
"""RAG eval — hit@5, MRR@10, RAGAS (faithfulness, answer_relevancy, context_precision).

Runs against evals/golden_rag.json (25 triples).
For RAGAS metrics, answers are generated at eval time via OpenAI.
Thresholds from eval_thresholds.yaml. Report written locally + uploaded to MinIO.

Usage:
    python evals/run_rag_eval.py

Environment variables:
    API_URL           default: http://localhost:8000
    MODELSERVER_URL   default: http://localhost:8001
    OPENAI_API_KEY    required for answer generation + RAGAS (skipped if absent)
    MINIO_ENDPOINT    default: localhost:9000
    MINIO_ACCESS_KEY  default: minioadmin
    MINIO_SECRET_KEY  default: minioadmin_dev
    EVAL_EMAIL        default: eval@example.com
    EVAL_PASSWORD     default: eval_password_123

Exit codes:
    0  all thresholds met
    1  one or more thresholds breached (regression)
    2  runtime error (cannot connect, missing file, etc.)
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent.parent
GOLDEN = ROOT / "evals" / "golden_rag.json"
THRESHOLDS_FILE = ROOT / "eval_thresholds.yaml"
REPORT_PATH = ROOT / "eval_rag_report.json"

API_URL = os.environ.get("API_URL", "http://localhost:8000")
MODELSERVER_URL = os.environ.get("MODELSERVER_URL", "http://localhost:8001")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin_dev")
EVAL_EMAIL = os.environ.get("EVAL_EMAIL", "eval@example.com")
EVAL_PASSWORD = os.environ.get("EVAL_PASSWORD", "eval_password_123")

_ANSWER_SYSTEM = """You are a helpful assistant for a GitHub issue management system.
Given a question and retrieved context passages, answer the question concisely.
Use only information from the provided context. If the context is insufficient, say so."""


# ── Auth helpers ──────────────────────────────────────────────────────────────


def _ensure_user() -> str:
    """Register (if needed) and login; return JWT token."""
    reg = requests.post(
        f"{API_URL}/auth/register",
        json={"email": EVAL_EMAIL, "password": EVAL_PASSWORD, "role": "user"},
        timeout=15,
    )
    if reg.status_code not in (200, 201, 400):
        reg.raise_for_status()

    login = requests.post(
        f"{API_URL}/auth/login",
        json={"email": EVAL_EMAIL, "password": EVAL_PASSWORD},
        timeout=15,
    )
    login.raise_for_status()
    return login.json()["access_token"]


def _create_conversation(token: str) -> str:
    resp = requests.post(
        f"{API_URL}/conversations",
        json={"title": "RAG eval"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["id"]


# ── Retrieval ─────────────────────────────────────────────────────────────────


def retrieve(question: str, conversation_id: str, token: str, top_k: int = 10) -> list[str]:
    """Return list of retrieved chunk texts (up to top_k)."""
    resp = requests.post(
        f"{API_URL}/rag/search",
        json={"query": question, "conversation_id": conversation_id, "top_k": top_k},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    chunks = data.get("chunks") or data.get("results") or []
    texts: list[str] = []
    for c in chunks:
        if isinstance(c, str):
            texts.append(c)
        elif isinstance(c, dict):
            texts.append(c.get("text") or c.get("content") or "")
    return [t for t in texts if t]


# ── Answer generation ─────────────────────────────────────────────────────────


def generate_answer(question: str, contexts: list[str]) -> str:
    if not OPENAI_API_KEY:
        return ""
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    context_block = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts[:5]))
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _ANSWER_SYSTEM},
            {
                "role": "user",
                "content": f"Context:\n{context_block}\n\nQuestion: {question}",
            },
        ],
        temperature=0,
        max_tokens=300,
    )
    return resp.choices[0].message.content or ""


# ── Retrieval metrics ──────────────────────────────────────────────────────────


def _hit(retrieved: list[str], ground_truth_chunks: list[str]) -> bool:
    """True if any ground-truth phrase appears in any retrieved chunk."""
    joined = " ".join(retrieved).lower()
    return any(gt.lower() in joined for gt in ground_truth_chunks)


def compute_retrieval_metrics(
    results: list[dict],
) -> tuple[float, float]:
    """Return (hit@5, MRR@10)."""
    hit_count = 0
    rr_sum = 0.0

    for r in results:
        retrieved = r["retrieved"]
        gts = r["ground_truth_chunks"]

        if _hit(retrieved[:5], gts):
            hit_count += 1

        for rank, chunk in enumerate(retrieved[:10], start=1):
            if any(gt.lower() in chunk.lower() for gt in gts):
                rr_sum += 1.0 / rank
                break

    n = len(results)
    hit_at_5 = round(hit_count / n, 4) if n else 0.0
    mrr_at_10 = round(rr_sum / n, 4) if n else 0.0
    return hit_at_5, mrr_at_10


# ── RAGAS ─────────────────────────────────────────────────────────────────────


def run_ragas(results: list[dict]) -> dict | None:
    if not OPENAI_API_KEY:
        print("[WARN] OPENAI_API_KEY not set — RAGAS skipped")
        return None
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, context_precision, faithfulness
    except ImportError:
        print("[WARN] ragas/datasets not installed — RAGAS skipped")
        return None

    rows = [r for r in results if r.get("answer")]
    if not rows:
        print("[WARN] No answers generated — RAGAS skipped")
        return None

    ds = Dataset.from_list(
        [
            {
                "question": r["question"],
                "answer": r["answer"],
                "contexts": r["retrieved"][:5],
                "ground_truths": r["ground_truth_chunks"],
            }
            for r in rows
        ]
    )
    ragas_result = evaluate(
        ds,
        metrics=[faithfulness, answer_relevancy, context_precision],
    )
    scores = ragas_result.to_pandas()[
        ["faithfulness", "answer_relevancy", "context_precision"]
    ].mean()
    return {
        "faithfulness": round(float(scores["faithfulness"]), 4),
        "answer_relevancy": round(float(scores["answer_relevancy"]), 4),
        "context_precision": round(float(scores["context_precision"]), 4),
    }


# ── Judge agreement (hand-labeled subset) ────────────────────────────────────


def compute_judge_agreement(results: list[dict]) -> float | None:
    hand_labeled = [r for r in results if r.get("hand_labeled")]
    if not hand_labeled:
        return None
    hits = sum(1 for r in hand_labeled if r.get("hit5"))
    return round(hits / len(hand_labeled), 4)


# ── MinIO upload ──────────────────────────────────────────────────────────────


def upload_report(report: dict) -> None:
    try:
        from minio import Minio

        mc = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=False,
        )
        bucket = "evals"
        if not mc.bucket_exists(bucket):
            mc.make_bucket(bucket)
        content = json.dumps(report, indent=2).encode()
        mc.put_object(
            bucket,
            "eval_rag_report.json",
            io.BytesIO(content),
            length=len(content),
            content_type="application/json",
        )
        print(f"[MinIO] Uploaded to s3://{bucket}/eval_rag_report.json")
    except Exception as exc:
        print(f"[WARN] MinIO upload failed: {exc}")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    print("=" * 60)
    print("RAG EVAL")
    print("=" * 60)

    if not GOLDEN.exists():
        print(f"[ERROR] {GOLDEN} not found — run from repo root")
        return 2

    triples: list[dict] = json.loads(GOLDEN.read_text())
    print(f"Golden set: {len(triples)} triples")

    if not THRESHOLDS_FILE.exists():
        print(f"[ERROR] {THRESHOLDS_FILE} not found")
        return 2
    raw_thresholds = yaml.safe_load(THRESHOLDS_FILE.read_text())
    rag_thresholds = raw_thresholds.get("ragas", {})
    ret_thresholds = raw_thresholds.get("retrieval", {})

    # ── Auth + conversation ───────────────────────────────────────────────────
    print("\n[Setup] Authenticating eval user ...")
    try:
        token = _ensure_user()
        conversation_id = _create_conversation(token)
        print(f"  conversation_id: {conversation_id}")
    except Exception as exc:
        print(f"[ERROR] Auth/conversation setup: {exc}")
        return 2

    # ── Retrieve + answer ─────────────────────────────────────────────────────
    print(f"\n[Retrieval] Running {len(triples)} queries ...")
    results: list[dict] = []

    for idx, triple in enumerate(triples):
        qid = triple["id"]
        question = triple["question"]
        gts = triple["ground_truth_chunks"]
        print(f"  [{idx + 1:02d}/{len(triples)}] #{qid} ...", end="\r", flush=True)

        try:
            retrieved = retrieve(question, conversation_id, token, top_k=10)
        except Exception as exc:
            print(f"\n  [WARN] Retrieval failed for #{qid}: {exc}")
            retrieved = []

        answer = ""
        if OPENAI_API_KEY:
            try:
                answer = generate_answer(question, retrieved)
            except Exception as exc:
                print(f"\n  [WARN] Answer generation failed for #{qid}: {exc}")

        hit5 = _hit(retrieved[:5], gts)
        results.append(
            {
                "id": qid,
                "question": question,
                "retrieved": retrieved,
                "answer": answer,
                "ground_truth_chunks": gts,
                "hit5": hit5,
                "hand_labeled": triple.get("hand_labeled", False),
            }
        )

    print()

    # ── Retrieval metrics ─────────────────────────────────────────────────────
    hit_at_5, mrr_at_10 = compute_retrieval_metrics(results)
    print("\n── Retrieval metrics ──")
    print(f"  hit@5:   {hit_at_5:.4f}")
    print(f"  MRR@10:  {mrr_at_10:.4f}")

    # ── RAGAS ─────────────────────────────────────────────────────────────────
    print("\n── RAGAS ──")
    ragas_scores = run_ragas(results)
    if ragas_scores:
        for metric, score in ragas_scores.items():
            print(f"  {metric}: {score:.4f}")
    else:
        print("  (skipped)")

    # ── Judge agreement ───────────────────────────────────────────────────────
    agreement = compute_judge_agreement(results)
    if agreement is not None:
        hand_n = sum(1 for r in results if r.get("hand_labeled"))
        print(f"\n── Judge agreement (n={hand_n} hand-labeled) ──")
        print(f"  hit@5 agreement: {agreement:.4f}")

    # ── Threshold checks ──────────────────────────────────────────────────────
    checks = [
        ("hit@5", hit_at_5, ret_thresholds.get("hit_at_5", 0.70)),
        ("MRR@10", mrr_at_10, ret_thresholds.get("mrr_at_10", 0.50)),
    ]
    if ragas_scores:
        checks += [
            (
                "RAGAS faithfulness",
                ragas_scores["faithfulness"],
                rag_thresholds.get("faithfulness", 0.70),
            ),
            (
                "RAGAS answer_relevancy",
                ragas_scores["answer_relevancy"],
                rag_thresholds.get("answer_relevancy", 0.70),
            ),
            (
                "RAGAS context_precision",
                ragas_scores["context_precision"],
                rag_thresholds.get("context_precision", 0.65),
            ),
        ]

    failures: list[str] = []
    print("\n── Threshold checks ──")
    for name, actual, threshold in checks:
        status = "✓ PASS" if actual >= threshold else "✗ FAIL"
        print(f"  {status}  {name}: {actual:.4f} (threshold {threshold})")
        if actual < threshold:
            failures.append(f"{name}: {actual:.4f} < {threshold}")

    # ── Report ────────────────────────────────────────────────────────────────
    report: dict = {
        "eval": "rag",
        "n_triples": len(triples),
        "retrieval": {
            "hit_at_5": hit_at_5,
            "mrr_at_10": mrr_at_10,
        },
        "ragas": ragas_scores,
        "judge_agreement": agreement,
        "thresholds": {"ragas": rag_thresholds, "retrieval": ret_thresholds},
        "failures": failures,
        "passed": len(failures) == 0,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print(f"\n[LOCAL] Saved to {REPORT_PATH.name}")
    upload_report(report)

    print("\n" + "=" * 60)
    if failures:
        print(f"RESULT: FAILED — {len(failures)} threshold(s) breached")
        for f in failures:
            print(f"  ✗ {f}")
        print("=" * 60)
        return 1

    print("RESULT: PASSED — all thresholds met")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
