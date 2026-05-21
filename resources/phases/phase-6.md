# Phase 6 — Evals + CI

**Status:** ✅ Done (2026-05-21)

---

## 6-A: Eval Scripts

### evals/run_classification_eval.py

Evaluates all 3 classifiers against `evals/golden_classification.json` (25 issues).

**Models tested:**
- DistilBERT (`POST /classify` — batch, modelserver)
- TF-IDF + LR (`POST /classify/classical` — per-issue, modelserver)
- GPT-4o-mini zero-shot (skipped if `OPENAI_API_KEY` absent)

**Metrics:** macro-F1, per-class F1, confusion matrix (sklearn)

**Threshold keys (eval_thresholds.yaml `classifier:`):**
- `dl_f1` — DistilBERT macro-F1
- `classical_f1` — TF-IDF+LR macro-F1
- `llm_f1` — GPT-4o-mini macro-F1
- `f1_macro` — deployed model macro-F1 (same as dl_f1)

**Output:** `eval_classification_report.json` (local) + `s3://evals/eval_classification_report.json` (MinIO)

**Exit codes:** 0=pass, 1=regression, 2=runtime error

---

### evals/run_rag_eval.py

Evaluates retrieval quality against `evals/golden_rag.json` (25 triples).

**Steps:**
1. Register/login eval user → create conversation (required by SearchRequest)
2. For each question: `POST /rag/search` → retrieve top-10 chunks
3. Generate answer via GPT-4o-mini using top-5 contexts (if `OPENAI_API_KEY` set)
4. Compute hit@5, MRR@10 from ground_truth_chunks phrase matching
5. Run RAGAS (faithfulness, answer_relevancy, context_precision) if ragas installed
6. Compute judge agreement on 5 hand-labeled triples (IDs: 1, 5, 10, 15, 20)

**Threshold keys (eval_thresholds.yaml):**
- `retrieval.hit_at_5` = 0.70
- `retrieval.mrr_at_10` = 0.50
- `ragas.faithfulness` = 0.70
- `ragas.answer_relevancy` = 0.70
- `ragas.context_precision` = 0.65

**Output:** `eval_rag_report.json` (local) + `s3://evals/eval_rag_report.json` (MinIO)

**Exit codes:** 0=pass, 1=regression, 2=runtime error

---

### evals/requirements.txt

Eval-specific deps: `ragas`, `datasets`, `openai`, `scikit-learn`, `minio`, `pyyaml`, `requests`

---

## 6-B: CI Workflow

**File:** `.github/workflows/ci.yml`

**Jobs (in order, with dependencies):**

| Job | Trigger | Description |
|-----|---------|-------------|
| `lint` | all pushes/PRs | `ruff check` + `ruff format --check` |
| `typecheck` | all pushes/PRs | `mypy api/` |
| `unit-tests` | all pushes/PRs | pytest (skips DB + modelserver tests) |
| `redaction` | all pushes/PRs | `pytest tests/test_phase2_redaction.py` |
| `build-images` | all pushes/PRs | `docker build` for api + modelserver |
| `eval` | push to main only | Full stack eval (classification + RAG) |

**Eval job details:**
- `docker compose up -d --wait` (spins full stack)
- Seeds corpus via `scripts/seed_rag_corpus.py`
- Promotes eval user to admin via `docker compose exec db psql`
- Runs both eval scripts
- Uploads `eval_classification_report.json` + `eval_rag_report.json` as artifacts
- Tears down with `docker compose down -v`

**Secrets required (GitHub repo settings):**
- `OPENAI_API_KEY` — for LLM baseline + RAGAS + answer generation

---

## 6-C: Golden Sets (manual curation)

### evals/golden_classification.json — 25 issues

Distribution: 7 bug / 7 feature / 6 docs / 5 question

All from pandas-dev/pandas domain (synthetic, not from training split).

Hand-label accuracy: each label was verified against class definitions:
- `bug` — defect/incorrect behavior
- `feature` — new functionality or enhancement
- `docs` — documentation, docstrings, examples
- `question` — usage question, wants to understand

### evals/golden_rag.json — 25 triples

Format: `{id, question, ideal_answer, ground_truth_chunks: [str], hand_labeled: bool}`

`hand_labeled: true` on IDs 1, 5, 10, 15, 20 (5 entries for RAGAS judge agreement).

Questions represent real maintainer workflows:
- Issue triage and reproduction patterns
- API behavior edge cases
- Performance and memory questions
- Migration guidance
- Configuration and dtype questions

---

## Corpus Seeding

**scripts/seed_rag_corpus.py** — seeds eval corpus for CI:
1. Registers `eval@example.com` (idempotent)
2. Ingests each triple's `ideal_answer` as a document via `POST /rag/ingest`
3. Falls back with clear error message if user lacks admin role

To promote user manually:
```sql
docker compose exec db psql -U copilot -d copilot_db
UPDATE users SET role='admin' WHERE email='eval@example.com';
```
