# Phase 0-T — Tooling Smoke Tests

**Status:** ✅ All passed (2026-05-20)

---

## Goal

Verify the scaffolded repo is completely clean before writing any application code:
no lint errors, no type errors, no secrets in history, pre-commit passes on every file.

A failure here means the foundation is broken. Every later phase builds on top of this.

---

## Tests & Results

### 1. Ruff lint
```bash
uv run ruff check .
```
**Result:** `All checks passed!`
Checks: style errors (E), undefined names (F), import order (I),
modern syntax (UP), bug patterns (B), unnecessary complexity (SIM).

### 2. Mypy type check
```bash
uv run mypy .
```
**Result:** `Success: no issues found in 11 source files`
Runs in strict mode. Excludes service dirs (chatbot/, modelserver/, widget/, host/)
to avoid cross-service module name collisions — each service runs mypy independently.

### 3. Pre-commit (all files)
```bash
uv run pre-commit run --all-files
```
**Result:** All 10 hooks passed

| Hook | Result |
|------|--------|
| ruff | Passed |
| ruff-format | Passed |
| mypy | Passed |
| gitleaks | Passed |
| trailing-whitespace | Passed (auto-fixed PLAN.md on first run) |
| end-of-file-fixer | Passed (auto-fixed several spec-kit files on first run) |
| check-yaml | Passed |
| check-json | Passed |
| check-merge-conflict | Passed |
| detect-private-key | Passed |

**Note:** On the first commit attempt, `trailing-whitespace` and `end-of-file-fixer`
auto-modified files (PLAN.md, spec-kit workflow files). This is expected — the hooks
fix the files in place, then the commit fails so you re-stage and re-commit. Second
commit passed cleanly.

### 4. Secret scan
```bash
uv run pre-commit run gitleaks --all-files
```
**Result:** `Detect hardcoded secrets...Passed` — zero secrets detected in any file.

### 5. .env not tracked
```bash
git ls-files | grep -E "^\.env$"
```
**Result:** No output — `.env` is correctly excluded by `.gitignore`.

### 6. Git log clean
```bash
git log --oneline
```
**Result:**
```
cfa1eda chore: fix pyproject.toml dep duplication, pre-commit versions, gitignore cleanup
f540113 feat: phase 0 — repo scaffold, tooling, layered directory structure
d2414dc Initial commit from Specify template
```

### 7. Remote connected
```bash
git remote -v
```
**Result:**
```
origin  https://github.com/Jawad-Mansour/maintainers-ai-copilot.git (fetch)
origin  https://github.com/Jawad-Mansour/maintainers-ai-copilot.git (push)
```

---

## Issues Hit During Testing

| Problem | What Happened | Resolution |
|---------|--------------|------------|
| mypy `Duplicate module named "app"` | `chatbot/app.py` + `modelserver/app/__init__.py` both resolved to module `app` | Renamed `chatbot/app.py` → `chatbot/main.py` |
| `trailing-whitespace` failed on first commit | Hook auto-fixed files, then commit aborted | Re-staged and re-committed — passed on second attempt |
| `end-of-file-fixer` failed on first commit | Same as above — spec-kit files had no trailing newline | Same resolution |

---

## Pass Criteria — All Met ✅

- [x] `ruff check .` exits 0
- [x] `mypy .` exits 0
- [x] All 10 pre-commit hooks pass
- [x] `gitleaks` detects zero secrets
- [x] `.env` not in `git ls-files`
- [x] 3 clean commits on `main`, pushed to GitHub

**Phase 0-T passed. Cleared to proceed to Phase 1.**
