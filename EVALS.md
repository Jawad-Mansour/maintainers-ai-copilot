# EVALS.md — Evaluation Methodology

## Overview

Two automated evaluation pipelines run against committed golden sets:

| Pipeline | Script | Golden set | Metrics |
|---|---|---|---|
| Classification | `evals/run_classification_eval.py` | `evals/golden_classification.json` | macro-F1, per-class F1 |
| RAG | `evals/run_rag_eval.py` | `evals/golden_rag.json` | hit@5, MRR@10, RAGAS faithfulness/relevancy |

All thresholds are committed in `eval_thresholds.yaml`. CI hard-fails if any metric falls below its threshold.

---

## Classification Evaluation

### Golden Set

`evals/golden_classification.json` — 25 pandas-dev/pandas issues, hand-labeled.

Each entry:
```json
{
  "title": "DataFrame.merge() raises ValueError with nullable Int64 columns",
  "body": "...",
  "expected_label": "bug"
}
```

Label distribution: 7 bug, 6 feature, 6 docs, 6 question (balanced for evaluation).

### Methodology

1. For each golden item, POST to `POST /classify` via the API (which routes to modelserver DistilBERT).
2. Compare `predicted_label` to `expected_label`.
3. Compute macro-F1, per-class precision/recall/F1 using `sklearn.metrics`.
4. Write report to `eval_classification_report.json` and upload to MinIO bucket `eval-reports/`.
5. Compare against `eval_thresholds.yaml → classifier.f1_macro`. Exit 1 if below threshold.

### Thresholds

```yaml
classifier:
  f1_macro: 0.86      # DistilBERT measured 0.8867 on test set — threshold is 2pp below
```

### Deployed Model Results (test split, 2,146 issues)

| Model | Macro-F1 | Bug F1 | Feature F1 | Docs F1 | Question F1 | Latency |
|---|---|---|---|---|---|---|
| TF-IDF + Logistic Regression | 0.8404 | 0.9521 | 0.9195 | 0.8889 | 0.5986 | 0.015ms |
| **DistilBERT fine-tune (deployed)** | **0.8867** | **0.9626** | **0.9315** | **0.9197** | **0.7330** | **470ms** |
| GPT-4o-mini zero-shot | 0.9030 | 0.9620 | 0.9263 | 0.9117 | 0.8118 | 775ms |

DistilBERT was chosen over GPT-4o-mini for zero per-call cost. See D-deploy in [DECISIONS.md](resources/DECISIONS.md).

---

## RAG Evaluation

### Golden Set

`evals/golden_rag.json` — 25 pandas-dev/pandas issues with ground-truth retrieval fragments and ideal answers.

Each entry:
```json
{
  "question": "Has this bug where groupby silently drops NaN rows been reported before?",
  "ideal_answer": "Yes — pandas groupby excludes NaN keys from all groups by default...",
  "ground_truth_chunks": [
    "pandas groupby excludes NaN keys from all groups",
    "silently dropped from the result",
    "pass dropna=False to groupby()"
  ]
}
```

**Critical implementation note:** `ground_truth_chunks` are literal substrings of corpus documents — not semantic paraphrases. The `_hit()` function uses `substring in retrieved_text` (exact match), so ground-truth strings must appear verbatim in the corpus.

### Retrieval Metrics

**hit@5:** fraction of questions where at least one `ground_truth_chunk` appears as a literal substring in the top-5 retrieved chunks' combined text.

```python
def _hit(ground_truth_chunks: list[str], retrieved_texts: list[str]) -> bool:
    combined = " ".join(retrieved_texts)
    return any(chunk in combined for chunk in ground_truth_chunks)
```

**MRR@10 (Mean Reciprocal Rank):** For each question, find the rank of the first retrieved chunk that contains a ground-truth fragment. MRR = mean of 1/rank across all questions.

### RAGAS Metrics

RAGAS uses GPT-4o-mini as a judge to score:

- **Faithfulness:** Does the answer contain only claims supported by the retrieved context? (0–1)
- **Answer relevancy:** Does the answer directly address the question? (0–1)
- **Context precision:** Are the retrieved chunks actually relevant to the question? (0–1)

RAGAS takes `(question, answer, contexts)` triples — the eval script calls `POST /chat/stream` to get both the answer and retrieved sources.

### Thresholds

```yaml
retrieval:
  hit_at_5: 0.70
  mrr_at_10: 0.50

ragas:
  faithfulness: 0.70
  answer_relevancy: 0.70
  context_precision: 0.65
```

### Measured Results (after golden set fix)

| Metric | Measured | Threshold | Status |
|---|---|---|---|
| hit@5 | 0.8400 | 0.70 | ✅ |
| MRR@10 | 0.7097 | 0.50 | ✅ |

RAGAS metrics depend on live API + corpus state at eval time.

### Judge Validation (5-item hand-label)

Five of the 25 golden items were hand-labeled independently (faithful/not-faithful, relevant/not-relevant) and compared against RAGAS scores.

| Item | Hand: faithful | RAGAS: faithful | Hand: relevant | RAGAS: relevant |
|---|---|---|---|---|
| groupby NaN | 1 | 0.89 | 1 | 0.91 |
| SettingWithCopyWarning | 1 | 0.94 | 1 | 0.88 |
| merge ValueError | 1 | 0.85 | 1 | 0.92 |
| dropna=False | 1 | 0.81 | 1 | 0.87 |
| smart_groupby ENH | 0 | 0.12 | 1 | 0.90 |

Agreement rate: 4/5 = 80% on faithfulness (threshold: hand_label=1 ↔ RAGAS > 0.70). RAGAS judge is trusted for automated CI gating.

---

## Embedding Model Comparison

Both models were evaluated on the 25-item RAG golden set at corpus ingestion time:

| Model | hit@5 | MRR@10 | Storage | Infra |
|---|---|---|---|---|
| **text-embedding-3-small (deployed)** | **0.8400** | **0.7097** | 0 (API) | same key |
| BAAI/bge-small-en-v1.5 | 0.8000 | 0.6812 | +90MB modelserver | new service |

`text-embedding-3-small` wins on both metrics with no infrastructure cost. See D7 in [DECISIONS.md](resources/DECISIONS.md).

---

## Corpus Ingestion

The RAG corpus (`data/rag_corpus.jsonl`) consists of all pandas-dev/pandas closed issues, regardless of label. All entries are ingested under `label="docs"` (collection label, not GitHub label) and chunked hierarchically:

- **Child chunks:** 256 tokens (embedded, indexed in pgvector + GIN)
- **Parent chunks:** 1024 tokens (returned to LLM when child is retrieved)

Total corpus: ~50K chunks. HNSW index query latency: <10ms.

---

## CI Integration

`.github/workflows/eval.yml` runs on every push to `main`:

1. Start services via `docker compose up -d`
2. Wait for healthchecks
3. Run `python scripts/bulk_ingest_corpus.py` (if chunk_count == 0)
4. Run `python evals/run_classification_eval.py` — exit 1 on threshold failure
5. Run `python evals/run_rag_eval.py` — exit 1 on threshold failure
6. Upload reports to MinIO `eval-reports/` bucket
7. Post metric summary as PR comment

Thresholds in `eval_thresholds.yaml` are the single source of truth for pass/fail.
