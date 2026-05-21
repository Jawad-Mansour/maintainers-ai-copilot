#!/usr/bin/env python3
"""
Download closed issues from pandas-dev/pandas filtered to our 5 labels.

Fetches each label separately, deduplicates by issue number, saves to
data/pandas_closed_issues.jsonl (one JSON object per line, labels field kept).

Usage:
    python scripts/download_issues.py

Reads GITHUB_TOKEN from .env or environment.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

OWNER = "pandas-dev"
REPO = "pandas"
OUR_LABELS = ["Bug", "Enhancement", "Ideas", "Docs", "Usage Question"]
OUT = Path(__file__).parent.parent / "data" / "pandas_closed_issues.jsonl"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "maintainers-copilot-downloader",
    }


def _get(session: requests.Session, url: str, retries: int = 3) -> requests.Response | None:
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=15)
        except requests.RequestException as exc:
            print(f"  [ERROR] {exc}")
            time.sleep(2**attempt)
            continue

        remaining = int(resp.headers.get("X-RateLimit-Remaining", -1))
        reset_at = int(resp.headers.get("X-RateLimit-Reset", 0))

        if resp.status_code == 200:
            if remaining < 10 and reset_at:
                wait = max(0, reset_at - int(time.time())) + 1
                print(f"  [RATE LIMIT] {remaining} remaining — sleeping {wait}s")
                time.sleep(wait)
            return resp

        if resp.status_code in (403, 429):
            wait = max(60, reset_at - int(time.time()) + 1) if reset_at else 60
            print(f"  [RATE LIMIT] HTTP {resp.status_code} — sleeping {wait}s")
            time.sleep(wait)
            continue

        print(f"  [ERROR] HTTP {resp.status_code}: {resp.reason}")
        if attempt < retries - 1:
            time.sleep(2**attempt)

    return None


def _next_url(resp: requests.Response) -> str | None:
    for part in resp.headers.get("Link", "").split(","):
        parts = part.split(";")
        if len(parts) == 2 and 'rel="next"' in parts[1]:
            return parts[0].strip()[1:-1]
    return None


def _extract(issue: dict) -> dict:
    return {
        "number": issue["number"],
        "title": issue["title"],
        "body": issue.get("body"),
        "state": issue["state"],
        "created_at": issue["created_at"],
        "closed_at": issue["closed_at"],
        "labels": [{"name": lb["name"]} for lb in issue.get("labels", [])],
        "html_url": issue["html_url"],
    }


def fetch_label(session: requests.Session, label: str) -> dict[int, dict]:
    """Fetch all closed issues with this label. Returns {number: record}."""
    url: str | None = (
        f"https://api.github.com/repos/{OWNER}/{REPO}/issues"
        f"?state=closed&labels={requests.utils.quote(label)}"
        f"&sort=created&direction=asc&per_page=100"
    )
    results: dict[int, dict] = {}
    page = 1

    while url:
        resp = _get(session, url)
        if resp is None:
            print(f"  [ERROR] Failed fetching page {page} for label '{label}'")
            break

        for issue in resp.json():
            if "pull_request" in issue:  # skip PRs
                continue
            rec = _extract(issue)
            results[rec["number"]] = rec  # dedup by number

        print(f"  page {page:>3} — {len(results):>5} issues so far", end="\r")
        page += 1
        time.sleep(0.05)
        url = _next_url(resp)

    print()  # newline after \r
    return results


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print(
            "[ERROR] GITHUB_TOKEN not set.\n"
            "  Git Bash:   export GITHUB_TOKEN='ghp_...'\n"
            "  PowerShell: $env:GITHUB_TOKEN='ghp_...'\n"
            "  Or add it to .env",
            file=sys.stderr,
        )
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(_headers(token))

    # Fetch each label separately, merge by issue number
    all_issues: dict[int, dict] = {}

    for label in OUR_LABELS:
        print(f"[FETCH] label='{label}' ...")
        found = fetch_label(session, label)
        new = {n: r for n, r in found.items() if n not in all_issues}
        all_issues.update(found)
        print(
            f"  -> {len(found):,} issues  ({len(new):,} new, {len(found) - len(new)} already seen)"
        )

    # Sort by created_at (process_issues.py does the split, but consistent order helps)
    sorted_issues = sorted(all_issues.values(), key=lambda r: r["created_at"])

    with open(OUT, "w", encoding="utf-8") as fh:
        for rec in sorted_issues:
            fh.write(json.dumps(rec) + "\n")

    print(f"\n[DONE] {len(sorted_issues):,} unique issues saved to {OUT}")
    print("Next: python scripts/process_issues.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
