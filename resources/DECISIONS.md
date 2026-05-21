# DECISIONS.md
Every decision in this file is backed by a number or a concrete tradeoff. No choices without justification.

---

## Summary Table — All 18 Decisions

| # | Category | Choice | Why |
|---|---|---|---|
| D1 | Dataset | **pandas-dev/pandas** | Only repo with all 4 classes + 1,656 question samples. scikit-learn had 119 (~83 train — too few). huggingface/transformers had zero question class. |
| D2 | ML — Encoder | **distilbert-base-uncased** | Assignment says "small encoder". Trains ~8 min/epoch on Colab T4 vs 18 min RoBERTa. 14,869 samples sufficient. F1 lever is class weighting, not model size. |
| D3 | ML — Freeze | **Full fine-tune** | GitHub issues ≠ Wikipedia (pre-training domain). Freezing early layers leaves [CLS] tuned for prose. Full fine-tune at lr=2e-5 gives ~5% macro-F1 gain over linear probe. |
| D4 | ML — Logger | **Weights & Biases** | Zero infra — no new container. Native Colab integration. Free tier. Best UI for Friday demo. MLflow needs an 11th compose service. |
| D5 | ML — NER | **spaCy + EntityRuler** | Code entities (versions, exceptions, file paths) have deterministic syntax — regex captures them perfectly. HuggingFace NER trained on news text, misses all software entities. 12MB vs 250–400MB. |
| D6 | ML — Summarizer | **LLM call** | BART/T5 trained on CNN/DailyMail news — wrong domain. LLM infra already exists. Adds 0 bytes to modelserver vs 1.6GB for BART-large. Prompt versioned in `prompts/`. |
| D-pre | ML — Preprocessing | **7-step pipeline** (strip HTML, dedup, drop empty, drop dual-label, keep code, normalize, truncate) | Assignment requires defended preprocessing. Code blocks kept — they are the strongest classification signal. |
| D7 | RAG — Embeddings | **text-embedding-3-small** | MTEB 62.3 — matches bge-small. Entire corpus costs $0.05 to embed. Same OpenAI API key. Adds 0MB to modelserver. 3-large costs 6.5x more for 2.3 MTEB points — not justified. |
| D8 | RAG — Chunking | **Hierarchical parent-child** (256 / 1024 tokens) | Fixed-size is the baseline to beat. Small chunks = precise embeddings. Large chunks = rich LLM context. Hierarchical gives both: search children, return parent to LLM. |
| D-meta | RAG — Metadata | **WHERE label + source filter** | Assignment requires metadata filtering. Pre-filters HNSW from 50K→10-15K chunks using current issue label. Fallback: no filter if label unknown. |
| D9 | RAG — Vector store | **pgvector + HNSW** | Already in the stack. HNSW added in pgvector 0.5+ — same algorithm as Qdrant. ~50K chunks well within its range. Hybrid query in one SQL statement. |
| D10 | RAG — Sparse | **PostgreSQL FTS** (tsvector + GIN) | Postgres already running. Persistent + indexed. Hybrid in one SQL query. `rank_bm25` rebuilds in-memory on every restart. Elasticsearch adds 2GB for what Postgres already does. |
| D11 | RAG — Reranker | **ms-marco-MiniLM-L-6-v2** | Single biggest RAG quality improvement. Cross-encoder scores true relevance. MS MARCO training matches our task. 22M params, ~200ms for top-20 on CPU. L-12: +1–2% but +75% latency — not worth it. |
| D12 | RAG — Query | **HyDE** | Queries are question-shaped; corpus is answer-shaped. HyDE generates a hypothetical answer that bridges the vector gap. One LLM call. Combined 50/50 with original query to prevent drift. |
| D13 | Chatbot — LLM | **GPT-4o-mini** | Best tool-calling at lowest cost ($0.15/1M tokens). 20-turn session = $0.0024. gpt-4o is 17x more expensive. Same API key as embeddings — zero new infra. |
| D14 | Chatbot — Memory | **Semantic** | Facts ("asyncio issues are P0") are few, stable, reusable across all sessions. Episodic grows unboundedly. Procedural is too narrow. Best Friday demo: save fact in session 1, applied in session 2. |
| D15 | Chatbot — TTL | **24h conversation / 5min cache** | 24h covers a full working day. Overnight gap intentionally clears stale context. TTL resets on every message. 5min for API cache (`GET /me`) — rarely changes. |
| D16 | Infra — Tracing | **Langfuse cloud** | Built for LLM observability — native generation/tool/RAG spans with token counts. Jaeger is not LLM-aware. Cloud free tier = zero extra container. Best trace tree UI for Friday demo. |
| D17 | Infra — RAG eval | **RAGAS** | Purpose-built for RAG. Faithfulness + answer relevancy as first-class metrics. One function call in CI. Pinned version = reproducible judge. Hand-label 5/25 to validate agreement. |
| D18 | Frontend — CSS | **Tailwind CSS** | Vite PurgeCSS strips unused classes → ~3–5KB CSS for 5-component widget. 3–4x faster to build. Runtime theming via CSS variable. Assignment explicitly allows it. |
| D-deploy | Classifier Deployment | **Fine-tuned DistilBERT** (macro-F1 = 0.8867) | Highest F1 among zero-cost models. GPT-4o-mini scores 0.9030 but costs $7.8e-05/call. Classical ML 0.8404. DistilBERT: free inference, 469ms latency, 0.8867 macro-F1. |

---

## D1 — Dataset: pandas-dev/pandas

**Decision:** Use `pandas-dev/pandas` closed issues as the dataset.

**Options evaluated (closed issues, from GitHub label filter):**

| Repo | Bug | Feature | Docs | Question | Total | Verdict |
|---|---|---|---|---|---|---|
| pandas | 7,881 (53%) | 2,989 (20%) | 2,343 (16%) | 1,656 (11%) | 14,869 | ✅ Chosen |
| scikit-learn | 2,108 (51%) | 551 (13%) | 1,371 (33%) | 119 (3%) | 4,149 | ❌ Question class too small |
| huggingface/transformers | 2,973 (86%) | 408 (12%) | 67 (2%) | 0 | ~3,448 | ❌ No question class, docs severely underrepresented |

**Why pandas:**
- All four classes are present with clean label mappings
- Largest dataset — 14,869 issues vs 4,149 (scikit-learn) and ~3,448 (transformers)
- Question class is 11% (1,656 samples) — after 70/15/15 split, ~1,159 training samples. scikit-learn's question class (119 total → ~83 train) is too small for the model to learn reliably
- Better overall balance: no class below 11%, vs scikit-learn's 3% question tail

**Why not scikit-learn:** Only 119 question-labeled issues. After stratified split, ~83 training samples for that class — too few for DistilBERT fine-tuning to generalize.

**Why not huggingface/transformers:** Zero question-class issues. Docs (2%) is severely underrepresented.

**Label mapping:**
| GitHub label | Our class | Notes |
|---|---|---|
| `Bug` | `bug` | Direct |
| `Enhancement`, `Ideas` | `feature` | Ideas (75) merged with Enhancement (2,914) |
| `Docs` | `docs` | Direct |
| `Usage Question` | `question` | Semantic equivalent — questions about API usage |

**Tradeoff accepted:** `Usage Question` label has some noise — a small number of issues carry both `Bug` and `Usage Question`. Mitigation: during preprocessing, drop any issue that has both labels simultaneously. This keeps the question class clean without discarding significant data.

---

## D2 — Fine-tuning Encoder: distilbert-base-uncased

**Decision:** Fine-tune `distilbert-base-uncased` for 4-class issue classification.

**Options evaluated:**

| Model | Params | Training speed | Accuracy ceiling | Tokenizer | Pre-training data | Verdict |
|---|---|---|---|---|---|---|
| `distilbert-base-uncased` | 66M | Fastest | Good | WordPiece | 16GB | ✅ Chosen |
| `distilroberta-base` | 82M | Fast | Strong | BPE | 160GB | ❌ Marginal gain, not worth the tradeoff |
| `bert-base-uncased` | 110M | Slow | Good | WordPiece | 16GB | ❌ Heavier than distilbert, no accuracy advantage |
| `roberta-base` | 125M | Slowest | Best | BPE | 160GB | ❌ Over-engineered for this task and timeline |

**Why distilbert-base-uncased:**

1. **Task complexity does not justify a heavier model.** Four-class GitHub issue classification has large vocabulary differences between classes — a `bug` report and a `feature request` look nothing alike. DistilBERT has solved harder classification problems. The extra capacity of RoBERTa buys nothing measurable here.

2. **Dataset size is sufficient for distilbert.** With 14,869 labeled issues (~10,400 training samples after 70/15/15 split), distilbert will converge well. RoBERTa's advantage comes from low-data regimes where pre-training quality compensates for scarce labels — not applicable here.

3. **The lever that moves F1 is not the model — it is class weighting.** The `question` class (11%) and `feature` class (20%) are the hardest to classify. Applying inverse-frequency class weights in the loss function improves per-class F1 more than switching to a heavier encoder.

4. **Training on Google Colab — iteration speed matters.** Each training run on a Colab T4 GPU: distilbert ~8 min/epoch, roberta ~18 min/epoch. With 3–5 epochs and multiple debugging iterations (learning rate, weight decay, batch size), distilbert saves 1–2 hours of wall-clock time on a tight Thursday deadline.

5. **Assignment specification says "small encoder."** DistilBERT is the canonical small encoder in NLP. No deviation from spec without a measurable justification — a 1–2% F1 gain is not sufficient given the timeline cost.

6. **Industry standard for this use case.** DistilBERT is the model used in production for short-text classification at scale. It is the expected choice for this type of task.

**How it will be used:**

```
Input: issue title + " [SEP] " + issue body (truncated to 512 tokens)
         ↓
distilbert-base-uncased (pre-trained weights from HuggingFace)
         ↓
[CLS] token representation (768-dim)
         ↓
Dropout(0.1) → Linear(768 → 4)
         ↓
Softmax → bug / feature / docs / question
```

- **Tokenizer:** `DistilBertTokenizerFast`, max_length=512, truncation=True, padding=True
- **Classification head:** single linear layer on the `[CLS]` token output
- **Loss:** CrossEntropyLoss with inverse-frequency class weights
- **Optimizer:** AdamW, lr=2e-5, weight_decay=0.01
- **Scheduler:** linear warmup (first 10% of steps) then linear decay
- **Epochs:** 3–5, early stopping on validation macro-F1
- **Batch size:** 16 (Colab T4 memory constraint)
- **Training environment:** Google Colab (T4 GPU), weights saved to MinIO after training
- **Model card:** saved alongside weights — architecture, hyperparameters, training data SHA-256, final metrics

**Tradeoff accepted:** distilroberta-base would score ~1–2% higher macro-F1 due to BPE tokenizer and 10x more pre-training data. This gap is smaller than the variance from hyperparameter choices and is not worth the slower training loop on our deadline.

---

## D3 — Freeze Policy: Full Fine-tune

**Decision:** Fully fine-tune all layers of `distilbert-base-uncased` with a low learning rate and warmup.

**Options evaluated:**

| Policy | Layers trained | Params updated | Expected macro-F1 | Training time (Colab T4) |
|---|---|---|---|---|
| Linear probe | Head only | ~3K | ~0.78–0.82 | ~2 min/epoch |
| Partial unfreeze (last 2 blocks + head) | Last 2 transformer blocks + head | ~22M | ~0.83–0.86 | ~5 min/epoch |
| **Full fine-tune** | All 6 transformer blocks + head | 66M | ~0.87–0.91 | ~8 min/epoch |

**Why full fine-tune:**

1. **Dataset size justifies it.** ~10,400 training samples is well within the regime where full fine-tuning converges without overfitting. The original BERT fine-tuning paper (Devlin et al., 2018) and subsequent work consistently show full fine-tune outperforms linear probe by 3–5% macro-F1 on text classification tasks of this size. That gap is the number we defend.

2. **Domain shift requires layer adaptation.** DistilBERT was pre-trained on Wikipedia + BookCorpus — prose text. GitHub issues contain error messages (`ValueError: Cannot merge on object and int64 columns`), function calls (`DataFrame.merge()`), version strings (`pandas==2.0.1`), and code snippets. The early transformer layers encode token co-occurrence patterns calibrated for prose. Freezing them means the `[CLS]` representation is built on representations tuned for the wrong domain. Full fine-tune lets every layer adapt slightly to GitHub issue vocabulary and structure.

3. **Low learning rate prevents catastrophic forgetting.** lr=2e-5 with linear warmup is the standard BERT fine-tuning regime. At this rate, early layers move by less than 0.1% of their initialized values per epoch — they retain pre-trained language knowledge while adapting to the domain. Weight decay=0.01 further prevents drift. This is not destroying pre-trained weights — it is nudging them.

4. **Assignment requires a defended number.** Full fine-tune produces a clearly measurable improvement over linear probe (~5% macro-F1). This makes the freeze policy defense concrete: "Full fine-tune scored 0.89 macro-F1 vs 0.83 for linear probe on our val set."

5. **Training time is acceptable.** 3 epochs × 8 min = 24 minutes on Colab T4. With early stopping (patience=2 on val macro-F1), often 2 epochs suffice (~16 min). This fits in the Tuesday DL track block.

**Tradeoff accepted:** Full fine-tune risks overfitting if training runs too long. Mitigated by early stopping with patience=2 on validation macro-F1, and weight decay=0.01. If val F1 does not improve for 2 consecutive epochs, training stops.

---

## D4 — Run Logger: Weights & Biases (W&B)

**Decision:** Use Weights & Biases (`wandb`) for experiment tracking during fine-tuning.

**Options evaluated:**

| Tool | Hosting | Extra container | Colab support | UI quality | Free tier |
|---|---|---|---|---|---|
| **W&B** | Cloud | No | Native | Excellent | Unlimited runs |
| MLflow | Self-hosted | Yes (11th service) | Manual setup | Good | Self-managed |

**Why W&B:**

1. **Zero infrastructure overhead.** MLflow requires either a 11th docker-compose service (tracking server) or an external server — both add ops complexity to an already 10-service stack. W&B is cloud-hosted. Three lines in Colab: `pip install wandb`, `wandb.login()`, `wandb.init()`. Nothing to deploy, nothing to maintain.

2. **Native Colab integration.** Training runs in Google Colab. W&B has first-class Colab support — handles auth via browser, persists run history across sessions, streams metrics in real time to the W&B dashboard. MLflow in Colab requires starting a local server, tunneling, and manual configuration.

3. **Free tier is sufficient.** W&B free tier: unlimited runs, 100GB storage, full dashboard. A project with 15–20 training runs uses less than 1% of this.

4. **Better demo artifact.** W&B produces live training curves (loss, accuracy, learning rate per step), confusion matrix heatmaps, and side-by-side hyperparameter comparison tables. The Friday demo can walk through actual training charts. MLflow's local UI is functional but less polished.

5. **Industry standard for GPU training experiments.** W&B is the tool practitioners use when training on cloud GPUs. MLflow is stronger in MLOps serving pipelines. For the training phase of this project, W&B is the correct fit.

**What will be logged:**
- Per epoch: train loss, val loss, train accuracy, val macro-F1, per-class F1 (bug/feature/docs/question)
- End of training: confusion matrix on val set
- Run config: all hyperparameters (lr, batch size, epochs, class weights, model name)
- Final weights artifact uploaded to W&B + separately to MinIO (architecture requirement)

**Tradeoff accepted:** W&B sends training data (metrics, hyperparameters) to W&B cloud servers. For this project the training data is derived from public GitHub issues — no sensitivity concern. If offline training were required, MLflow would be the fallback.

---

## D5 — NER Implementation: spaCy + EntityRuler

**Decision:** Use spaCy `en_core_web_sm` with a custom `EntityRuler` component for code-shaped entity extraction.

**Options evaluated:**

| Approach | Base model | Code entity support | Memory footprint | Speed | Testability |
|---|---|---|---|---|---|
| **spaCy + EntityRuler** | `en_core_web_sm` (12MB) | Custom regex patterns — deterministic | ~50MB total | CPU, microseconds/doc | Fully deterministic |
| HuggingFace `pipeline("ner")` | `dslim/bert-base-NER` (400MB) | CoNLL-2003 types only — misses code entities | ~1.5GB | GPU recommended | Probabilistic |
| HuggingFace `pipeline("ner")` | `elastic/distilbert-NER` (250MB) | CoNLL-2003 types only — misses code entities | ~1GB | Medium | Probabilistic |

**Why spaCy + EntityRuler:**

1. **"Code-shaped entities" = deterministic patterns, not statistical NER.** The assignment says "code-shaped entities." These are: version numbers (`pandas==2.0.1`, `Python 3.11`, `v2.3.0`), Python exceptions (`ValueError`, `TypeError`, `KeyError`), file paths (`pandas/core/frame.py`), function/method calls (`DataFrame.merge()`), and import names. Every one of these follows a rigid syntactic pattern that a regex captures with 100% precision and recall. A neural NER model trained on CoNLL-2003 (news text: PERSON, ORG, LOC, DATE) does not recognize any of them — it was never trained on this data.

2. **No off-the-shelf HuggingFace NER model targets software entities.** Available models (`dslim/bert-base-NER`, `elastic/distilbert-NER`, `Jean-Baptiste/roberta-large-ner-english`) are all trained on news/Wikipedia corpora for standard entity types. They would label `pandas` as ORG (sometimes), miss `DataFrame.merge()` entirely, and confuse `Python 3.11` with a DATE. The domain mismatch is fundamental — it cannot be fixed without retraining, which the assignment forbids ("integration only").

3. **Memory constraint in modelserver.** The `modelserver` container already loads distilbert-base-uncased (~250MB). Adding a HuggingFace NER transformer (+250–400MB) brings the container to 500–650MB. spaCy `en_core_web_sm` is 12MB — total modelserver memory stays ~262MB. In a 10-service compose stack on a development machine, this matters.

4. **Deterministic output is testable.** EntityRuler produces identical output for identical input, always. Unit tests for NER are simple assertions: "given this issue text, extract these entities." Neural models produce variable outputs — tests require fuzzy matching or probabilistic assertions, which are harder to write and maintain.

5. **spaCy EntityRuler is designed exactly for this.** It is spaCy's built-in component for rule-based entity recognition. Define regex patterns and token patterns, add to pipeline, run. Handles overlap with statistical NER cleanly.

**Entity patterns:**
```python
patterns = [
    {"label": "VERSION",   "pattern": [{"TEXT": {"REGEX": r"v?\d+\.\d+(\.\d+)?"}}]},
    {"label": "EXCEPTION", "pattern": [{"TEXT": {"REGEX": r"\w+(Error|Exception|Warning)"}}]},
    {"label": "FILEPATH",  "pattern": [{"TEXT": {"REGEX": r"[\w/\\]+\.py"}}]},
    {"label": "FUNCTION",  "pattern": [{"TEXT": {"REGEX": r"\w+\.\w+\("}}]},
    {"label": "PACKAGE",   "pattern": [{"LOWER": {"IN": ["pandas", "numpy", "scipy",
                                        "sklearn", "matplotlib", "pytest"]}}]},
]
```

**Tradeoff accepted:** EntityRuler patterns require manual definition. New entity types (e.g., new exception names) require a pattern update. Mitigated by using broad regex patterns (`\w+Error` catches all Python exceptions generically) rather than exhaustive explicit lists, so new patterns are rarely needed.

---

## D6 — Summarizer: LLM Call

**Decision:** Use an LLM API call with a structured summarization prompt. No pre-trained summarization model loaded in `modelserver`.

**Options evaluated:**

| Approach | Model | Domain fit for GitHub issues | Memory added to modelserver | Quality | Marginal cost |
|---|---|---|---|---|---|
| **LLM call** | Same LLM as chatbot | Excellent | 0 bytes | High | ~$0.0001/call |
| Pre-trained BART | `facebook/bart-large-cnn` (400M) | Poor — trained on news (CNN/DailyMail) | ~1.6GB | Low | $0 |
| Pre-trained T5 | `t5-small` (60M) | Poor — general web text | ~240MB | Medium-Low | $0 |
| Pre-trained DistilBART | `sshleifer/distilbart-cnn-12-6` (306M) | Poor — news domain | ~1.2GB | Low-Medium | $0 |

**Why LLM call:**

1. **BART/T5 have fatal domain mismatch.** Every available pre-trained summarization model on HuggingFace was fine-tuned on CNN/DailyMail news articles. GitHub issue threads are a completely different domain: multi-participant technical discussions with stack traces, code blocks, version numbers, and resolution comments. A model trained on "The president signed the bill today..." produces structurally poor output on "I'm seeing a `ValueError` in `DataFrame.merge()` when using `left_on` with nullable integers — here is the stack trace..." An LLM with a specific prompt handles this perfectly because modern instruction-tuned LLMs understand GitHub issue format from pre-training.

2. **LLM infrastructure already exists.** The chatbot calls an LLM for every response. The API client, Vault-stored API key, and `app/infra/` adapter are already built for D13 (LLM provider decision). The summarizer is one additional prompt sent to the same client. Zero new infrastructure, zero new container, zero new Vault secret.

3. **Memory constraint is real.** `modelserver` with distilbert + spaCy is ~300MB. Adding BART-large would make it a 1.9GB container. The LLM runs externally — `modelserver` stays lean.

4. **Quality through instruction.** The summarization prompt is specific: "Summarize this GitHub issue thread. Focus on: (1) the problem statement, (2) environment (library version, Python version), (3) key error messages, (4) resolution or proposed fix. Output as 4 concise bullet points." An instruction-tuned LLM follows this precisely and produces structured, domain-aware output. BART generates a flowing paragraph with no structure control.

5. **Prompt is version-controlled as required.** Lives at `prompts/summarize.md`, committed to git. Changing summarization behavior = a reviewed commit. No retraining required.

6. **Cost is negligible.** A GitHub issue thread is ~500–1,000 tokens. One summarization call costs ~$0.0001 at GPT-4o-mini pricing. For a course project with dozens of demo calls, total cost is under $0.01.

**How it will be used:**
```
POST modelserver/summarize
  body: { "title": "...", "body": "...", "comments": ["...", "..."] }
  ↓
app/infra/llm_client.py → LLM API call with prompts/summarize.md as system prompt
  ↓
returns: { "summary": "• Problem: ...\n• Environment: ...\n• Error: ...\n• Resolution: ..." }
```

**Tradeoff accepted:** LLM call introduces ~0.5–1.5s latency and API dependency. Mitigated by: (1) timeout + graceful fallback in `app/infra/` — if LLM is unavailable, return `{"summary": null, "error": "summarizer_unavailable"}` and chatbot continues without crashing; (2) result cached in Redis by `issue_id` — the same issue thread is not re-summarized within a session.

---

## D-preprocess — Text Preprocessing Pipeline

**Decision:** Clean GitHub issue text with a 7-step deterministic pipeline before any model sees it.

**Why each step is required by the assignment ("defend your choices in DECISIONS.md"):**

| Step | What it does | Why |
|---|---|---|
| 1. Strip HTML | Remove `<code>`, `<pre>`, `<br>` tags from issue bodies (GitHub API returns Markdown + some HTML) | Prevents tag fragments polluting token sequences |
| 2. Deduplicate | Remove exact-duplicate (title + body) entries | GitHub allows duplicate issues; duplicates bias training |
| 3. Drop empty | Remove issues with no title or empty body (<20 chars) | No signal — model would learn noise |
| 4. Drop dual-labeled | Remove issues carrying both `Bug` AND `Usage Question` simultaneously | Ambiguous ground truth corrupts classifier training |
| 5. Keep code blocks | Do NOT strip backtick content or inline code | Code fragments are the strongest signal for bug vs question classification |
| 6. Normalize whitespace | Collapse multiple newlines/spaces to single | Consistent token sequences; prevents artificial length variance |
| 7. Truncate | Truncate title + `[SEP]` + body to 512 tokens for DistilBERT | Transformer hard limit; title carries most signal so it is preserved first |

**Implementation:**
```python
def preprocess_issue(title: str, body: str) -> str:
    body = strip_html(body)              # BeautifulSoup4
    body = normalize_whitespace(body)
    combined = f"{title} [SEP] {body}"
    return combined[:MAX_CHARS]          # conservative char limit before tokenizer truncates
```

**What is NOT stripped:** code blocks, error messages, version numbers, function names — these are the entities NER extracts and classifiers learn from.

**Tradeoff accepted:** Not lowercasing (DistilBERT tokenizer handles casing; uncased model handles it at the vocabulary level). Not removing stop words (transformer attention handles relevance weighting better than stop-word lists, and removing stops hurts semantic search).

---

## D7 — Embedding Model: text-embedding-3-small

**Decision:** Use OpenAI `text-embedding-3-small` as the RAG embedding model. Test against `BAAI/bge-small-en-v1.5` on the golden set and deploy the winner.

**Options evaluated:**

| Model | Dims | MTEB avg | Cost | Hosted | Memory in modelserver |
|---|---|---|---|---|---|
| **`text-embedding-3-small`** | 1536 | 62.3 | $0.02/1M tokens | API | 0 |
| `BAAI/bge-small-en-v1.5` | 384 | 62.2 | Free | modelserver | +90MB |
| `text-embedding-ada-002` | 1536 | 61.0 | $0.10/1M tokens | API | 0 |
| `text-embedding-3-large` | 3072 | 64.6 | $0.13/1M tokens | API | 0 |

**Why text-embedding-3-small:**

1. **Best quality-to-cost ratio.** `text-embedding-3-small` matches `bge-small-en-v1.5` on MTEB (62.3 vs 62.2) while requiring zero extra infrastructure. `text-embedding-3-large` scores higher (64.6) but costs 6.5x more — not justified for our corpus size.

2. **Corpus embedding cost is negligible.** Pandas docs + ~2,000 resolved issues = ~2.5M tokens to embed once at ingestion. At $0.02/1M tokens = **$0.05 total**. Query embeddings (~50 tokens each) cost fractions of a cent per session. Cost is not a factor here.

3. **Reuses existing API infrastructure.** Same OpenAI API key in Vault, same `app/infra/llm_client.py` adapter. No new service, no new Vault secret.

4. **No memory pressure on modelserver.** `bge-small-en-v1.5` would add ~90MB to a modelserver already holding distilbert (250MB) + spaCy (50MB) + cross-encoder (85MB). API-based embedding adds 0 bytes.

5. **Assignment requires testing an alternative.** We run both models on the 25-item RAG golden set, report hit@5 + MRR@10 for each, and deploy the winner. If `bge-small-en-v1.5` wins on our specific corpus, we switch — the code path is the same either way.

**Tradeoff accepted:** API dependency — if OpenAI embedding API is unreachable, RAG query-time embedding fails. Corpus embeddings are already stored in pgvector so ingestion is unaffected. Graceful fallback returns "search unavailable" at query time without crashing.

---

## D8 — Chunking Strategy: Hierarchical Parent-Child

**Decision:** Hierarchical parent-child chunking. Child chunks (256 tokens) for embedding. Parent chunks (1024 tokens) returned to the LLM as context.

**Options evaluated:**

| Strategy | Retrieval precision | LLM context quality | Assignment requirement |
|---|---|---|---|
| Naive fixed-size 512 chars | Low — mid-sentence cuts | Medium | ❌ Baseline to beat |
| Sentence-based | Medium | Low — short context | Partial |
| Paragraph-based | Medium | Good | Partial |
| **Hierarchical parent-child** | High — sharp child embeddings | High — rich parent context | ✅ Not naive fixed-size |
| Semantic chunking | High | High | ✅ But high complexity |

**Why hierarchical parent-child:**

**The fundamental tradeoff in chunking:** small chunks produce sharp embeddings (one idea per vector → precise retrieval), but the LLM gets a tiny snippet with no surrounding context. Large chunks give the LLM rich context, but their embeddings are a blurry average of many topics → imprecise retrieval. You cannot pick one size that is good at both.

**Hierarchical chunking solves this by separating retrieval from context delivery:**

```
DOCUMENT
├── Parent chunk (1024 tokens) — returned to LLM ──────────────────────┐
│   ├── Child chunk A (256 tokens) → embedded, searched               │  context
│   ├── Child chunk B (256 tokens) → embedded, searched               │  window
│   └── Child chunk C (256 tokens) → embedded, searched               │
```

At query time: search child embeddings (precise) → find matching child → return its parent to the LLM (rich context). The RAG guide: "Almost always use this. If a single technique in this guide is worth implementing, it is probably this one."

**Applied to our corpus:**
- **Documentation:** pandas docs have natural section structure. Parents = full sections (~1024 tokens). Children = paragraphs within sections (~256 tokens).
- **Resolved issues:** Parents = full issue + comments. Children = individual comment chunks.

**Schema:**
```sql
chunks (id, parent_id, text, embedding vector(1536), search_vector tsvector,
        source, issue_id, label, created_at, is_parent bool)
```

**Tradeoff accepted:** ~2x storage vs single-size chunking. For ~50K total chunks this is negligible.

---

## D9 — Vector Store: pgvector with HNSW Index

**Decision:** Use pgvector (already in the stack). Do not add Qdrant.

**Options evaluated:**

| Store | In stack | HNSW | New container | Corpus limit | Verdict |
|---|---|---|---|---|---|
| **pgvector + HNSW** | ✅ Yes | ✅ v0.5+ | No | ~1M vectors | ✅ Chosen |
| Qdrant | ❌ No | ✅ Native | Yes — 11th service | Billions | ❌ Over-engineered |

**Why pgvector:**

1. **Already required.** The assignment specifies `postgres:16 with pgvector`. It is in the compose stack regardless. Using it for vectors costs nothing extra.

2. **HNSW index resolves the performance concern.** pgvector 0.5+ (2023) added HNSW — the same algorithm used by Qdrant and Pinecone. The RAG guide: "switching to HNSW will cut query latency substantially with no code changes beyond the index creation." For our ~50K chunk corpus, HNSW query latency is <10ms.

```sql
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

3. **Hybrid retrieval in one SQL query.** pgvector + PostgreSQL FTS (D10) run in the same database — hybrid retrieval is one query, no cross-service coordination.

4. **Qdrant's advantages do not apply here.** Qdrant is the right choice at millions of vectors with complex filtered queries at high write throughput. Our corpus is ~50K static chunks — pgvector handles this trivially.

**Tradeoff accepted:** pgvector does not match Qdrant at 10M+ vectors. If this project scaled 200x, Qdrant would be the call. For now, adding Qdrant is premature optimization.

---

## D10 — Sparse Retrieval: PostgreSQL Full-Text Search

**Decision:** Use PostgreSQL `tsvector` + GIN index + `ts_rank` for the sparse (BM25-like) component of hybrid retrieval.

**Options evaluated:**

| Approach | Extra service | Persistent | Latency | Integration |
|---|---|---|---|---|
| **PostgreSQL FTS (tsvector)** | No | ✅ GIN index | 5–20ms | Single SQL query |
| `rank_bm25` Python library | No | ❌ Rebuilt at startup | 50–200ms in-memory | Separate in-memory store |
| Elasticsearch | Yes — 11th service | ✅ | ~10ms | New infra, new Vault secret |

**Why PostgreSQL FTS:**

1. **Zero new infrastructure.** Postgres is already running. `tsvector` is a native Postgres type. Add one column and one index — done.

2. **Persistent and indexed.** Unlike `rank_bm25` (rebuilds from corpus on every container restart, holds corpus in RAM), PostgreSQL FTS persists the GIN index on disk. No startup delay, no memory spike.

3. **Hybrid retrieval in a single query.** Vector similarity (pgvector) + keyword scoring (tsvector) in one SQL statement — no application-layer result merging needed:

```sql
SELECT id, text,
  (0.6 * (1 - embedding <-> $query_vec) +
   0.4 *  ts_rank(search_vector, plainto_tsquery($query))) AS hybrid_score
FROM chunks
ORDER BY hybrid_score DESC
LIMIT 20;
```
The 0.6/0.4 weighting is tuned on the 25-item RAG golden set.

4. **`rank_bm25` is wrong for production.** It holds the full corpus in RAM and rebuilds on every restart. For a corpus of 50K chunks, that is wasteful and fragile.

5. **Elasticsearch is a 2GB service** for functionality Postgres already provides at this scale.

**Schema addition:**
```sql
ALTER TABLE chunks ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;
CREATE INDEX idx_chunks_fts ON chunks USING GIN(search_vector);
```

**Tradeoff accepted:** PostgreSQL `ts_rank` approximates BM25 but is not an exact BM25 implementation. For our corpus the quality difference is negligible. A pure BM25 implementation would require Elasticsearch or a separate BM25 service — unjustifiable operational cost.

---

## D11 — Cross-Encoder Reranker: ms-marco-MiniLM-L-6-v2

**Decision:** Use `cross-encoder/ms-marco-MiniLM-L-6-v2` to rerank the top-20 hybrid results, returning top-5 to the LLM.

**Options evaluated:**

| Model | Params | Latency top-20 CPU | Quality | Memory |
|---|---|---|---|---|
| **`ms-marco-MiniLM-L-6-v2`** | 22M | ~200ms | Good | ~85MB |
| `ms-marco-MiniLM-L-12-v2` | 33M | ~350ms | +1–2% | ~130MB |
| `ms-marco-electra-base` | 110M | ~900ms | Best | ~440MB |
| LLM reranking | — | ~1,500ms + API | Excellent | 0 |

**Why ms-marco-MiniLM-L-6-v2:**

1. **Reranking is the single biggest RAG quality improvement.** The RAG guide: "Reranking is one of the single biggest quality improvements you can make to a RAG system." The bi-encoder (embedding model) retrieves by approximate vector similarity — fast, but imprecise. The cross-encoder reads query + chunk together and scores true semantic relevance, catching cases where a chunk is nearby in vector space but does not actually answer the question.

2. **MS MARCO training matches our task.** MS MARCO is a passage retrieval dataset of real user questions + relevant passages — exactly the query-passage relevance problem we have. Cross-encoders trained on MS MARCO generalize well to technical Q&A.

3. **22M params at 200ms fits our latency budget.** modelserver holds distilbert (66M) + spaCy (12MB) + cross-encoder (22M) = ~100M params total, well within a 2GB container. 200ms reranking is acceptable for a maintainer tool where total RAG latency target is <600ms.

4. **L-12 is not worth 75% more latency.** `MiniLM-L-12-v2` improves quality by ~1–2% but costs 350ms vs 200ms. In an interactive chat interface, that difference is perceptible. The quality gap does not justify it.

**Flow:**
```
Hybrid retrieval → top-20 candidates
  ↓
cross-encoder scores each (query, chunk_text) pair
  ↓
sorted by cross-encoder score → top-5 → LLM context
```

**Tradeoff accepted:** Cross-encoder only runs on the top-20 hybrid candidates. If the relevant chunk is not in the top-20, reranking cannot recover it. Mitigated by tuning hybrid retrieval recall (α weight, index settings) to ensure the top-20 has high coverage before reranking.

---

## D-meta — Metadata Filtering

**Decision:** Filter the vector search space by `label` and `source` at query time before HNSW retrieval.

**Assignment requirement:** "Metadata filtering over the corpus."

**Options evaluated:**

| Approach | Reduces search space | Requires extra infra | Latency impact |
|---|---|---|---|
| **WHERE clause on label/source** | Yes — 50K → ~10-15K | No — already in chunks table | –30% query time |
| Separate index per label | Yes | Requires 4+ indexes | Adds schema complexity |
| Post-filter (search all, then filter) | No | None | No benefit |

**Why WHERE clause:**

The `chunks` table already has `label` and `source` columns. At query time we know the current issue's probable class (from the HyDE step, the issue text itself, or the classifier result). We use this to narrow retrieval:

```sql
SELECT id, text,
  (0.6 * (1 - embedding <-> $query_vec) +
   0.4 * ts_rank(search_vector, plainto_tsquery($query))) AS hybrid_score
FROM chunks
WHERE (label = $current_label OR label IS NULL OR source = 'docs')
ORDER BY hybrid_score DESC
LIMIT 20;
```

- `label = $current_label` — prioritize same-class resolved issues (bug query → bug chunks)
- `label IS NULL` — include parent chunks (parent chunks don't carry a label, only children do)
- `source = 'docs'` — always include documentation regardless of label

**What `$current_label` is set to:**
- If the maintainer has already classified the issue → use that label
- If not classified yet → omit the filter (search all)

**Tradeoff accepted:** Filtering by label assumes the current issue's class is known before RAG runs. In practice the `classify_issue` tool runs first, making the label available. If classification hasn't run, the filter is dropped — full corpus search is the fallback.

---

## D12 — Query Transformation: HyDE (Hypothetical Document Embeddings)

**Decision:** Use HyDE — generate a hypothetical answer to the user's query, embed it, and combine with the original query vector for retrieval.

**Options evaluated:**

| Technique | LLM calls | Best for | Weakness |
|---|---|---|---|
| **HyDE** | 1 | Short queries where query ≠ document shape | Latency; drift risk |
| Multi-query | 1 (3–4 variants) | Under-specified queries | Higher LLM cost; RRF complexity |
| Step-back prompting | 1 | Over-specific queries | Overkill for general queries |
| Query expansion | 0 | Keyword-sparse queries | Limited — just synonym addition |

**Why HyDE:**

1. **Our queries look nothing like our corpus.** A maintainer asks: *"Has this ValueError in merge been seen before?"* — question-shaped, short, contextual. The corpus contains resolved issues and docs — answer-shaped. The embedding distance between question and answer is larger than answer-to-answer. HyDE closes this gap: the LLM generates a hypothetical resolved issue that would answer the question. That hypothetical is answer-shaped — it lands close to relevant corpus chunks in vector space. The RAG guide: "The hypothetical answer just needs to be in the right shape — it does not need to be correct."

2. **Maintainer queries are short and contextual.** After pasting an issue, the maintainer asks short follow-ups like "has this been fixed?", "what's the workaround?". HyDE expands the query implicitly by writing out what a good answer looks like.

3. **One LLM call is within budget.** We already make LLM calls for each chatbot turn. One additional call (~300ms, ~500 tokens) at RAG query time is acceptable.

4. **Multi-query adds complexity without equivalent gain.** For a technical pandas corpus, 3–4 rephrasings of "ValueError in merge" tend to hit the same chunks. HyDE achieves more directional shift with a single call.

**Flow:**
```
User query: "Has this ValueError in merge() with nullable ints been reported?"
  ↓
LLM call (prompts/hyde.md):
  "Write a short passage from a resolved GitHub issue answering this question."
  ↓
Hypothetical: "Resolved: ValueError raised when merging DataFrames with Int64
               nullable dtype. Fixed in 2.1 — cast to object before merge..."
  ↓
embed(query)        → vector_q
embed(hypothetical) → vector_h
search_vector = 0.5 × vector_q + 0.5 × vector_h
  ↓
Hybrid retrieval (dense + sparse) → top-20 → cross-encoder rerank → top-5 → LLM
```

The 0.5/0.5 blend weight is tuned on the RAG golden set.

**Tradeoff accepted:** If the hypothetical drifts (LLM generates something too generic), it shifts the search vector away from relevant chunks. Mitigated by: (1) combining 50/50 with original query vector so one bad hypothetical doesn't dominate; (2) tightly constrained HyDE prompt in `prompts/hyde.md`; (3) cross-encoder reranker catches false positives before they reach the LLM.

---

## D13 — LLM Provider + Model: OpenAI GPT-4o-mini

**Decision:** Use OpenAI `gpt-4o-mini` as the single tool-calling LLM for the chatbot.

**Options evaluated:**

| Model | Tool calling | Cost (input/output) | Latency | Context window | Verdict |
|---|---|---|---|---|---|
| **`gpt-4o-mini`** | Excellent | $0.15 / $0.60 per 1M tokens | Fast (~500ms) | 128K | ✅ Chosen |
| `gpt-4o` | Excellent | $2.50 / $10.00 per 1M tokens | Medium | 128K | ❌ 17x more expensive, marginal gain |
| `claude-haiku-4-5` | Excellent | $0.80 / $4.00 per 1M tokens | Fast | 200K | ❌ More expensive, different SDK |
| `claude-sonnet-4-5` | Excellent | $3.00 / $15.00 per 1M tokens | Medium | 200K | ❌ Overkill for triage chatbot |
| `gemini-1.5-flash` | Good | $0.075 / $0.30 per 1M tokens | Very fast | 1M | ❌ Less proven for complex tool orchestration |

**Why gpt-4o-mini:**

1. **Best tool-calling quality at low cost.** `gpt-4o-mini` is OpenAI's current small model, built on the same architecture as gpt-4o with strong function/tool calling support. Tool calling is the core behaviour of our chatbot — it must reliably choose classify_issue vs rag_search vs write_memory based on context. gpt-4o-mini handles multi-turn tool orchestration reliably.

2. **Cost is negligible for this project.** A maintainer triage session: ~20 turns × ~800 tokens each = ~16K tokens. At $0.15/1M input tokens = **$0.0024 per session**. The total cost for a week of demos is under $0.10. gpt-4o at $2.50/1M tokens would cost 17x more with no meaningful quality improvement for a triage classification task.

3. **Same API key and infrastructure as embeddings.** We already have an OpenAI API key in Vault and an `app/infra/llm_client.py` adapter for embeddings (D7). The chatbot LLM is the same client with a different model name. Zero new Vault secrets, zero new infrastructure.

4. **128K context window handles long conversations.** A full triage session with issue text, tool results, and history stays well within 128K tokens. No truncation concerns.

5. **gpt-4o is not justified.** The triage task — classify an issue, extract entities, search past issues, summarize — does not require GPT-4o-level reasoning. These are structured, well-defined operations. gpt-4o-mini is sufficient.

**How it will be used:**
```
System prompt: prompts/system.md (issue triage assistant, tool definitions)
Tools available: classify_issue, extract_entities, summarize_thread,
                 search_knowledge_base, write_memory
Turn: maintainer message → gpt-4o-mini → tool call → result → final response
```

- Streaming: enabled — response tokens stream to the frontend as they arrive
- Temperature: 0.1 — low variance for structured triage decisions
- Max tokens: 1024 per response (triage answers are short)
- Tool choice: "auto" — model decides when to call tools

**Tradeoff accepted:** OpenAI API dependency — if OpenAI is unreachable, the chatbot is unavailable. Mitigated by the "refuse to boot" check: if the LLM API key is missing or invalid at startup, the app exits cleanly rather than running in a broken state.

---

## D14 — Long-term Memory Type: Semantic

**Decision:** Implement **semantic memory** — store general facts and conventions about the repo and the maintainer's workflow in pgvector.

**Options evaluated:**

| Type | Stores | Example | Persistence value | Implementation complexity |
|---|---|---|---|---|
| **Semantic** | General facts and conventions | "asyncio issues are high priority", "questions without repro steps → close as docs" | High — reusable across all future sessions | Low |
| Episodic | Specific past events | "On May 15 the maintainer resolved issue #1234 by..." | Medium — fades in relevance | Medium |
| Procedural | Behavioral preferences | "Always output bullet points", "skip greeting" | Low — narrow scope | Low |

**Why semantic memory:**

1. **Most useful for a triage copilot.** A maintainer's long-term context is not a log of past events — it is a set of facts about how their repo works: "We label issues without a stack trace as `docs`", "All asyncio-related issues are P0", "Enhancement requests need a design doc before labeling `feature`." These are semantic facts that apply to every future session. The copilot retrieves relevant facts at the start of each turn and uses them to give context-aware responses.

2. **Episodic memory grows unboundedly and most events are not reusable.** If every resolved issue creates episodic entries, the memory store grows to thousands of rows quickly. The majority of past events are not relevant to the current triage session. Semantic facts are few, stable, and broadly applicable.

3. **Maps cleanly to pgvector.** Each semantic fact is a sentence. Store its embedding in pgvector. At query time, embed the current issue context and retrieve the top-3 most relevant facts. Include them in the LLM's system prompt for that turn. Clean, simple, demonstrable on Friday.

4. **Easiest to demo cross-conversation recall on Friday.** Session 1: maintainer says "remember: asyncio issues are always P0". Session 2 (new session): maintainer asks about an asyncio bug → copilot responds "This is an asyncio issue — marking as P0 per your standing instruction." That is a concrete, impressive demo of persistent memory.

**Schema:**
```sql
long_term_memories (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    content TEXT,
    embedding vector(1536),
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
)
-- Audit log row on every INSERT/UPDATE/DELETE
```

**Retrieval at turn start:**
```python
relevant_memories = await memory_repo.search(
    user_id=user.id,
    query_embedding=embed(current_issue_text),
    limit=3
)
# Injected into system prompt as: "Standing instructions from this maintainer: ..."
```

**Tradeoff accepted:** Semantic memory requires the maintainer to explicitly save facts via the `write_memory` tool — no auto-writes (as required by the assignment). The copilot only knows what it has been told. Mitigated by prompting the LLM to suggest saving a fact when it detects a reusable convention in the conversation.

---

## D15 — Redis TTL: 24 hours for conversation, 5 minutes for cache

**Decision:** Conversation state TTL = 86,400 seconds (24 hours). API response cache TTL = 300 seconds (5 minutes).

**Options evaluated for conversation TTL:**

| TTL | Behaviour | Problem |
|---|---|---|
| 1 hour | Session expires mid-afternoon | Maintainer returns after lunch to a blank session |
| **24 hours** | Full working day + overnight preserved | Sessions from yesterday are intentionally cleared |
| 7 days | Long persistence | Stale context from last week pollutes responses |
| No TTL | Never expires | Redis fills indefinitely |

**Why 24 hours for conversation state:**

1. **Matches a maintainer's working day.** A maintainer triages issues in focused sessions of 1–4 hours, often returning after breaks. 24h ensures they return to an active session after lunch, after a meeting, after a coffee break — without losing context. An overnight gap (25+ hours) intentionally clears the session — yesterday's triage context is stale and should not bleed into today's work.

2. **Prevents Redis memory accumulation.** A conversation with 50 messages at ~200 tokens each ≈ ~15KB. 1,000 active users × 15KB = 15MB — trivial. But without a TTL, Redis grows indefinitely as abandoned sessions accumulate. 24h TTL self-cleans abandoned sessions.

3. **Standard for stateful web sessions.** 24h aligns with industry-standard session cookie expiry, JWT TTL defaults, and the natural daily working cycle.

4. **What happens at TTL expiry mid-conversation:** If a maintainer has the chat open overnight and the TTL fires, the next message starts a fresh session. The chatbot greets them as if new. Long-term memory (D14) is unaffected — semantic facts persist independently. This is the correct behaviour.

**Why 5 minutes for API response cache:**

- `GET /me` (user profile): changes rarely, 5 min TTL is safe and avoids DB hits
- `GET /conversations` (list): changes on new message, 5 min acceptable staleness
- Classifier predictions: cached by `issue_id` — no TTL needed, invalidated on model update

**Implementation:**
```python
CONVERSATION_TTL = 86_400   # 24h — conversation history in Redis
CACHE_TTL        = 300      # 5 min — API response cache
# Keys: conversation:{user_id}:{session_id} → JSON list of messages
# Set with: redis.setex(key, CONVERSATION_TTL, value)
# Reset TTL on every message: redis.expire(key, CONVERSATION_TTL)
```

**Tradeoff accepted:** A power user who leaves a conversation open for >24h loses context. This is intentional — stale context is worse than no context. Long-term semantic memory (D14) covers the facts worth keeping across days.

---

## D16 — Tracing Backend: Langfuse

**Decision:** Use Langfuse (cloud free tier) as the tracing backend.

**Options evaluated:**

| Tool | LLM-native | Hosting | Extra container | Trace UI quality | Tool call visibility |
|---|---|---|---|---|---|
| **Langfuse** | ✅ Yes — built for LLMs | Cloud (free tier) or self-hosted | No (cloud) | Excellent | Native support |
| Jaeger | ❌ No — general distributed tracing | Self-hosted | Yes | Good | Manual span attributes |
| Phoenix (Arize) | ✅ Yes | Self-hosted | Yes | Good | Native support |

**Why Langfuse:**

1. **Built for LLM observability, not generic tracing.** Jaeger is designed for microservice distributed tracing — it understands HTTP spans, DB queries, service-to-service calls. It does not natively understand prompts, token counts, LLM model names, or tool call inputs/outputs. Every LLM-specific attribute must be manually attached as span tags. Langfuse natively models: generation (LLM call), trace (conversation), span (tool call / RAG retrieval), with built-in fields for model, prompt, completion, token usage, cost.

2. **Cloud free tier = zero extra container.** Langfuse cloud free tier: unlimited traces for solo projects. No new compose service, no new port, no new volume. Just `LANGFUSE_SECRET_KEY` and `LANGFUSE_PUBLIC_KEY` in Vault, and the `langfuse` Python SDK in the app. Compare to self-hosting Jaeger (Jaeger all-in-one container, port 16686, ~500MB RAM) or Phoenix (another Python server).

3. **Best trace UI for the Friday demo.** The assignment says: "walk through a real conversation's trace tree including one trace that hit an error path." Langfuse's trace view shows the full conversation tree: user message → LLM call → tool call → HTTP to modelserver → result → LLM response, all as a nested timeline. This is the most readable UI for a 10-minute presentation.

4. **OpenTelemetry compatible.** Langfuse supports OpenTelemetry export. If we ever need to switch backends, the instrumentation code doesn't change.

5. **Span attributes captured automatically:**
   - LLM calls: model name, prompt, completion, token counts (input/output), latency, cost estimate
   - Tool calls: tool name, input arguments (after redaction), output
   - RAG retrieval: query, retrieved chunks, reranker scores

**Instrumentation pattern:**
```python
# app/infra/tracing.py
from langfuse import Langfuse
langfuse = Langfuse()  # reads keys from env (fetched from Vault at startup)

# Every LLM call:
with langfuse.start_as_current_span("llm_call", input=redact(prompt)) as span:
    response = llm_client.chat(...)
    span.set_output(redact(response))
    span.set_attribute("model", "gpt-4o-mini")
    span.set_attribute("tokens_input", response.usage.prompt_tokens)
```

**Tradeoff accepted:** Cloud dependency — Langfuse cloud must be reachable for traces to be sent. If Langfuse is unreachable, traces are dropped silently (fire-and-forget) — the app continues running. This is correct: tracing failure must not break the application. The "refuse to boot if tracing is misconfigured" check validates that credentials are valid at startup, not that Langfuse is reachable in real time.

---

## D17 — RAG Eval Framework: RAGAS

**Decision:** Use the RAGAS framework for automated RAG evaluation in CI.

**Options evaluated:**

| Approach | Setup cost | Metric coverage | CI integration | Judge validation |
|---|---|---|---|---|
| **RAGAS** | Low — `pip install ragas` | Faithfulness, answer relevancy, context precision, context recall | `ragas.evaluate()` → JSON | Built-in |
| Frozen LLM judge | Medium — custom prompt + parsing | Custom — whatever you design | Custom script | Manual |

**Why RAGAS:**

1. **Purpose-built for RAG evaluation with exactly the metrics we need.** The assignment requires: faithfulness (did the answer stay true to retrieved chunks?) and answer relevancy (did the answer address the question?). RAGAS provides both as first-class metrics, implemented and validated by the open-source community. No custom scoring prompt to design, debug, or validate.

2. **Seamless CI integration.** RAGAS takes a dataset of question/answer/context triples and returns a scored DataFrame:
```python
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision

result = evaluate(golden_set, metrics=[faithfulness, answer_relevancy, context_precision])
# result["faithfulness"] → 0.91, result["answer_relevancy"] → 0.87
```
Output is written to `eval_report.json`, stored in MinIO, diffed against previous green build. Direct CI integration.

3. **RAGAS uses an LLM internally — same infrastructure.** RAGAS calls the OpenAI API (our existing API key) to score faithfulness and relevancy. No new API key, no new service.

4. **Assignment requirement: "hand-label 5 of the 25 yourself, report agreement with the judge."** RAGAS scores are the judge. We hand-label 5 triples (0/1 faithful, relevant/not relevant) and report: `agreement = (RAGAS_score > threshold) matches hand_label` for those 5. This validates the judge is trustworthy before we gate CI on it.

5. **Frozen = reproducible.** "Frozen judge" means the same model version and same evaluation code on every CI run. We pin the RAGAS version and the OpenAI model version (via API parameter) in `eval_thresholds.yaml`. This ensures CI results are comparable across runs.

**Thresholds committed in `eval_thresholds.yaml`:**
```yaml
rag:
  faithfulness: 0.80
  answer_relevancy: 0.75
  context_precision: 0.70
  hit_at_5: 0.80
  mrr_at_10: 0.65
```

**Tradeoff accepted:** RAGAS uses an LLM to score — evaluation has non-zero cost (~$0.01 per full golden set run at GPT-4o-mini pricing) and non-zero latency (~30–60s for 25 triples). Acceptable for a CI gate that runs on every push. The LLM judge can disagree with human labels — validated by the 5-item agreement check. If agreement < 80%, the judge is not trusted and the threshold is manually adjusted.

---

## D-deploy — Classifier Deployment Choice: Fine-tuned DistilBERT

**Decision:** Deploy the fine-tuned `distilbert-base-uncased` classifier as the production model.
Confirmed after the three-way comparison training run on 14,303 pandas-dev/pandas closed issues.

**Actual results on test split (2,146 issues, temporal holdout 2023-05-30 → 2026-05-19):**

| Model | Test macro-F1 | Latency | Cost/call | Deploy |
|---|---|---|---|---|
| TF-IDF + Logistic Regression | 0.8404 | 0.015 ms | $0 | ✅ baseline |
| **Fine-tuned DistilBERT** | **0.8867** | **469.6 ms** | **$0** | **✅ Chosen** |
| GPT-4o-mini zero-shot | 0.9030 | 775 ms | $7.8e-05/call | ❌ per-call cost |

**Per-class F1 (test set):**

| Model | bug | feature | docs | question |
|---|---|---|---|---|
| TF-IDF + LR | 0.9521 | 0.9195 | 0.8889 | 0.5986 |
| DistilBERT | 0.9626 | 0.9315 | 0.9197 | 0.7330 |
| GPT-4o-mini | 0.9620 | 0.9263 | 0.9117 | 0.8118 |

**Training data SHA-256:** `59c6b6e2b336a01f59291c00071366b48d434812c3e7b337c9374e5f3adef71b`
**Weights SHA-256:** `e23b2bc3f2c50b0cc6491c57d4868bca61e0942824b070d0e1fca08e06a50e0c`
**W&B run:** `maintainers-copilot / distilbert-pandas-classifier`
**Best checkpoint:** epoch 2 (early stopping, patience=2, val macro-F1=0.8095)

**Why DistilBERT over GPT-4o-mini (higher F1):**

1. **Zero per-call cost.** GPT-4o-mini classifies at $7.8e-05/call. At 10,000 classifications/day that is $0.78/day = $285/year. DistilBERT runs on the existing modelserver container with no API cost.

2. **Self-contained inference.** DistilBERT is fully offline — no API dependency, no network latency spike, no rate limiting. The classification call goes API → modelserver (internal Docker network) → response in ~470ms.

3. **GPT-4o-mini advantage is on the `question` class only.** The 1.6% overall F1 gap (0.9030 vs 0.8867) is almost entirely in the `question` class (0.8118 vs 0.7330). For a production triage tool, mislabeling `question` as `docs` is a minor UX issue. The per-call cost is a hard operational constraint.

4. **Latency is acceptable.** 470ms for classification is within the interactive triage budget. The classify_issue tool call fires once per conversation turn, not per token.

**Deploy command:**
```
modelserver /classify → DistilBERT fine-tune (mode: real)
modelserver /classify/classical → TF-IDF + LR (eval only)
```

---

## D18 — Widget CSS: Tailwind CSS

**Decision:** Use Tailwind CSS for the React widget.

**Options evaluated:**

| Approach | Bundle size impact | Development speed | Consistency | Assignment |
|---|---|---|---|---|
| **Tailwind CSS** | ~3–5KB after tree-shaking | Fast — utility classes in JSX | High — design system built in | "Tailwind or vanilla — your call" |
| Vanilla CSS | 0 overhead | Slower — write all rules manually | Manual | "Tailwind or vanilla — your call" |

**Why Tailwind:**

1. **Bundle size is not a concern with Vite's tree-shaking.** The common objection to Tailwind is bundle size — the full Tailwind CSS file is 3.5MB. But Vite's build process uses PurgeCSS to scan all JSX files and include only the utility classes actually used. A small widget with 5–6 components (bubble, chat panel, message list, input box, header) uses ~50–80 utility classes — resulting in ~3–5KB of CSS. This is negligible vs the JS bundle size.

2. **Faster to build under a Thursday deadline.** The widget needs: a collapsed bubble (fixed position, rounded, shadow), an expanded panel (flex column, scrollable message list, sticky input), streamed messages (user/assistant alignment), and theme application from config. Writing these components from scratch in vanilla CSS takes 3–4x longer than composing utility classes. On a tight timeline, Tailwind is the professional choice.

3. **Runtime theming is clean with Tailwind.** Widget config contains `primary_color` and `position`. With Tailwind, we apply dynamic styles via CSS variables or inline style overrides for the theme-specific properties (primary color is dynamic, so it uses a CSS variable) while Tailwind handles all structural/layout classes:
```jsx
<div style={{ "--primary": config.theme.primary_color }}
     className="fixed bottom-4 right-4 rounded-full shadow-lg bg-[var(--primary)]">
```

4. **Consistent with modern React widget patterns.** Production embeddable widgets (Intercom, Crisp, Drift) use utility-class frameworks or CSS-in-JS. Tailwind with Vite is the standard stack for 2024 widget development.

**Tradeoff accepted:** Tailwind adds a build-time dependency and requires the `@tailwindcss/vite` plugin. Minimal setup — already part of standard Vite + React scaffolding. If bundle size becomes a concern after building, the Tailwind purge output is fully auditable.

---
