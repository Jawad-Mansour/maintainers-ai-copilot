# Phase 7 — ML/DL: Training + Real Modelserver

**Status:** ✅ Complete (2026-05-21)
**Tests:** 13/13 passing (test_phase7_modelserver.py)

---

## Goal

Replace the stub modelserver (mock responses) with a production-grade inference server
that loads real trained weights from MinIO. Train three classifiers on pandas-dev/pandas
issues, pick the best zero-cost model to deploy, and add a boot guard so the API can
optionally refuse to start if modelserver is still in mock mode.

---

## Sub-phases

### 7-A: Training Notebook (`notebooks/train_classifier.ipynb`)

Run on **Colab T4** (free GPU). Not part of the Docker stack — outputs are uploaded to
MinIO and the modelserver loads them at boot.

**Dataset:** pandas-dev/pandas closed issues (14,869 after preprocessing)
- Download: `scripts/download_issues.py` (GitHub API, 5 labels)
- Preprocess: `scripts/process_issues.py` (7-step pipeline → train/val/test JSONL)
- Split: 70% train / 15% val / 15% test — **temporal** (oldest→newest, no data leakage)

**7-step preprocessing pipeline:**
1. Skip PRs (defensive — downloader already filters)
2. Drop dual-labelled issues (contradictory ground truth)
3. Keep issues with exactly 1 of our 5 labels
4. Strip HTML, normalize whitespace, truncate body to 2000 chars
5. Drop if cleaned text < 10 chars
6. MD5 dedup on cleaned text
7. Sort by `created_at` for temporal split

**Label mapping:**

| GitHub label | Class |
|---|---|
| Bug | bug |
| Enhancement, Ideas | feature |
| Docs | docs |
| Usage Question | question |

**Three models trained and compared:**

| Model | Macro-F1 | Latency | Cost/call | Status |
|---|---|---|---|---|
| TF-IDF + Logistic Regression | 0.8404 | ~0.015ms | $0 | Baseline |
| DistilBERT fine-tune | **0.8867** | ~470ms | $0 | **DEPLOYED** |
| GPT-4o-mini zero-shot | 0.9030 | ~775ms | $7.8e-05 | Reference only |

**Why DistilBERT over GPT-4o-mini:** 0.8867 vs 0.9030 — a 1.6% gap. GPT-4o-mini costs
$7.8e-05/call which adds up at scale and introduces external API dependency for inference.
DistilBERT runs in-container at zero marginal cost. See D-deploy in DECISIONS.md.

**DistilBERT training config:**
- Base model: `distilbert-base-uncased` (66M params)
- Optimizer: AdamW lr=2e-5, weight_decay=0.01
- Epochs: 3, batch=16, warmup_ratio=0.10
- Class weights: inverse-frequency normalized (mean=1) to handle imbalance
- Logged to: Weights & Biases (key in Colab secrets — never in repo)

**Artifacts saved to MinIO `models` bucket:**
```
distilbert/model_card.json       — metadata + SHA-256 checksums
distilbert/config.json
distilbert/model.safetensors     — fine-tuned weights
distilbert/tokenizer.json
distilbert/tokenizer_config.json
distilbert/vocab.txt
classical/tfidf_vectorizer.pkl
classical/lr_model.pkl
plots/confusion_matrices.png
```

**Training data SHA-256:** `59c6b6e2b336a01f59291c00071366b48d434812c3e7b337c9374e5f3adef71b`
**Weights SHA-256:** `e23b2bc3f2c50b0cc6491c57d4868bca61e0942824b070d0e1fca08e06a50e0c`

---

### 7-B: Real Modelserver (`modelserver/app/`)

Five new modules added to the modelserver, each handling one inference concern:

#### `app/vault.py` — Secrets Fetcher

Fetches MinIO credentials and OpenAI key from Vault at boot:
```python
@dataclass
class ModelServerSecrets:
    minio_url: str
    minio_access_key: str
    minio_secret_key: str
    openai_api_key: str
```
Reads from `/secret/data/minio` and `/secret/data/openai` KV paths.

#### `app/weights.py` — MinIO Download + SHA-256 Verify

Boot policy:
- Weights not in MinIO → `WeightsNotFound` → caller starts in **mock mode** (safe fallback)
- Weights present but SHA-256 mismatch → `RuntimeError` → **refuse to boot** (data integrity)
- Weights valid → returns parsed `model_card` dict

Downloads all artifacts from `models` bucket into `/tmp/weights/`.

#### `app/classifier.py` — DistilBERT Inference

```python
class Classifier:
    def predict(self, text: str) -> tuple[str, float]:
        # Returns (label, confidence)
```
- Loads from `/tmp/weights/distilbert_weights/`
- `DistilBertTokenizerFast` + `DistilBertForSequenceClassification`
- Runs `torch.no_grad()`, softmax, argmax
- `id2label` mapping from model card (falls back to model config)

#### `app/classical.py` — TF-IDF + Logistic Regression

```python
class ClassicalClassifier:
    def predict(self, text: str) -> tuple[str, float]:
```
- Loads `tfidf_vectorizer.pkl` + `lr_model.pkl` from `/tmp/weights/`
- Used by `/classify/classical` endpoint (eval only — not in the main pipeline)

#### `app/reranker.py` — Cross-Encoder Reranker

```python
class Reranker:
    def rerank(self, query: str, passages: list[str]) -> list[float]:
```
- Model: `cross-encoder/ms-marco-MiniLM-L-6-v2`
- Returns one score per passage (not sorted — caller decides cutoff)
- Used by the API's `rag_service` to rerank top-20 → top-5

#### `app/ner.py` — spaCy EntityRuler NER

```python
class NERPipeline:
    def extract(self, text: str) -> list[dict[str, str]]:
        # Returns [{"text": "pandas", "label": "PACKAGE"}, ...]
```
- `spacy.blank("en")` — no statistical model, zero GPU requirement
- 5 entity types via deterministic `EntityRuler` patterns:

| Label | Pattern | Example |
|---|---|---|
| VERSION | `^\d+\.\d+(\.\d+)?(\.dev\d+\|[ab]\d+\|rc\d+)?$` | `1.5.2`, `2.0.0rc1` |
| PACKAGE | Phrase list (44 known packages) | `pandas`, `torch` |
| EXCEPTION | `^[A-Z]\w*(Error\|Exception\|Warning)$` | `ValueError`, `KeyError` |
| FILEPATH | `^[\w/\\]+\.py$` | `pandas/core/frame.py` |
| FUNCTION | `^\w+\.\w+\($` | `DataFrame.merge(` |

Text capped at 2000 chars for latency. Deduplication by `(text, label)` pair.

#### `app/summarizer.py` — GPT-4o-mini Summarizer

```python
class Summarizer:
    def summarize(self, thread: str) -> str:
```
- Single OpenAI call with the `prompts/summarize.md` template
- Used by the `summarize_thread` tool in the chat agent loop

#### `modelserver/main.py` — FastAPI with Mock Fallback

Boot sequence (FastAPI lifespan):
```
1. Fetch secrets from Vault
2. download_and_verify(mc)  ← WeightsNotFound? → mock mode
3. If real weights: load Classifier, ClassicalClassifier, Reranker, NERPipeline, Summarizer
4. Set _mode = "real" | "mock"
```

**Endpoints:**

| Method | Path | Input | Output |
|---|---|---|---|
| GET | /health | — | `{"status": "ok", "mode": "real\|mock"}` |
| POST | /classify | `{"texts": [str]}` | `{"labels": [str], "confidences": [float], "mode": str}` |
| POST | /classify/classical | `{"text": str}` | `{"label": str, "confidence": float, "mode": str}` |
| POST | /rerank | `{"query": str, "passages": [str]}` | `{"scores": [float], "mode": str}` |
| POST | /ner | `{"text": str}` | `{"entities": [{"text", "label"}], "mode": str}` |
| POST | /summarize | `{"thread": str}` | `{"summary": str, "mode": str}` |

Mock mode returns placeholder responses — useful when weights haven't been trained yet.

---

### 7-C: API Boot Guard

New env var: `REQUIRE_REAL_MODELSERVER` (default: `false`)

```python
# api/config.py
require_real_modelserver: bool = Field(default=False)
```

When `true`, the API calls `GET /modelserver/health` at startup. If `mode != "real"`,
it logs a warning but **does not exit** (soft guard). This env var is designed for
production deployments where mock mode should never run.

---

## 5-FIX: Bug Fixes Applied in This Phase

Four bugs found during Phase 7 integration testing:

### Fix 1 — CORS wildcard + credentials

**Problem:** `CORSMiddleware(allow_origins=["*"], allow_credentials=True)` is rejected
by all browsers — spec forbids wildcards with credentials.

**Fix (`api/main.py`):**
```python
_cors_origins = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:8501,http://localhost:5173,http://localhost:3001,http://localhost:3000",
    ).split(",")
    if o.strip()
]
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_credentials=True, ...)
```
`CORS_ORIGINS` env var in docker-compose allows production override without code change.

### Fix 2 — widget.js iframe src

**Problem:** iframe pointed to `/chat-widget?widget_id=...` (relative path, no host) —
browsers can't resolve this from an external website.

**Fix (`api/app/api/routes/widgets.py`):**
```python
_WIDGET_BASE_URL = os.environ.get("WIDGET_BASE_URL", "http://localhost:5173")
iframe.src = f"{_WIDGET_BASE_URL}/?widget_id={widget_id}"
```

### Fix 3 — NER missing patterns

**Problem:** `ner.py` only had VERSION, PACKAGE, EXCEPTION. FILEPATH and FUNCTION were
missing from the EntityRuler patterns (assignment requires all 5).

**Fix:** Added two patterns to `_TOKEN_PATTERNS` in `modelserver/app/ner.py`.

### Fix 4 — docker-compose depends_on ordering

**Problem:** `modelserver` could start before Vault/MinIO were initialized (one-shot jobs
might not have run yet), causing connection failures at boot.

**Fix (`docker-compose.yml`):**
```yaml
modelserver:
  depends_on:
    vault-init:
      condition: service_completed_successfully
    minio-init:
      condition: service_completed_successfully

api:
  depends_on:
    modelserver:
      condition: service_healthy
```

---

## Files Created

| File | Purpose |
|---|---|
| `modelserver/app/vault.py` | Vault secrets fetcher |
| `modelserver/app/weights.py` | MinIO download + SHA-256 verify |
| `modelserver/app/classifier.py` | DistilBERT inference |
| `modelserver/app/classical.py` | TF-IDF + LR inference |
| `modelserver/app/reranker.py` | Cross-encoder reranker |
| `modelserver/app/ner.py` | spaCy EntityRuler NER |
| `modelserver/app/summarizer.py` | GPT-4o-mini summarizer |
| `notebooks/train_classifier.ipynb` | Full training notebook (Colab T4) |
| `scripts/download_issues.py` | GitHub API issue downloader |
| `scripts/process_issues.py` | 7-step preprocessing pipeline |
| `scripts/upload_to_minio.py` | Upload training artifacts to MinIO |
| `tests/test_phase7_modelserver.py` | 13 unit tests |

## Files Modified

| File | Change |
|---|---|
| `modelserver/main.py` | Full rewrite: lifespan, real inference, mock fallback |
| `modelserver/requirements.txt` | Added torch, transformers, sentence-transformers, spacy, openai |
| `api/main.py` | CORS wildcard fix |
| `api/config.py` | `require_real_modelserver` setting |
| `api/app/api/routes/widgets.py` | iframe src fix |
| `docker-compose.yml` | depends_on ordering, CORS_ORIGINS + WIDGET_BASE_URL env vars |
| `eval_thresholds.yaml` | Classifier thresholds filled with real measured values |
| `resources/DECISIONS.md` | D-deploy filled with actual F1 numbers |

---

## Data Flow — modelserver Boot

```
FastAPI lifespan start
    │
    ├── vault.fetch_secrets() → ModelServerSecrets
    │       └── Vault KV: /secret/data/minio, /secret/data/openai
    │
    ├── Minio(mc) client created
    │
    ├── weights.download_and_verify(mc)
    │       ├── WeightsNotFound? → _mode = "mock" (all endpoints return placeholders)
    │       └── OK? → verify SHA-256 for model.safetensors, tfidf.pkl, lr.pkl
    │
    └── _mode = "real"
            ├── Classifier(model_card)        — distilbert loaded from /tmp/weights/
            ├── ClassicalClassifier()          — tfidf + lr loaded from /tmp/weights/
            ├── Reranker()                     — cross-encoder loaded from HF cache
            ├── NERPipeline()                  — spacy blank + EntityRuler
            └── Summarizer(openai_api_key)     — OpenAI client (lazy call)
```
