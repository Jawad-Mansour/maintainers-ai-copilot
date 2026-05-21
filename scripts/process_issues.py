#!/usr/bin/env python3
"""
Preprocess pandas-dev/pandas closed issues for classification and RAG.

Input:  data/pandas_closed_issues.jsonl  (from download_issues.py)
Output:
  data/train.jsonl          — 70% oldest issues, labelled
  data/val.jsonl            — 15% middle, labelled
  data/test.jsonl           — 15% newest, labelled
  data/rag_corpus.jsonl     — all cleaned issues regardless of label
  data/processing_report.json

Usage:
    python scripts/process_issues.py
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

try:
    from bs4 import BeautifulSoup

    _BS4 = True
except ImportError:
    _BS4 = False

# ── Label mapping (must match the Colab notebook exactly) ────────────────────
# Enhancement + Ideas both → "feature" (Ideas has only ~75 issues — too few for a separate class)
LABEL_MAP: dict[str, str] = {
    "Bug": "bug",
    "Enhancement": "feature",
    "Ideas": "feature",
    "Docs": "docs",
    "Usage Question": "question",
}
OUR_LABELS = set(LABEL_MAP.keys())  # the 5 GitHub labels we care about
CLASSES = ["bug", "docs", "feature", "question"]  # sorted alphabetically

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
INPUT = ROOT / "data" / "pandas_closed_issues.jsonl"
OUT = ROOT / "data"

# ── Text cleaning ─────────────────────────────────────────────────────────────
_CODE_FENCE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_HTML_TAG = re.compile(r"<[^>]+>")
_URL = re.compile(r"https?://\S+")
_WHITESPACE = re.compile(r"\s+")
MAX_CHARS = 2000  # truncate body to this before concatenating with title


def _strip_html(text: str) -> str:
    if not text:
        return ""
    if _BS4:
        return BeautifulSoup(text, "html.parser").get_text(separator=" ")
    return _HTML_TAG.sub(" ", text)


def clean_text(title: str, body: str | None) -> str:
    """Return cleaned concatenation of title + body."""
    title = _strip_html(title or "")
    body = _strip_html(body or "")

    # Keep code blocks (they carry diagnostic signal) but clean their delimiters
    body = _CODE_FENCE.sub(" CODE_BLOCK ", body)
    body = _INLINE_CODE.sub(" CODE ", body)
    body = _URL.sub(" URL ", body)

    # Truncate body before concatenating (title always kept in full)
    body = body[:MAX_CHARS]

    text = f"{title} {body}"
    text = _WHITESPACE.sub(" ", text).strip()
    return text


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()


# ── Processing ────────────────────────────────────────────────────────────────


def load_raw(path: Path) -> list[dict]:
    issues = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                with contextlib.suppress(json.JSONDecodeError):
                    issues.append(json.loads(line))
    return issues


def count_our_labels(issue: dict) -> list[str]:
    """Return list of OUR_LABELS that appear on this issue."""
    return [lb["name"] for lb in issue.get("labels", []) if lb["name"] in OUR_LABELS]


def process(issues: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """
    Apply 7-step pipeline.  Returns (labelled, rag_corpus, stats).

    Steps:
    1. Skip PRs (already skipped by downloader, defensive check)
    2. Drop dual-labelled: issues with 2+ of OUR 5 labels (contradictory ground truth)
    3. Keep only issues with exactly 1 of our labels for classification
    4. Strip HTML, normalize whitespace, truncate body
    5. Drop if cleaned text < 10 chars (empty issues)
    6. MD5 dedup on cleaned text
    7. Sort by created_at for temporal split
    """
    stats: dict[str, int] = {
        "total_raw": len(issues),
        "prs_skipped": 0,
        "dual_label": 0,
        "no_our_label": 0,
        "too_short": 0,
        "duplicate": 0,
        "kept": 0,
    }

    rag_corpus: list[dict] = []
    labelled: list[dict] = []
    seen_hashes: set[str] = set()

    for issue in issues:
        # 1. Skip PRs
        if "pull_request" in issue:
            stats["prs_skipped"] += 1
            continue

        # 4. Clean text (for ALL issues — RAG corpus)
        text = clean_text(issue.get("title", ""), issue.get("body"))

        # 5. Drop empty
        if len(text) < 10:
            stats["too_short"] += 1
            continue

        # 6. Dedup
        h = _md5(text)
        if h in seen_hashes:
            stats["duplicate"] += 1
            continue
        seen_hashes.add(h)

        rag_record = {
            "number": issue["number"],
            "text": text,
            "created_at": issue.get("created_at", ""),
            "html_url": issue.get("html_url", ""),
        }
        rag_corpus.append(rag_record)

        # 2. Count our labels
        our = count_our_labels(issue)

        if len(our) == 0:
            stats["no_our_label"] += 1
            continue

        if len(our) > 1:
            stats["dual_label"] += 1
            continue

        # 3. Map to class
        label = LABEL_MAP[our[0]]
        labelled.append(
            {
                "number": issue["number"],
                "text": text,
                "label": label,
                "created_at": issue.get("created_at", ""),
            }
        )

    # 7. Sort by created_at (temporal split later)
    labelled.sort(key=lambda x: x["created_at"])
    rag_corpus.sort(key=lambda x: x["created_at"])

    stats["kept"] = len(labelled)
    return labelled, rag_corpus, stats


def temporal_split(
    records: list[dict],
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
) -> tuple[list[dict], list[dict], list[dict]]:
    n = len(records)
    train_end = int(n * train_ratio)
    val_end = int(n * (train_ratio + val_ratio))
    return records[:train_end], records[train_end:val_end], records[val_end:]


def save_jsonl(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    if not INPUT.exists():
        print(f"[ERROR] {INPUT} not found. Run scripts/download_issues.py first.")
        return 1

    print(f"[LOAD] {INPUT}")
    raw = load_raw(INPUT)
    print(f"[LOAD] {len(raw):,} raw records")

    labelled, rag_corpus, stats = process(raw)

    train, val, test = temporal_split(labelled)

    # Save splits
    save_jsonl(train, OUT / "train.jsonl")
    save_jsonl(val, OUT / "val.jsonl")
    save_jsonl(test, OUT / "test.jsonl")
    save_jsonl(rag_corpus, OUT / "rag_corpus.jsonl")

    # Report
    report = {
        "pipeline_stats": stats,
        "splits": {
            "train": len(train),
            "val": len(val),
            "test": len(test),
        },
        "rag_corpus": len(rag_corpus),
        "class_distribution": {
            split_name: dict(Counter(r["label"] for r in split))
            for split_name, split in [("train", train), ("val", val), ("test", test)]
        },
        "date_ranges": {
            "train": {
                "first": train[0]["created_at"][:10] if train else None,
                "last": train[-1]["created_at"][:10] if train else None,
            },
            "val": {
                "first": val[0]["created_at"][:10] if val else None,
                "last": val[-1]["created_at"][:10] if val else None,
            },
            "test": {
                "first": test[0]["created_at"][:10] if test else None,
                "last": test[-1]["created_at"][:10] if test else None,
            },
        },
    }
    (OUT / "processing_report.json").write_text(json.dumps(report, indent=2))

    # Console summary
    print()
    print("=" * 60)
    print("PROCESSING SUMMARY")
    print("=" * 60)
    print(f"  Total raw:            {stats['total_raw']:>7,}")
    print(f"  PRs skipped:          {stats['prs_skipped']:>7,}")
    print(f"  Dual-labelled (drop): {stats['dual_label']:>7,}")
    print(f"  No matching label:    {stats['no_our_label']:>7,}")
    print(f"  Too short (drop):     {stats['too_short']:>7,}")
    print(f"  Duplicates (drop):    {stats['duplicate']:>7,}")
    print(f"  Kept (labelled):      {stats['kept']:>7,}")
    print()
    t0, t1 = train[0]["created_at"][:10], train[-1]["created_at"][:10]
    v0, v1 = val[0]["created_at"][:10], val[-1]["created_at"][:10]
    e0, e1 = test[0]["created_at"][:10], test[-1]["created_at"][:10]
    print(f"  Train:  {len(train):>6,}  ({t0} → {t1})")
    print(f"  Val:    {len(val):>6,}  ({v0} → {v1})")
    print(f"  Test:   {len(test):>6,}  ({e0} → {e1})")
    print(f"  RAG corpus: {len(rag_corpus):,} issues (all labels)")
    print()
    print("  Class distribution (train):")
    for cls in CLASSES:
        c = report["class_distribution"]["train"].get(cls, 0)
        pct = c / len(train) * 100 if train else 0
        print(f"    {cls:<10}: {c:>5,}  ({pct:.1f}%)")
    print()
    print("  Output files:")
    print(f"    data/train.jsonl     ({len(train):,} records)")
    print(f"    data/val.jsonl       ({len(val):,} records)")
    print(f"    data/test.jsonl      ({len(test):,} records)")
    print(f"    data/rag_corpus.jsonl({len(rag_corpus):,} records)")
    print()
    print("  Next: upload train.jsonl, val.jsonl, test.jsonl to Colab")
    print("  and run the training notebook.")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
