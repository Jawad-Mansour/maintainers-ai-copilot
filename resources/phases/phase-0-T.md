# Phase 0-T — Tooling Smoke Tests

## Goal
Verify the scaffolded repo is clean: no linting errors, no type errors, no secrets, pre-commit passes on all files.

## Tests

### 1. Ruff check
```bash
uv run ruff check .
# Expected: "All checks passed!"
```

### 2. Mypy
```bash
uv run mypy .
# Expected: "Success: no issues found in N source files"
```

### 3. Pre-commit (all files)
```bash
uv run pre-commit run --all-files
# Expected: all hooks pass
```

### 4. Secret scan
```bash
uv run pre-commit run gitleaks --all-files
# Expected: zero secrets detected
```

### 5. .env not tracked
```bash
git ls-files | grep -E "^\.env$"
# Expected: no output (empty)
```

## Pass Criteria
All 5 checks exit 0 with no errors. Only then proceed to Phase 1.
