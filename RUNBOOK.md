# RUNBOOK.md — Operational Guide

## Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- `.env` file in project root (copy from `.env.example`, fill real secrets)

```
VAULT_ADDR=http://localhost:8200
VAULT_TOKEN=root
OPENAI_API_KEY=sk-...          # real key
LANGFUSE_PUBLIC_KEY=pk-lf-...  # from Langfuse dashboard
LANGFUSE_SECRET_KEY=sk-lf-...  # from Langfuse dashboard
LANGFUSE_HOST=http://langfuse:3000
```

Never commit `.env`. The `.gitignore` excludes it.

---

## First Boot

```bash
# 1. Build all images
docker compose build

# 2. Start all services
docker compose up -d

# 3. Wait ~3 minutes for Langfuse to initialise (heaviest service)
docker compose ps        # all services should show "healthy" or "exited 0"
```

Startup order (enforced by healthcheck dependencies):
1. `db`, `redis`, `minio`, `vault` — data stores
2. `vault-init`, `langfuse-db-init`, `minio-init` — one-shot initialisers
3. `langfuse`, `modelserver` — application services (slow start)
4. `migrate` — Alembic `upgrade head`
5. `api` — FastAPI (waits for all above)
6. `chatbot`, `widget`, `host` — frontends

---

## Service URLs

| Service | URL | Credentials |
|---|---|---|
| API (FastAPI docs) | http://localhost:8000/docs | — |
| Streamlit chatbot | http://localhost:8501 | register via UI |
| Widget (iframe) | http://localhost:5173 | — |
| Demo host page | http://localhost:3001 | — |
| Langfuse tracing | http://localhost:3000 | admin@local.dev / admin123 |
| MinIO console | http://localhost:9001 | minioadmin / minioadmin_dev |
| Vault UI | http://localhost:8200 | token: root |

---

## Creating the First Admin User

```bash
# Register via API
curl -s -X POST http://localhost:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"changeme","is_superuser":true}' | jq .

# Or use the Streamlit chatbot UI → Register tab → sign in → admin panel
```

---

## Ingest the RAG Corpus

```bash
# Run the bulk ingest script (needs OPENAI_API_KEY in env)
PYTHONIOENCODING=utf-8 python scripts/bulk_ingest_corpus.py \
  --corpus data/rag_corpus.jsonl \
  --api http://localhost:8000 \
  --email admin@example.com \
  --password changeme
```

This embeds all documents and stores parent/child chunks in pgvector.

---

## Creating a Widget

1. Sign in to the chatbot (http://localhost:8501) as admin
2. Navigate to **Widget Config** in the sidebar
3. Click **Create Widget** — copy the UUID
4. Replace `WIDGET_ID_PLACEHOLDER` in `host/index.html` with the UUID
5. Rebuild the host container: `docker compose build host && docker compose up -d host`

Or via API:
```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"changeme"}' | jq -r .access_token)

curl -s -X POST http://localhost:8000/admin/widgets \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"pandas demo","allowed_origins":["http://localhost:3001"]}' | jq .
```

---

## Running Evaluations

### Classification eval

```bash
PYTHONIOENCODING=utf-8 python evals/run_classification_eval.py \
  --api http://localhost:8000 \
  --golden evals/golden_classification.json \
  --report eval_classification_report.json
```

### RAG eval (retrieval + RAGAS)

```bash
PYTHONIOENCODING=utf-8 python evals/run_rag_eval.py \
  --api http://localhost:8000 \
  --golden evals/golden_rag.json \
  --report eval_rag_report.json
```

Both scripts compare results against thresholds in `eval_thresholds.yaml` and exit non-zero if any metric falls below threshold.

---

## Common Operations

### Restart a single service

```bash
docker compose restart api
docker compose restart modelserver
```

### View logs

```bash
docker compose logs -f api           # FastAPI structured JSON logs
docker compose logs -f modelserver   # ML inference logs
docker compose logs -f langfuse      # Langfuse startup (slow)
```

### Check health endpoints

```bash
curl http://localhost:8000/health    # {"status":"ok"}
curl http://localhost:8001/health    # {"status":"ok","models":[...]}
```

### Read a secret from Vault

```bash
docker exec -e VAULT_TOKEN=root maintainers-ai-copilot-vault-1 \
  vault kv get -field=openai_api_key secret/copilot
```

### Run Alembic migrations manually

```bash
docker compose run --rm migrate alembic upgrade head
```

### Connect to PostgreSQL directly

```bash
docker exec -it maintainers-ai-copilot-db-1 \
  psql -U copilot -d copilot_db
```

---

## Stopping and Cleanup

```bash
# Stop all services (preserves volumes)
docker compose down

# Stop and delete all data volumes (full reset)
docker compose down -v
```

---

## Debugging Common Issues

### Langfuse not healthy after 5 minutes

Langfuse runs Next.js and can take 4–7 minutes on first boot while it runs DB migrations.

```bash
docker compose logs langfuse | tail -40
# Wait until you see: "✓ Ready"
```

### modelserver not healthy

The modelserver downloads ML models on first boot (~500MB). Check:

```bash
docker compose logs modelserver | tail -20
# Should show: "DistilBERT loaded", "spaCy loaded", "cross-encoder loaded"
```

### API exits on boot (Vault error)

The API refuses to start if secrets are missing from Vault. Check `vault-init` ran:

```bash
docker compose logs vault-init
# Should show: "Success! Data written to: secret/data/copilot"
```

If not, re-run: `docker compose up vault-init`

### RAG returns empty results

1. Check corpus was ingested: `curl http://localhost:8000/health | jq .chunk_count`
2. If 0 — run the ingest script (see above)
3. Check embedding endpoint: `curl http://localhost:8000/health | jq .openai`

### Widget not appearing on host page

1. Open http://localhost:3001 in browser DevTools → Console
2. Check for `/widget.js` 404 → API must be running on :8000
3. Check `data-widget-id` attribute is a valid UUID (not `WIDGET_ID_PLACEHOLDER`)

---

## Friday Demo Checklist

- [ ] `docker compose ps` — all services healthy
- [ ] Corpus ingested (chunk_count > 0 in `/health`)
- [ ] Admin user created and can log in at http://localhost:8501
- [ ] Widget UUID created and set in `host/index.html`
- [ ] http://localhost:3001 shows widget bubble in bottom-right
- [ ] Langfuse at http://localhost:3000 shows traces
- [ ] Paste a sample issue into the widget — verify classify + RAG response
- [ ] Save a memory in session 1, open new session, verify recall
- [ ] Run both eval scripts — all metrics above thresholds
