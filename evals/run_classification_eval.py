#!/usr/bin/env python3
"""Classification eval — runs all 3 models against evals/golden_classification.json.

Metrics per model: macro-F1, per-class F1, confusion matrix.
Thresholds from eval_thresholds.yaml. Report written locally + uploaded to MinIO.

Usage:
    python evals/run_classification_eval.py

Environment variables:
    MODELSERVER_URL   default: http://localhost:8001
    OPENAI_API_KEY    required for LLM baseline (skipped if absent)
    MINIO_ENDPOINT    default: localhost:9000
    MINIO_ACCESS_KEY  default: minioadmin
    MINIO_SECRET_KEY  default: minioadmin_dev

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
import time
from pathlib import Path

import requests
import yaml
from sklearn.metrics import classification_report, confusion_matrix, f1_score

ROOT = Path(__file__).parent.parent
GOLDEN = ROOT / "evals" / "golden_classification.json"
THRESHOLDS_FILE = ROOT / "eval_thresholds.yaml"
REPORT_PATH = ROOT / "eval_classification_report.json"

CLASSES = ["bug", "docs", "feature", "question"]

MODELSERVER_URL = os.environ.get("MODELSERVER_URL", "http://localhost:8001")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin_dev")

_LLM_SYSTEM = """You are a classifier for GitHub issues from the pandas-dev/pandas repository.
Classify the issue into exactly one of:
- bug: A defect or incorrect behavior in existing functionality.
- feature: A request for new functionality or an enhancement to existing behavior.
- docs: A problem with documentation, docstrings, examples, or the website.
- question: A usage question about how pandas works. The reporter wants to understand something,
  not report a defect.

Respond with ONLY valid JSON: {"label": "<one of: bug, feature, docs, question>"}"""


def _text(issue: dict) -> str:
    return f"{issue['title']} {issue['body']}"


# ── Model runners ─────────────────────────────────────────────────────────────


def run_distilbert(issues: list[dict]) -> list[str]:
    texts = [_text(i) for i in issues]
    resp = requests.post(
        f"{MODELSERVER_URL}/classify",
        json={"texts": texts},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("mode") == "mock":
        print("[WARN] modelserver is in mock mode — DistilBERT scores are placeholder values")
    return data["labels"]


def run_classical(issues: list[dict]) -> list[str]:
    labels = []
    for issue in issues:
        resp = requests.post(
            f"{MODELSERVER_URL}/classify/classical",
            json={"text": _text(issue)},
            timeout=30,
        )
        resp.raise_for_status()
        labels.append(resp.json()["label"])
    return labels


def run_llm(issues: list[dict]) -> list[str]:
    if not OPENAI_API_KEY:
        print("[WARN] OPENAI_API_KEY not set — LLM baseline skipped, returning 'bug' for all")
        return ["bug"] * len(issues)

    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    labels = []
    for i, issue in enumerate(issues):
        print(f"  LLM {i + 1}/{len(issues)} ...", end="\r", flush=True)
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _LLM_SYSTEM},
                    {"role": "user", "content": _text(issue)},
                ],
                temperature=0,
                max_tokens=20,
            )
            raw = resp.choices[0].message.content or "{}"
            label = json.loads(raw).get("label", "bug")
            if label not in CLASSES:
                label = "bug"
        except Exception as exc:
            print(f"\n  [WARN] LLM issue {issue['id']}: {exc}")
            label = "bug"
        labels.append(label)
        time.sleep(0.05)
    print()
    return labels


# ── Metrics ───────────────────────────────────────────────────────────────────


def compute_metrics(y_true: list[str], y_pred: list[str], name: str) -> dict:
    macro_f1 = round(
        float(f1_score(y_true, y_pred, labels=CLASSES, average="macro", zero_division=0)), 4
    )
    per_class = {
        cls: round(
            float(f1_score(y_true, y_pred, labels=[cls], average="micro", zero_division=0)), 4
        )
        for cls in CLASSES
    }
    cm = confusion_matrix(y_true, y_pred, labels=CLASSES).tolist()
    report_str = classification_report(y_true, y_pred, labels=CLASSES, zero_division=0)
    print(f"\n── {name} ──")
    print(f"  macro-F1: {macro_f1:.4f}")
    print(report_str)
    return {
        "model": name,
        "macro_f1": macro_f1,
        "per_class_f1": per_class,
        "confusion_matrix": cm,
        "classification_report": report_str,
    }


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
            "eval_classification_report.json",
            io.BytesIO(content),
            length=len(content),
            content_type="application/json",
        )
        print(f"[MinIO] Uploaded to s3://{bucket}/eval_classification_report.json")
    except Exception as exc:
        print(f"[WARN] MinIO upload failed: {exc}")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    print("=" * 60)
    print("CLASSIFICATION EVAL")
    print("=" * 60)

    if not GOLDEN.exists():
        print(f"[ERROR] {GOLDEN} not found — run from repo root")
        return 2

    issues: list[dict] = json.loads(GOLDEN.read_text())
    y_true = [i["label"] for i in issues]
    print(f"Golden set: {len(issues)} issues  classes: {sorted(set(y_true))}")

    if not THRESHOLDS_FILE.exists():
        print(f"[ERROR] {THRESHOLDS_FILE} not found")
        return 2
    thresholds = yaml.safe_load(THRESHOLDS_FILE.read_text())["classifier"]

    # ── Run models ────────────────────────────────────────────────────────────
    print("\n[1/3] DistilBERT (/classify) ...")
    try:
        bert_preds = run_distilbert(issues)
    except Exception as exc:
        print(f"[ERROR] DistilBERT: {exc}")
        return 2

    print("[2/3] TF-IDF + LR (/classify/classical) ...")
    try:
        classical_preds = run_classical(issues)
    except Exception as exc:
        print(f"[ERROR] Classical: {exc}")
        return 2

    print("[3/3] GPT-4o-mini zero-shot ...")
    llm_preds = run_llm(issues)

    # ── Metrics ───────────────────────────────────────────────────────────────
    bert_m = compute_metrics(y_true, bert_preds, "DistilBERT")
    classical_m = compute_metrics(y_true, classical_preds, "TF-IDF+LR")
    llm_m = compute_metrics(y_true, llm_preds, "GPT-4o-mini")

    # ── Threshold checks ──────────────────────────────────────────────────────
    checks = [
        ("DistilBERT macro-F1 (dl_f1)", bert_m["macro_f1"], thresholds["dl_f1"]),
        ("TF-IDF+LR macro-F1 (classical_f1)", classical_m["macro_f1"], thresholds["classical_f1"]),
        ("GPT-4o-mini macro-F1 (llm_f1)", llm_m["macro_f1"], thresholds["llm_f1"]),
        ("Deployed model macro-F1 (f1_macro)", bert_m["macro_f1"], thresholds["f1_macro"]),
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
        "eval": "classification",
        "n_issues": len(issues),
        "models": {
            "distilbert": bert_m,
            "classical": classical_m,
            "llm": llm_m,
        },
        "thresholds": thresholds,
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
