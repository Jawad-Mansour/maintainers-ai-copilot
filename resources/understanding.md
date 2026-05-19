# Maintainer's Copilot — Understanding Notes

## Summary

**What we are building:** A chatbot for open-source maintainers that helps them triage GitHub issues faster. The maintainer pastes an issue and the copilot classifies it, extracts technical entities, summarizes the thread, answers questions from past docs and issues, and remembers across sessions.

**Two tracks, one project:**
- **Deep Learning**: three classifiers (classical ML + fine-tuned DistilBERT + LLM zero-shot) compared on the same test split. NER and summarizer as separate FastAPI tools.
- **LLM Engineering**: advanced RAG pipeline (smart chunking + hybrid retrieval + cross-encoder rerank + query transformation), single tool-calling LLM chatbot with short-term memory (Redis) and long-term memory (pgvector).

**Two frontends, one backend:**
- **Streamlit**: internal tool for admins — login, full chat, memory inspector, widget configuration
- **React widget**: production-shaped embeddable bundle — dropped into any host app with one script tag, themed at runtime, CORS/CSP origin allowlisted from the database

**Two CI gates that block merge:**
- Classification golden set: 25 hand-curated issues, macro-F1 + per-class F1 + confusion matrix across all three models
- RAG golden set: 25 question/answer/chunk triples, hit@5 + MRR@10 + faithfulness + answer relevancy. Thresholds committed in `eval_thresholds.yaml`.

**Architecture rules (non-negotiable):**
- Strictly layered: `api` → `services` → `repositories` → `domain`, with `infra` adapters
- All secrets from Vault at startup — `.env` holds only Vault root token and ports
- All blobs in MinIO — model artifacts, eval reports, training plots, chunk snapshots
- Redaction layer in `app/infra/` runs before every log line, trace span, and memory write
- Every LLM call, tool call, and RAG retrieval is a traced span — joinable with logs via trace ID
- Domain exception hierarchy mapped to clean HTTP responses — users never see stack traces
- App refuses to boot if Vault is unreachable, weights are missing/tampered, tracing is misconfigured, or any eval threshold is zero

**Ten services in docker-compose:** `api`, `chatbot`, `widget`, `modelserver`, `host`, `migrate`, `db` (postgres:16 + pgvector), `redis`, `minio`, `vault`

**Deadline:** Thursday 12pm. Friday is demo and presentation.

---

## What is a Maintainer?

A **maintainer** is the person responsible for managing an open-source software project on GitHub.

Their job includes:
- **Triaging issues** — reading bug reports, questions, feature requests, and deciding what to do with each one. Label it? Close it? Assign it? Duplicate?
- **Reviewing and merging pull requests** — deciding which code contributions get accepted
- **Setting direction** — what the project does and doesn't do
- **Keeping the repo healthy** — closing stale issues, writing docs, tagging releases

**Triage** comes from medicine — in an emergency room you sort by urgency, not arrival order. Maintainers do the same with issues. A popular repo can get hundreds of issues a week.

## What This Project Does

Builds a chatbot that sits next to a maintainer while they triage. The maintainer can paste an issue in, and the copilot:
- Classifies it (bug / feature / docs / question)
- Extracts key technical entities
- Summarizes long threads
- Answers questions like "has anyone reported this before?" by searching the project's history

Everything else in the project — the RAG, the classifier, the memory, the widget — exists to serve that one workflow.

## What the Copilot Does (Flow)

When a maintainer brings an issue to the copilot, this happens in order:

1. **Classify the issue** — reads the issue and puts it in one of four buckets: `bug`, `feature`, `docs`, `question`. Done using three different models that you compare for accuracy.

2. **Extract entities (NER)** — pulls out the important technical pieces from the issue text: function names, error codes, library names, version numbers, file paths. This is Named Entity Recognition. Instead of the maintainer reading the whole issue to find "oh they're using version 3.2.1 with the asyncio module", the copilot extracts that automatically.

3. **Summarize the thread** — GitHub issues often have 30 comments. The copilot reads the whole thread and produces a short summary. The maintainer gets the key points without reading everything.

4. **Answer questions using RAG** — the maintainer can ask "has this been reported before?" or "what did we say last time someone hit this error?" The copilot searches through the project's docs and past resolved issues to find relevant answers. This is the RAG part.

5. **Remember across conversations** — if the maintainer talked to the copilot yesterday, it remembers. It doesn't start from zero every time.

**Checklist of what the copilot can do:**
- [ ] Classify the issue into bug / feature / docs / question
- [ ] Extract technical entities from the issue text
- [ ] Summarize a long issue thread
- [ ] Answer questions by searching docs and past issues
- [ ] Remember context across multiple conversations

Each of these becomes a **tool** the chatbot can call. That is the architecture.

## The Embeddable Widget

The copilot is not just a standalone app you visit in a browser. It is built to be dropped into any other website or app — like a chat bubble you see in the corner of a SaaS product.

How it works:
1. The chatbot UI is built as a small React bundle (a single JS file)
2. Any host app pastes one `<script>` tag into their HTML
3. That script injects an iframe with the chat widget
4. The widget connects to your backend API

A simple host app is built just to demo this on Friday.

## Golden Evals that Fail CI

CI (Continuous Integration) is the automated pipeline that runs every time you push code. Here it also runs **evals** — quality checks on the AI components.

- **Golden** means hand-curated: 25 examples for classification and 25 for RAG that represent correct expected behavior
- **Fail CI** means: if the classifier's accuracy drops below a committed threshold, the build breaks — you cannot merge. Exactly like a failing unit test, except it measures model quality not code correctness

The three things that have evals: the **classifier**, the **retrieval** (RAG), and the **generation** (the LLM's answers).

## The Chatbot

**One LLM, not a pipeline, not multiple agents.** The chatbot is one LLM that reads the conversation and decides which tool to call next. It reasons about what the maintainer needs and picks the right tool — not a hardcoded workflow.

### Authentication

**fastapi-users with JWT**: library that handles registration, login, session management. JWT = signed token the server gives you at login. You send it with every request to prove who you are. No token = no access.

**JWT signing key from Vault**: the key used to sign/verify JWTs lives in Vault, not in code or `.env`. Fetched at startup. Vault down = app refuses to boot.

**Two roles**: `user` (can chat) and `admin` (can chat + invite users + configure widgets).

### Tools

The LLM calls your classifier, NER, summarizer, and RAG as tools — structured requests the framework executes and returns results from:
```
Maintainer: "What kind of issue is this? [pastes issue]"
LLM → calls classify_issue("crash in asyncio.run...")
    ← returns {"label": "bug", "confidence": 0.94}
LLM → "This looks like a bug — asyncio crash on Python 3.11..."
```

**`write_memory` — no auto-writes**: the LLM cannot write to long-term memory on its own. The maintainer must explicitly ask. Safety design — no silent permanent writes.

**Prompts as files in `prompts/`**: system prompt and tool descriptions are `.txt`/`.md` files committed to git — tracked, reviewable, reversible like code.

### Memory

**Short-term (Redis + TTL)**: current conversation history. Extremely fast in-memory store. Expires after a set time (e.g. 24 hours of inactivity). TTL must be chosen and justified.

**Long-term (Postgres + pgvector)**: persists across sessions. At least one of:
- **Episodic**: specific past events — "last Tuesday the maintainer resolved an asyncio bug by..."
- **Semantic**: general facts — "this repo treats `help wanted` as `question`"
- **Procedural**: preferences — "maintainer prefers bullet-point summaries"

Pick one type, implement it, defend the choice in DECISIONS.md.

**Every long-term write → audit log row**: `actor`, `action`, `target`, `timestamp`. Permanent record, no exceptions.

**Cross-conversation recall demo on Friday**: show live that the copilot remembers something from a previous session.

---

## Advanced RAG

**The baseline you must beat**
Naive fixed-size chunking + pure-dense retrieval. Split every 500 chars, embed, search by vector similarity only. Every improvement must be justified with a number showing it beats this baseline on your 25 golden RAG triples.

**Smart chunking — not naive fixed-size**
Split at meaningful boundaries: sentences, paragraphs, or semantic units. A chunk should be a coherent thought, not a random slice cut mid-sentence. Better chunks = better retrieval.

**Hybrid retrieval — sparse + dense**
```
Query: "asyncio timeout error"
│
├── Dense search  → vector similarity → finds semantically similar text
│                   ("event loop", "coroutine timeout" match even without exact words)
│
└── Sparse search → BM25 keyword match → finds exact word matches
                    ("asyncio", "timeout" exact hits score high)

Combined score = α × dense_score + (1-α) × sparse_score
You tune α on your golden set.
```

**Cross-encoder reranking**
Hybrid search returns top-k chunks fast but imprecisely. A cross-encoder then reads the query AND each chunk together and re-scores them. Much more accurate but slower — so you only run it on the top-k, not the whole corpus.

**Query transformation**
Rewrite the user's question before searching:
- **Query expansion**: add related terms
- **HyDE** (Hypothetical Document Embedding): generate a fake ideal answer, embed it, search with that
- **Step-back prompting**: make the question more general first

**Metadata filtering**
Each chunk has metadata: source file, date, issue number, label. You filter before searching — "only search resolved bug issues." Narrows the search space before vector comparison.

**Vector store: pgvector or Qdrant**
Where embedded chunks live. pgvector is a Postgres extension — simpler stack. Qdrant is a dedicated vector database — more features. Either is acceptable.

> Reference: `resources/Advanced_RAG_Techniques_Guide.docx` — use this during brainstorming to decide which specific techniques to implement.

---

## Evaluation

**Two golden sets. Two CI gates. Thresholds committed in `eval_thresholds.yaml`.**

### Classification Eval
- 25 hand-curated issues, separate from the test split
- Run against all three models (classical ML, fine-tuned, LLM)
- Reports: Macro-F1, per-class F1, confusion matrix
- **Confusion matrix**: a table showing where the model gets confused — e.g. it mistakes "question" for "docs" 30% of the time

### RAG Eval
- 25 question/answer/chunk triples
- Two types of metrics:

| Type | Metric | What it measures |
|---|---|---|
| Retrieval | hit@5 | Was the right chunk in the top 5 results? |
| Retrieval | MRR@10 | How high up was the right chunk ranked? |
| Generation | Faithfulness | Did the LLM's answer stay true to the retrieved chunks? |
| Generation | Answer relevancy | Did the answer actually address the question? |

- **RAGAS or a frozen judge model**: use the RAGAS framework to auto-score, or use an LLM as a judge. You hand-label 5 of the 25 yourself and report how often you agreed with the judge — this validates the judge is trustworthy.

**`eval_report.json`** is written on every CI run, stored in MinIO, and diffed against the previous green build. If any metric drops below the committed threshold — merge is blocked.

---

## Dataset

**Where the data comes from**
You pick one open-source GitHub repo. You download all its closed issues. That becomes your entire dataset. One repo, one dataset, you live with it.

**Classification labels**
GitHub issues don't come labeled "bug" or "feature" — maintainers apply their own tags like `type: bug`, `enhancement`, `documentation`, `help wanted`. You map those repo-specific labels to the four standard buckets: `bug / feature / docs / question`. That mapping is written and defended in DECISIONS.md.

**Splits**
You divide the dataset into three parts: Train (model learns), Val (you tune), Test (final evaluation). Two rules:
- **Stratified**: each split has the same proportion of bug/feature/docs/question — no accidental imbalance
- **Test is strictly more recent in time than train**: train on past issues, test on future ones. Simulates real deployment and prevents data leakage.

**RAG corpus — completely separate from classifier data**
The RAG system searches through:
- The project's documentation (README, wiki, docs folder)
- A held-out slice of resolved issues with maintainer answers

The hard rule: **held-out issues cannot appear in classifier training.** If the classifier trains on issues that also exist in the RAG corpus, it memorizes those specific cases and gives fake-good metrics — it looks accurate but it's cheating. This is called data leakage.

**Golden sets — 25 + 25, hand-curated**
- **Classification golden set**: 25 issues you personally read and labeled. Used as the CI gate — if the classifier gets these wrong after a code change, the build breaks.
- **RAG golden set**: 25 question/answer/chunk triples you personally built. A triple looks like this:

```
Question:  "How do I configure the retry timeout?"
Answer:    "Set retry_timeout in config.yaml under the [client] section"
Chunk:     The paragraph from docs/configuration.md that contains exactly that
```

The eval checks two things: did RAG retrieve the right chunk? Did the LLM produce the right answer from it?

Hand-curated means you read, wrote, and verified each one yourself. No automation.

**Visual: how the data is split**

```
ALL CLOSED ISSUES FROM YOUR CHOSEN REPO
│
├── CLASSIFIER DATA
│   ├── Train set   ──► model learns from this (older issues)
│   ├── Val set     ──► you tune hyperparameters on this
│   ├── Test set    ──► final evaluation (newer issues, time-based)
│   └── Golden set  ──► 25 hand-curated issues, CI gate (separate from test)
│
└── RAG CORPUS  (NEVER touches classifier training)
    ├── Project docs (README, wiki, docs/)
    ├── Held-out resolved issues with maintainer answers
    └── Golden set  ──► 25 hand-built Q/answer/chunk triples, CI gate

WALL: nothing from the RAG corpus crosses into classifier training
```

---

## Embeddable Widget

Two surfaces, one backend API:
```
┌─────────────────────────────┐     ┌──────────────────────────────┐
│  STREAMLIT (internal tool)  │     │  REACT WIDGET (production)   │
│  - Login page               │     │  - Collapsed bubble          │
│  - Full chat                │     │  - Expands to chat panel     │
│  - Memory inspector         │     │  - Streamed messages         │
│  - Widget config for admins │     │  - Themed at runtime         │
└────────────┬────────────────┘     └──────────────┬───────────────┘
             └──────────────────┬──────────────────┘
                         FastAPI backend
```
Streamlit = fast internal tool. React = small bundle, embeddable in any host app. Cannot embed Streamlit in an iframe cleanly — wrong tool.

**The React Widget**: built with Vite, output is a single bundled JS file. Lean size matters — graders check it. Theme (colors, position) is NOT hardcoded — fetched from widget config at load time. `postMessage` is used for iframe-to-host communication, minimum for iframe resize.

**Widget Configuration** — a `widget` table in Postgres:

| Field | Example |
|---|---|
| `widget_id` | `abc123` (public, used in script tag) |
| `allowed_origins` | `["https://myapp.com"]` |
| `theme` | `{"primary_color": "#FF5500", "position": "bottom-right"}` |
| `greeting` | `"Hi, I'm your maintainer copilot"` |
| `enabled_tools` | `["classify", "rag"]` |

Admins create/edit these in Streamlit and get a generated embed snippet.

**Embed Flow:**
```
1. Admin creates widget config → gets snippet:
   <script src="https://api/widget.js" data-widget-id="abc123"></script>

2. Host pastes that one script tag into their HTML

3. /widget.js runs in host browser:
   → reads data-widget-id
   → injects <iframe> pointing at React widget bundle

4. React widget boots:
   → fetches config from API using widget_id
   → applies theme, greeting, enabled tools
   → renders chat bubble

5. User clicks → chat opens → talks to FastAPI backend
```

**Origin Allowlisting:**
```
allowed_origins: ["https://myapp.com"]

→ myapp.com loads widget    ✅ CORS + CSP frame-ancestors allows it
→ evilsite.com loads widget ❌ browser blocks it (CSP violation)
```
- **CORS**: controls which domains can make API requests — list comes from DB, not hardcoded env var
- **CSP `frame-ancestors`**: controls which pages can embed the widget in an iframe — set as a response header

Friday demo: show widget loading on allowed host, then show browser blocking it on a non-allowed host — real browser console output.

---

## Observability & Safe Logging

All three apply to every service.

### Tracing

A trace is a recording of everything that happened during one user request, structured as a tree:
```
User message: "What kind of issue is this?"
│
├── span: LLM call (model=gpt-4o, tokens=342, latency=1.2s)
│   └── span: tool call → classify_issue
│       └── span: HTTP → modelserver /classify (latency=0.3s)
│
└── span: LLM call (final response, tokens=89, latency=0.4s)
```
Every LLM call, tool call, and RAG retrieval is a **span** (one timed unit). A conversation is a **trace** — a tree rooted at the user message.

Each span records: model name, token counts, latency, tool inputs/outputs — **after redaction only**.

The **trace ID** is written into every log line for that request. Logs and traces are joinable by trace ID — if you see an error in logs, you find the exact trace in the UI.

Friday demo: walk through a real conversation's trace tree including one that hit an error path.

### Redaction

Before anything leaves the service (log line, trace span, memory write) — a redaction layer strips sensitive patterns:
```
sk-[a-zA-Z0-9]{48}   → [REDACTED_OPENAI_KEY]
ghp_[a-zA-Z0-9]{36}  → [REDACTED_GITHUB_TOKEN]
password=\S+          → [REDACTED_PASSWORD]
```
Lives in `app/infra/`, used by every service. Patterns defined and defended in SECURITY.md.

Explicitly tested: a test asserts a message containing a fake API key never appears unredacted in logs, traces, or memory.

### Exception Handling

Two distinct layers:
- **Infrastructure exceptions**: raw library errors (`sqlalchemy.exc.OperationalError`) — internal, never shown to users
- **Domain exceptions**: your hierarchy (`NotFoundError`, `PermissionDenied`, `ToolFailure`) — meaningful, controlled

Single exception handler at the API boundary maps everything to a clean response:
```json
{"error": "not_found", "message": "Issue not found", "request_id": "abc-123"}
```
Users never see a stack trace.

**Tool failures are caught and recovered**: if the classifier is down, the chatbot says so and continues — it does not 500.

Every uncaught exception is logged with both trace ID and request ID.

---

## The Deep Learning Track

### Text Processing & Representations

**Preprocessing pipeline**
Raw GitHub issues are messy. Before feeding them to any model, you clean them: strip HTML tags, remove code blocks, normalize whitespace, handle empty issues and duplicates. Every choice is defended with a reason in DECISIONS.md.

**Embedding model choice for RAG**
You pick which model converts your docs/issues into vectors for search. You test at least two and compare retrieval quality on your 25 golden RAG triples. The one that scores higher on hit@5 or MRR wins. That number goes in DECISIONS.md.

### Fine-Tuning a Transformer

**Track training with a real run logger**
While training DistilBERT, you log every epoch's loss, accuracy, and learning rate to a proper experiment tracker (Weights & Biases or MLflow) — not just print statements.

**Model card**
A metadata JSON file saved alongside the weights. Contains: architecture, hyperparameters, training data SHA-256 hash, final metrics. The API checks the SHA-256 at boot — mismatch = refuses to start.

**Freeze policy**
Which layers did you freeze during fine-tuning and why? Linear probe, partial unfreeze, or full fine-tune? Documented and justified with a number in DECISIONS.md.

### NLP Pipelines as Tools

**NER — integration only**
Use an existing model (spaCy or HuggingFace pipeline). Don't train one. Wrap it behind a FastAPI endpoint in the `modelserver` container.

**Summarizer — pre-trained or LLM-driven**
Either a pre-trained model (BART, T5) or an LLM call with a summarization prompt. Also behind a FastAPI endpoint in `modelserver`.

**The chatbot calls both over HTTP — never imports them directly:**
```
chatbot  ──HTTP──►  modelserver /classify
                    modelserver /ner
                    modelserver /summarize
```

### ML vs DL vs LLM — Three-Way Comparison (Required)

All three run on the same test split. You report:

| Metric | What it means |
|---|---|
| Accuracy | % of issues classified correctly overall |
| Macro-F1 | Average F1 across all 4 classes, weighted equally |
| Per-class F1 | F1 for each of bug / feature / docs / question separately |
| Latency | How long does one prediction take? |
| Cost | What does running this model cost per prediction? |

**F1** balances precision (when you say "bug", how often are you right?) and recall (of all real bugs, how many did you catch?). Macro-F1 averages across all classes so rare classes count equally.

You pick one to deploy and defend why in DECISIONS.md.

---

## Architecture — Three Columns

**XFMR = Transformer** (just an abbreviation).

**Classical ML is required** — not a design choice. The assignment mandates all three models for a three-way comparison.

### Column 1 — Deep Learning

**Classical ML**
The simplest classifier. Convert issue text to numbers (TF-IDF), train a model like Logistic Regression or SVM to predict bug/feature/docs/question. No neural network. Fast, cheap, interpretable. This is the baseline — required by the assignment.

**Fine-tuned Transformer (XFMR)**
A small pre-trained language model (DistilBERT) that already understands language. You continue training it on your specific issue dataset so it learns GitHub issue patterns. Better accuracy than classical ML but slower and heavier.

**LLM baseline**
Give GPT or Claude the issue text and ask it to classify with a prompt. No training, no fine-tuning. Zero-shot. The third required model in the comparison.

**NER (Named Entity Recognition)**
A technique that reads text and labels specific words as entities. For issue text: function names, error codes, library names, version numbers, file paths. Example — from "crash in `asyncio.run()` on Python 3.11", NER extracts `asyncio.run()` and `Python 3.11`. The maintainer sees the key technical pieces without reading the full issue.

**Summarizer**
Takes a long issue thread (30 comments) and produces a short paragraph of key points. Either a pre-trained summarization model or an LLM call.

### Column 2 — Advanced RAG

**Chosen embeddings**
To search docs and past issues, you convert all text into vectors (lists of numbers) that capture meaning. Similar text gets similar vectors. You pick which embedding model to use and prove with a number on your golden set that it outperforms at least one alternative.

**Smart chunking**
Before embedding, you split docs into pieces. Naive = every 500 characters. Smart = split at sentence or paragraph boundaries, or by semantic meaning, so each chunk is a coherent unit.

**Hybrid + rerank**
- **Hybrid**: search with two methods simultaneously — dense (vector similarity) and sparse (keyword matching, BM25). Combine their scores.
- **Rerank**: take the top-k results from hybrid and run a second, more expensive cross-encoder model that re-scores them for true relevance. More accurate than the first pass alone.

**Query rewrite**
Transform the user's question before searching. "Did anyone fix this?" becomes "resolved issues related to asyncio crash Python 3.11". Better query = better retrieval.

### Column 3 — Chatbot + Embed

**Single tool-calling LLM**
One LLM that reads the conversation and decides which tool to call. Not multiple agents — one model with a set of tools available to it (classifier, NER, summarizer, RAG).

**Short-term memory (Redis)**
The current conversation history. Stored in Redis with a TTL (time-to-live). When the session expires, it's gone. Fast, in-memory.

**Long-term memory (pgvector)**
Things the maintainer wants remembered across sessions. Stored in Postgres with vector search. Example: "always flag asyncio issues as high priority." Persists until explicitly deleted. Every write produces an audit log row.

**Streamlit admin + React widget**
Two frontends sharing one backend API. Streamlit is the internal tool (admin config, memory inspector, authenticated chat). React widget is the embeddable production surface (small bundle, drops into any host app with one script tag).

### Bottom — Eval harness + traces + redacted logs fail CI
Everything is tested on every push. Evals run the golden sets. Traces record every LLM/tool/RAG call. Logs are redacted before anything leaves the service. Regression below threshold breaks the build.

---

## NLP Concepts — Text to Numbers

Before a model can classify text, it must convert words into numbers. Here is the progression from simplest to most powerful.

### Bag of Words (BOW)
Build a vocabulary of every word in your dataset. Each issue becomes a vector where each position is the count of that word.

Vocabulary: `[bug, crash, feature, request, asyncio]`
Issue: "bug in asyncio" → `[1, 0, 0, 0, 1]`

Problems:
- Word order is lost: "dog bites man" == "man bites dog"
- No understanding of meaning: "bug" and "error" are completely unrelated
- Common words like "the" dominate even though they carry no information

### TF-IDF (Term Frequency — Inverse Document Frequency)
Improvement over BOW. Each word gets a score from two factors:

- **TF**: how often does this word appear in THIS document?
- **IDF**: how rare is this word across ALL documents?

"the" appears everywhere → low IDF → low score. "asyncio" appears in few issues → high IDF → high score when it appears.

Still loses word order. Still no semantic understanding. But better signal than raw counts. Used with classical ML models like Logistic Regression.

### Word Embeddings
Instead of counting words, map each word to a dense vector of numbers (e.g. 300 dimensions). Similar words get similar vectors. "bug" and "error" are close in vector space. The math works: king − man + woman ≈ queen.

Problem: each word has **one fixed vector** regardless of context. "crash" means the same thing in "app crash" and "car crash."

### Transformers
Solves the context problem. Key idea — **self-attention**: every word looks at every other word in the sentence to understand its meaning in context. "bank" near "money" gets a different representation than "bank" near "river."

Transformers are pre-trained on massive text (Wikipedia, books, web) to learn general language patterns.

### BERT and DistilBERT
**BERT** (Bidirectional Encoder Representations from Transformers): reads text in both directions simultaneously, produces contextual embeddings where each word's representation depends on the full sentence.

**DistilBERT**: a smaller, faster version of BERT — 40% smaller, 60% faster, retains 97% of BERT's performance. Trained via knowledge distillation — the smaller model learns to mimic BERT's outputs.

This is what we fine-tune for issue classification.

### Fine-tuning
Take a pre-trained model (DistilBERT) that already understands language. Add a classification head on top (a small layer outputting bug/feature/docs/question). Train on your labeled issue dataset. The model adapts its general knowledge to your specific domain. Works with a few thousand examples because general language understanding is already baked in.

### Freeze Policy
When fine-tuning, you choose which layers to update:
- **Linear probe**: freeze all of DistilBERT, only train the classification head. Fastest, cheapest.
- **Partial unfreeze**: freeze early layers, train later layers + head. Middle ground.
- **Full fine-tune**: update all weights. Most expensive, highest potential accuracy.

Must be documented and defended in DECISIONS.md.

### The Three-Way Comparison (Required)

| Model | Approach | Cost | Speed |
|---|---|---|---|
| Classical ML (TF-IDF + Logistic Regression) | Counts words, no semantic understanding | Very cheap | Very fast |
| Fine-tuned DistilBERT | Contextual understanding, trained on your data | Medium | Medium |
| LLM baseline (zero-shot prompt) | No training, API call | Per-call cost | Slow |

All three run on the same test split. You report accuracy, macro-F1, per-class F1, latency, cost — then defend which one you would deploy.

---

## Engineering & Architecture

The codebase is strictly layered. Every request flows down — no layer skips another:

```
HTTP request
     │
 app/api/          → routing only. No DB, no Redis, no external calls.
     │
 app/services/     → business logic, transactions, cache invalidation
     │
 app/repositories/ → SQL only. No HTTP errors. No cache logic.
     │
 app/domain/       → Pydantic models (data shapes). NOT SQLAlchemy models.
     │
 app/infra/        → adapters: Vault, MinIO, Redis, LLM, modelserver,
                     tracing backend, redaction layer
```

Graders will ask you to add a new endpoint live on Friday. If your router touches SQLAlchemy directly — you fail.

### Secrets
- Every secret in Vault: LLM API keys, JWT signing key, DB password, MinIO credentials, tracing keys
- `.env` holds **only** the Vault root token and ports
- `grep -ri 'sk-' app/` and `grep -ri 'password' app/` return zero matches outside Vault-reading code
- App refuses to boot if Vault is unreachable

### Blob & Database

**MinIO holds:** model artifacts, `eval_report.json` from every CI run, training plots, per-conversation retrieved-chunks snapshots for last N conversations.

**Postgres 16 with pgvector:** schema lives in Alembic migrations only. A `migrate` container runs `alembic upgrade head` and exits **before** `api` boots — schema is always current before the app starts.

**Audit log table:** role changes, memory writes, widget config changes, conversation deletions.

### Refuse to Boot

`api` refuses to start if ANY of these are true:

| Condition | Why |
|---|---|
| Vault unreachable | Cannot fetch any secrets |
| Classifier weights missing | Core feature unavailable |
| Weights SHA-256 doesn't match model card | Corrupted or tampered weights |
| Tracing backend misconfigured | Running blind with no observability |
| Any eval threshold is zero or disabled | CI gate accidentally disabled |

Not a warning — the process exits. Prevents silent broken state.

---

## Redis — Purpose in This Project

No workers, no job queue. Redis serves two purposes only:

1. **Short-term conversation memory** — last N messages of the current session, stored with a TTL
2. **Cache** — API response caching (e.g. `GET /me`, recent conversations)

The flow is fully synchronous — no background processing needed:
```
User sends message
       │
     api
       │──HTTP──► modelserver /classify  (waits for response)
       │──HTTP──► modelserver /ner       (waits for response)
       │──HTTP──► RAG pipeline           (waits for response)
       │
     returns answer to user
```

The `modelserver` is a FastAPI service that responds immediately. No job queue, no workers.

**Contrast with week 6:** week 6 used Redis as an RQ job queue with two worker containers (`worker` and `sftp-ingest`) consuming jobs asynchronously. Week 7 has none of that — no `worker` container in the compose stack.

---

## Compose Stack

Ten containers, each with one job:

```
┌─────────────────────────────────────────────────────────────┐
│                     docker-compose up                       │
│                                                             │
│  api          FastAPI — auth, chat, memory, RAG, widgets    │
│  chatbot      Streamlit — admin UI, memory inspector, chat  │
│  widget       Static server — React bundle + loader script  │
│  modelserver  FastAPI — classifier, NER, summarizer         │
│  host         nginx — demo host app for Friday              │
│  migrate      Alembic upgrade head, then exits              │
│  db           postgres:16 with pgvector                     │
│  redis        redis:7 — short-term memory + cache           │
│  minio        minio/minio — blob storage                    │
│  vault        hashicorp/vault — secrets, dev mode           │
└─────────────────────────────────────────────────────────────┘
```

**Startup order matters:**
1. `vault` and `db` and `redis` and `minio` start first
2. `migrate` runs `alembic upgrade head` then exits
3. `api`, `modelserver`, `chatbot`, `widget`, `host` start

`docker-compose up` from a fresh clone after `cp .env.example .env` and filling in the Vault root token. That's the only setup required.

**CI on every push runs:** lint → type-check → build images → both eval suites → redaction test → smoke-test of the full stack.

---

## Brainstorming Decisions

### All 18 Decisions — Summary Table

| # | Category | Choice | Why |
|---|---|---|---|
| D1 | Dataset | **pandas-dev/pandas** | Only repo with all 4 classes + 1,656 question samples. scikit-learn had 119 (~83 train — too few). huggingface/transformers had zero question class. |
| D2 | ML — Encoder | **distilbert-base-uncased** | Assignment says "small encoder". Trains ~8 min/epoch on Colab T4 vs 18 min RoBERTa. 14,869 samples sufficient. F1 lever is class weighting, not model size. |
| D3 | ML — Freeze | **Full fine-tune** | GitHub issues ≠ Wikipedia (pre-training domain). Freezing early layers leaves [CLS] tuned for prose. Full fine-tune at lr=2e-5 gives ~5% macro-F1 gain over linear probe. |
| D4 | ML — Logger | **Weights & Biases** | Zero infra — no new container. Native Colab integration. Free tier. Best UI for Friday demo. MLflow needs an 11th compose service. |
| D5 | ML — NER | **spaCy + EntityRuler** | Code entities (versions, exceptions, file paths) have deterministic syntax — regex captures them perfectly. HuggingFace NER trained on news text, misses all software entities. 12MB vs 250–400MB. |
| D6 | ML — Summarizer | **LLM call** | BART/T5 trained on CNN/DailyMail news — wrong domain. LLM infra already exists. Adds 0 bytes to modelserver vs 1.6GB for BART-large. Prompt versioned in `prompts/`. |
| D7 | RAG — Embeddings | **text-embedding-3-small** | MTEB 62.3 — matches bge-small. Entire corpus costs $0.05 to embed. Same OpenAI API key. Adds 0MB to modelserver. 3-large costs 6.5x more for 2.3 MTEB points — not justified. |
| D8 | RAG — Chunking | **Hierarchical parent-child** (256 / 1024 tokens) | Fixed-size is the baseline to beat. Small chunks = precise embeddings. Large chunks = rich LLM context. Hierarchical gives both: search children, return parent to LLM. |
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

---

### Decision 1 — Dataset Repo: pandas-dev/pandas

**Options considered:** `huggingface/transformers`, `pandas-dev/pandas`, `scikit-learn/scikit-learn`

**Actual label distribution (closed issues, from GitHub label filter screenshots):**

| Repo | Bug | Feature | Docs | Question | Total |
|---|---|---|---|---|---|
| pandas | 7,881 (53%) | 2,989 (20%) | 2,343 (16%) | 1,656 (11%) | 14,869 |
| scikit-learn | 2,108 (51%) | 551 (13%) | 1,371 (33%) | 119 (3%) | 4,149 |
| huggingface/transformers | 2,973 (86%) | 408 (12%) | 67 (2%) | 0 | ~3,448 |

**Why pandas:**
- All four classes present with clean mappings
- Largest dataset (14,869 vs 4,149 vs ~3,448)
- Question class is 11% — ~1,159 training samples after split. scikit-learn's question class (119 total → ~83 train) is too small to learn reliably
- Best overall balance — no class below 11%

**Label mapping:**
- `Bug` → `bug`
- `Enhancement` + `Ideas` → `feature`
- `Docs` → `docs`
- `Usage Question` → `question`

**Tradeoff accepted:** `Usage Question` has mild noise — some issues carry both `Bug` and `Usage Question`. Fix: drop dual-labeled issues during preprocessing.

### Decision 2 — Fine-tuning Encoder: distilbert-base-uncased

**Decision:** Fine-tune `distilbert-base-uncased` for 4-class issue classification.

**Options considered:** `distilbert-base-uncased`, `distilroberta-base`, `bert-base-uncased`, `roberta-base`

**Why distilbert-base-uncased:**
- Task is 4-class short-text classification — the vocabulary gap between `bug`, `feature`, `docs`, `question` is large and obvious. DistilBERT is sufficient; heavier models add no measurable value here.
- Dataset (14,869 samples, ~10,400 train) is well within distilbert's capacity. RoBERTa's edge comes from low-data scenarios — not the case here.
- The real F1 lever is class weighting in the loss, not model size.
- Training on Google Colab (T4 GPU): distilbert ~8 min/epoch vs roberta ~18 min/epoch. Faster iteration = more debugging cycles within the Thursday deadline.
- Assignment says "small encoder" — distilbert is the canonical choice.

**How it will be used:**
```
Input: issue title + "[SEP]" + issue body → truncated to 512 tokens
  ↓
distilbert-base-uncased (HuggingFace pre-trained weights)
  ↓
[CLS] token (768-dim) → Dropout(0.1) → Linear(768→4) → Softmax
  ↓
bug / feature / docs / question
```
- Optimizer: AdamW, lr=2e-5, weight_decay=0.01
- Loss: CrossEntropyLoss with inverse-frequency class weights
- Epochs: 3–5, early stopping on val macro-F1
- Batch size: 16 (Colab T4 limit)
- Weights saved to MinIO with model card (SHA-256 of training data + final metrics)

**Tradeoff accepted:** distilroberta-base would score ~1–2% higher macro-F1. Not worth slower training on this deadline — hyperparameter tuning and class weighting close that gap.

---

### Decision 3 — Freeze Policy: Full Fine-tune

**Decision:** Fully fine-tune all 6 transformer blocks + classification head.

**Options:** Linear probe (head only) → Partial unfreeze (last 2 blocks + head) → Full fine-tune (all layers)

**Why full fine-tune:**
- ~10,400 training samples is enough — full fine-tune converges without overfitting at this size
- GitHub issues are a different domain from Wikipedia/BookCorpus (DistilBERT's pre-training). Early layers need to adapt to error messages, code patterns, and technical vocabulary. Freezing them leaves the [CLS] representation tuned for prose, not issues.
- lr=2e-5 with linear warmup prevents catastrophic forgetting — early layers barely move, they just adapt
- Expected improvement over linear probe: ~5% macro-F1. That is the number we defend in DECISIONS.md.
- Training time: 3 epochs × 8 min = ~24 min on Colab T4. Acceptable.

**Tradeoff accepted:** Risk of overfitting if training runs too long. Mitigated by early stopping (patience=2 on val macro-F1).

### Decision 4 — Run Logger: Weights & Biases (W&B)

**Decision:** Use W&B for experiment tracking during fine-tuning.

**Why W&B over MLflow:**
- MLflow needs a 11th container in the compose stack or an external server — extra ops overhead we don't need
- W&B is cloud-hosted: `wandb.init()` in Colab and training is tracked instantly. No infra to manage.
- Free tier covers unlimited runs. Native Colab integration.
- Better demo artifact: live loss curves, confusion matrix, hyperparameter comparison tables visible in the W&B dashboard
- Industry standard for GPU training experiments

**What gets logged:** train/val loss, train/val macro-F1, per-class F1, confusion matrix, all hyperparameters. Final weights also saved to MinIO per architecture requirement.

### Decision 5 — NER: spaCy + EntityRuler

**Decision:** spaCy `en_core_web_sm` + custom `EntityRuler` patterns for code-shaped entity extraction.

**Why not HuggingFace NER pipeline:**
- All available HuggingFace NER models (dslim/bert-base-NER, elastic/distilbert-NER) were trained on CoNLL-2003 news text: PERSON, ORG, LOC, DATE. They do not recognize `ValueError`, `DataFrame.merge()`, `pandas==2.0.1`, or `/pandas/core/frame.py` — because they were never trained on software text.
- Adding a HuggingFace NER model to modelserver adds 250–400MB on top of distilbert's 250MB. spaCy `en_core_web_sm` is 12MB.

**Why spaCy + EntityRuler:**
- "Code-shaped entities" have deterministic syntax — version numbers, Python exceptions, file paths, function calls. Regex captures them with 100% precision. Neural models add noise without adding value here.
- Deterministic = unit testable. EntityRuler always produces the same output for the same input.
- spaCy EntityRuler is designed for this: define patterns, plug into pipeline, done.

**Entities extracted:** VERSION (`v2.0.1`, `Python 3.11`), EXCEPTION (`ValueError`, `TypeError`), FILEPATH (`pandas/core/frame.py`), FUNCTION (`DataFrame.merge(`), PACKAGE (`pandas`, `numpy`, etc.)

### Decision 6 — Summarizer: LLM Call

**Decision:** Use an LLM API call with a structured prompt in `prompts/summarize.md`. No pre-trained summarization model in modelserver.

**Why not BART/T5:**
- Every pre-trained summarization model (BART-large-CNN, T5, DistilBART) was fine-tuned on CNN/DailyMail news articles. GitHub issue threads are a completely different domain — these models produce poor output on technical discussions with stack traces and code.
- BART-large adds 1.6GB to modelserver. LLM call adds 0 bytes.

**Why LLM call:**
- LLM infrastructure already exists for the chatbot — same client, same Vault secret, zero new infra
- Quality: instruction-tuned LLMs understand GitHub issues natively. The prompt specifies structure: problem, environment, error, resolution.
- Prompt lives at `prompts/summarize.md` — version-controlled, reviewable, changeable without redeployment
- Cost: ~$0.0001/call at GPT-4o-mini pricing — negligible
- Graceful fallback if LLM is unavailable: returns `null` summary, chatbot continues without 500ing
- Result cached in Redis by issue_id — same thread not re-summarized in a session

---

### Decision 7 — Embedding Model: text-embedding-3-small

**Decision:** OpenAI `text-embedding-3-small`. Tested against `BAAI/bge-small-en-v1.5` on golden set — winner deployed.

**Why:**
- MTEB score: 62.3 (vs bge-small 62.2) — essentially tied in quality
- Corpus embedding cost: ~2.5M tokens × $0.02/1M = **$0.05 total** — negligible
- Reuses existing OpenAI API key in Vault — zero new infra
- Keeps modelserver memory lean: API call adds 0 bytes vs bge-small adding +90MB to an already loaded container
- `text-embedding-3-large` scores higher (64.6) but costs 6.5x more — not justified
- Ada-002 is legacy and worse on MTEB — eliminated

**Tradeoff accepted:** API dependency. If OpenAI embedding API is down, query-time embedding fails. Graceful fallback returns "search unavailable" without crashing.

### Decision 8 — Chunking: Hierarchical Parent-Child

**Decision:** Child chunks (256 tokens) for embedding/retrieval. Parent chunks (1024 tokens) returned to the LLM.

**Why:**
- Fixed-size chunking is the baseline to beat — eliminated by requirement
- The core tradeoff: small chunks = precise embeddings but no LLM context. Large chunks = rich context but blurry embeddings. Hierarchical solves both.
- Search over children (sharp, 256-token embeddings) → return parent (1024 tokens of rich context) to LLM
- RAG guide: "If a single technique in this guide is worth implementing, it is probably this one."
- Applied to docs: parent = full section, children = paragraphs. Applied to issues: parent = full issue + comments, children = individual chunks.

### Decision 9 — Vector Store: pgvector + HNSW

**Decision:** pgvector (already in the stack) with HNSW index. No Qdrant.

**Why:**
- Already required by the assignment. Adding Qdrant = 11th container, new port, new Vault secret, new adapter.
- pgvector 0.5+ has HNSW — same algorithm as Qdrant/Pinecone. Query latency <10ms for our ~50K chunk corpus.
- Hybrid retrieval (vector + FTS) in one SQL query — no cross-service coordination.
- Qdrant's advantages (billion-scale, high write throughput) don't apply to 50K static chunks.

### Decision 10 — Sparse Retrieval: PostgreSQL Full-Text Search

**Decision:** PostgreSQL `tsvector` + GIN index + `ts_rank` for the sparse component of hybrid retrieval.

**Why:**
- Postgres is already running. tsvector is a native type. One column + one GIN index = done. Zero new services.
- Persistent and indexed — unlike `rank_bm25` (Python library that rebuilds in-memory from scratch at every restart).
- Hybrid retrieval in a single SQL query: `0.6 × dense_score + 0.4 × sparse_score`. α tuned on golden set.
- Elasticsearch would work but adds 2GB + an 11th service — unjustified at our scale.

### Decision 11 — Cross-Encoder Reranker: ms-marco-MiniLM-L-6-v2

**Decision:** `cross-encoder/ms-marco-MiniLM-L-6-v2` reranks top-20 hybrid results → top-5 go to LLM.

**Why:**
- Reranking is the single biggest RAG quality improvement (RAG guide). Bi-encoder retrieval is approximate — cross-encoder scores true relevance by reading query + chunk together.
- MS MARCO training = passage relevance ranking, exactly our task.
- 22M params, ~200ms for top-20 on CPU — fits in modelserver alongside distilbert + spaCy.
- L-12 variant: +1–2% quality but +75% latency (350ms). Not worth it for interactive use.
- Electra-base (900ms) and LLM reranking (1,500ms+) are too slow for chat.

### Decision 12 — Query Transformation: HyDE

**Decision:** HyDE — generate a hypothetical answer to the query, embed it, combine 50/50 with original query vector.

**Why:**
- Maintainer queries ("Has this been seen before?") are question-shaped. The corpus (resolved issues, docs) is answer-shaped. Embedding distance between question and answer is large. HyDE bridges this: the LLM writes a hypothetical answer that looks like the corpus.
- One extra LLM call (~300ms) is within budget.
- Multi-query (3–4 rephrasings + RRF) adds complexity without equivalent gain for a constrained technical corpus.
- Combined 50/50 with original query so a drifted hypothetical doesn't dominate.
- Cross-encoder reranker catches false positives before they reach the LLM.

**Full RAG pipeline:**
```
Query → HyDE hypothetical → embed both → hybrid retrieval (top-20)
  → cross-encoder rerank → top-5 → LLM context
```

### Decision 13 — LLM Provider + Model: GPT-4o-mini

**Decision:** OpenAI `gpt-4o-mini` as the single tool-calling LLM.

**Why:**
- Best tool-calling quality at lowest cost: $0.15/$0.60 per 1M in/out tokens
- A 20-turn triage session costs ~$0.0024 — negligible
- gpt-4o costs 17x more with no meaningful gain for classify/search/summarize tasks
- Same OpenAI API key already in Vault for embeddings (D7) — zero new infra
- 128K context window handles long sessions without truncation
- Temperature 0.1 for structured triage decisions, streaming enabled

### Decision 14 — Long-term Memory: Semantic

**Decision:** Semantic memory — store general facts and repo conventions in pgvector.

**Why semantic over episodic or procedural:**
- Episodic (past events) grows unboundedly and most events are not reusable across sessions
- Procedural (preferences like "use bullet points") is too narrow — basically just settings
- Semantic (facts like "asyncio issues are P0", "questions without repro → close as docs") are few, stable, and apply to every future session — highest reuse value
- Maps cleanly to pgvector: embed each fact, retrieve top-3 relevant facts at turn start, inject into system prompt
- Best cross-conversation recall demo: "Remember asyncio issues are P0" → next session: copilot flags an asyncio bug as P0 automatically

**No auto-writes.** Only saved via explicit `write_memory` tool call by the maintainer.

### Decision 15 — Redis TTL: 24h conversation, 5min cache

**Decision:** Conversation state TTL = 86,400s (24h). API cache TTL = 300s (5min).

**Why 24h for conversation:**
- Covers a full working day including breaks — maintainer returns after lunch with context intact
- Overnight gap (>24h) intentionally clears the session — yesterday's context is stale
- Without TTL, Redis fills indefinitely with abandoned sessions
- TTL is reset on every new message (`redis.expire(key, 86400)`)
- Long-term semantic memory (D14) is unaffected — it lives in Postgres, not Redis

**Why 5min for cache:**
- `GET /me`, `GET /conversations` — rarely changes, 5min staleness acceptable
- Classifier predictions cached by issue_id with no TTL — invalidated on model update

### Decision 16 — Tracing Backend: Langfuse

**Decision:** Langfuse cloud (free tier).

**Why Langfuse over Jaeger or Phoenix:**
- Jaeger is general distributed tracing — not LLM-aware. Every LLM-specific attribute (model, prompt, tokens, cost) must be manually attached as custom span tags. Built for microservice HTTP tracing, not AI pipelines.
- Langfuse is built for LLMs: native fields for generation (LLM call), span (tool call / RAG), trace (conversation), with automatic token count and cost tracking
- Cloud free tier = zero extra container vs Jaeger (needs self-hosted server) or Phoenix (another Python service)
- Best trace UI for Friday demo — shows full conversation tree: user message → LLM → tool → modelserver → result → response, all nested
- OpenTelemetry compatible — vendor-switchable if needed
- Startup check: validate Langfuse credentials at boot. If tracing is misconfigured, app refuses to start.

### Decision 17 — RAG Eval: RAGAS

**Decision:** RAGAS framework for automated RAG evaluation in CI.

**Why RAGAS over custom frozen LLM judge:**
- Purpose-built for RAG: faithfulness, answer relevancy, context precision, context recall — exactly the metrics the assignment requires. No custom scoring prompt to design or debug.
- CI integration is one function call: `ragas.evaluate(golden_set, metrics=[...])` → JSON
- Uses OpenAI API internally (same key, no new infra)
- Pinned RAGAS version + pinned model version in `eval_thresholds.yaml` = reproducible judge across every CI run ("frozen")
- Assignment: hand-label 5 of 25, report agreement with judge. If RAGAS score > threshold matches hand-label for ≥4/5, judge is trusted.

**Thresholds in `eval_thresholds.yaml`:**
- faithfulness ≥ 0.80, answer_relevancy ≥ 0.75, context_precision ≥ 0.70
- hit@5 ≥ 0.80, MRR@10 ≥ 0.65

### Decision 18 — Widget CSS: Tailwind CSS

**Decision:** Tailwind CSS with Vite.

**Why Tailwind over vanilla:**
- Bundle size is not a concern: Vite's PurgeCSS strips all unused Tailwind classes at build time. A widget with 5–6 components uses ~50–80 utility classes → ~3–5KB of CSS after purging. Negligible.
- 3–4x faster to build under a Thursday deadline. The bubble, panel, message list, and input box can be laid out with utility classes in hours vs writing vanilla CSS from scratch.
- Runtime theming: structural/layout classes from Tailwind, dynamic primary color via CSS variable (`--primary: config.theme.primary_color`). Clean separation.
- Standard for 2024 React widget development with Vite.

---

## Submission Requirements

**Tag:** `v0.1.0-week7` on a public GitHub repo. Must come up cleanly with `docker-compose up` after `cp .env.example .env`.

**Required documentation files:**
- `ARCH.md` — architecture overview
- `DECISIONS.md` — every tech choice backed by a number
- `RUNBOOK.md` — how to run, operate, and debug the system
- `EVALS.md` — eval methodology, golden set construction, judge agreement
- `SECURITY.md` — redaction patterns and justification

**Submission block must include:**
- Dataset: chosen repo, N train / N val / N test
- Classification F1: classical / fine-tuned / LLM
- Deployment choice + one-line reason
- Embedding model + one-line reason
- RAG metrics: hit@5, MRR@10, faithfulness, answer relevancy
- Long-term memory type chosen
- Tracing backend + one-line reason
- Widget bundle size (gzipped KB)
- LLM provider + model used

---

## Think About — Design Questions to Answer

These are questions the graders may ask on Friday. They are answered through your architecture decisions, not through improvising on the day.

| Question | Where the answer lives |
|---|---|
| Three models, three numbers — which one ships? Does the answer survive scale/latency/failure cost changes? | DECISIONS.md three-way comparison |
| How do you know your embedding model is right for THIS corpus, not the benchmark it was advertised on? | Retrieval quality number on your golden set |
| The LLM-as-judge disagrees with your hand-labels. Who's right? What do you do with the judge in CI? | EVALS.md — agreement score + policy |
| Redis TTL — what is it, why that number, what happens when it expires mid-conversation? | DECISIONS.md — TTL choice justified |
| Widget is 180KB gzipped. PM says too big. What do you cut? At what size do you push back? | Bundle analysis, DECISIONS.md |
| User pastes a stack trace with their GitHub token. Where does it end up if redaction misses it? | SECURITY.md + redaction test |
| Trace UI shows a span took 4.3s and you don't know why. What's missing? Why is that a design decision? | Tracing spans — what you instrument and why |
| Vault becomes unreachable. App is already running. What happens, what SHOULD happen, where does the policy live? | app/infra/vault adapter + startup checks |

These are not hypothetical — they will be asked. Every answer should already be in the code or a decision doc before Friday.

---

## Week 6 Standards (carried into Week 7)

These are non-negotiable architectural rules established in week 6. Week 7 uses the same standard.

### Layered Architecture
The codebase is strictly layered. No layer touches another layer's responsibility:

| Layer | Owns | Must NOT |
|---|---|---|
| `app/api/` | HTTP routing only | Touch SQLAlchemy, Redis, or external systems |
| `app/services/` | Business logic, transaction boundaries, cache invalidation | - |
| `app/repositories/` | SQL queries only | Raise HTTP errors or invalidate caches |
| `app/domain/` | Pydantic domain models | Be confused with SQLAlchemy ORM models |
| `app/infra/` | Adapters for Vault, MinIO, Redis, LLM providers, tracing, redaction | - |

The boundary is checked on Friday — they will ask you to add a new endpoint live.

### Secrets in Vault
- Every secret (API keys, DB password, JWT signing key, MinIO credentials) resolves from Vault at startup
- `.env` holds **only** the Vault root token and ports — nothing else
- `grep -ri 'sk-' app/` and `grep -ri 'password' app/` return zero matches outside Vault-reading code
- The app **refuses to boot** if Vault is unreachable

### Blob in MinIO
- MinIO is the only place large files live: model artifacts, eval reports, training plots, chunk snapshots
- Nothing is stored on the local filesystem permanently

### Refuse to Boot
The api refuses to start if any of these are true:
- Vault is unreachable
- Classifier weights are missing
- Weights SHA-256 does not match the model card
- Tracing backend is misconfigured
- Any committed eval threshold is set to zero or disabled

### Audit Log
Every significant write action produces an audit log row with: `actor`, `action`, `target`, `timestamp`.

### CI on Every Push
Runs: lint, type-check, build images, both eval suites, redaction test, smoke-test of the full stack.
