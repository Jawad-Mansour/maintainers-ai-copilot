"""Model server — Phase 7-B: real inference with graceful mock fallback.

Boot policy:
  - Vault unreachable           → sys.exit(1)  (hard fail — always)
  - Weights not in MinIO yet    → mock mode     (training not run yet)
  - SHA-256 mismatch            → sys.exit(1)  (hard fail — data integrity)
  - All OK                      → real mode

/health returns {"mode": "real"} or {"mode": "mock"} so the API's
Phase 7-C refuse-to-boot check knows which state we're in.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from minio import Minio
from pydantic import BaseModel

# ─── State ────────────────────────────────────────────────────────────────────

_mode: str = "mock"
_classifier: Any = None
_classical: Any = None
_reranker: Any = None
_ner: Any = None
_summarizer: Any = None


# ─── Startup / shutdown ───────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    global _mode, _classifier, _classical, _reranker, _ner, _summarizer

    vault_addr = os.environ.get("VAULT_ADDR", "http://vault:8200")
    vault_token = os.environ.get("VAULT_TOKEN", "root")

    # 1. Vault — always required
    try:
        from app.vault import fetch_secrets

        secrets = fetch_secrets(vault_addr, vault_token)
    except Exception as exc:
        print(f"[modelserver] FATAL: Vault unavailable — {exc}", file=sys.stderr)
        sys.exit(1)

    # 2. MinIO — download and verify weights
    mc = Minio(
        secrets.minio_endpoint,
        access_key=secrets.minio_access_key,
        secret_key=secrets.minio_secret_key,
        secure=secrets.minio_secure,
    )

    try:
        from app.weights import WeightsNotFound, download_and_verify

        card = download_and_verify(mc)
    except WeightsNotFound as exc:
        print(f"[modelserver] INFO: weights not found — starting in mock mode. ({exc})")
        yield  # serve mock responses
        return
    except RuntimeError as exc:
        # SHA-256 mismatch or other integrity failure — hard stop
        print(f"[modelserver] FATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    # 3. Load real models
    print("[modelserver] Loading DistilBERT classifier …")
    from app.classifier import Classifier

    _classifier = Classifier(card)

    print("[modelserver] Loading classical classifier (TF-IDF + LR) …")
    from app.classical import ClassicalClassifier

    _classical = ClassicalClassifier()

    print("[modelserver] Loading cross-encoder reranker …")
    from app.reranker import Reranker

    _reranker = Reranker()

    print("[modelserver] Loading spaCy NER pipeline …")
    from app.ner import NERPipeline

    _ner = NERPipeline()

    print("[modelserver] Loading summarizer …")
    from app.summarizer import Summarizer

    _summarizer = Summarizer(secrets.openai_api_key)

    _mode = "real"
    print("[modelserver] All models loaded — serving in real mode.")

    yield  # application runs here

    # Shutdown: nothing to clean up for CPU inference


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Model Server", version="0.2.0", lifespan=lifespan)


# ─── Request / response models ────────────────────────────────────────────────


class ClassifyBatchRequest(BaseModel):
    texts: list[str]


class ClassifyBatchResponse(BaseModel):
    labels: list[str]
    confidences: list[float]
    mode: str


class ClassifySingleRequest(BaseModel):
    text: str


class ClassifySingleResponse(BaseModel):
    label: str
    confidence: float
    mode: str


class RerankRequest(BaseModel):
    query: str
    passages: list[str]


class RerankResponse(BaseModel):
    scores: list[float]
    mode: str


class NERRequest(BaseModel):
    text: str


class NERResponse(BaseModel):
    entities: list[dict[str, str]]
    mode: str


class SummarizeRequest(BaseModel):
    thread: str


class SummarizeResponse(BaseModel):
    summary: str
    mode: str


# ─── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mode": _mode}


@app.post("/classify", response_model=ClassifyBatchResponse)
async def classify(req: ClassifyBatchRequest) -> ClassifyBatchResponse:
    """Batch classify — accepts list[str], returns parallel list[str] labels."""
    if _mode == "real" and _classifier is not None:
        labels, confidences = [], []
        for text in req.texts:
            label, confidence = _classifier.predict(text)
            labels.append(label)
            confidences.append(confidence)
        return ClassifyBatchResponse(labels=labels, confidences=confidences, mode="real")
    # Mock fallback
    labels = ["bug"] * len(req.texts)
    confidences = [0.0] * len(req.texts)
    return ClassifyBatchResponse(labels=labels, confidences=confidences, mode="mock")


@app.post("/classify/classical", response_model=ClassifySingleResponse)
async def classify_classical(req: ClassifySingleRequest) -> ClassifySingleResponse:
    """TF-IDF + LR endpoint — for the three-way comparison in evals."""
    if _mode == "real" and _classical is not None:
        label, confidence = _classical.predict(req.text)
        return ClassifySingleResponse(label=label, confidence=confidence, mode="real")
    return ClassifySingleResponse(label="bug", confidence=0.0, mode="mock")


@app.post("/rerank", response_model=RerankResponse)
async def rerank(req: RerankRequest) -> RerankResponse:
    if _mode == "real" and _reranker is not None:
        scores = _reranker.rerank(req.query, req.passages)
        return RerankResponse(scores=scores, mode="real")
    # Mock: decreasing scores so callers can still sort
    scores = [round(1.0 - i * 0.1, 2) for i in range(len(req.passages))]
    return RerankResponse(scores=scores, mode="mock")


@app.post("/ner", response_model=NERResponse)
async def ner(req: NERRequest) -> NERResponse:
    if _mode == "real" and _ner is not None:
        entities = _ner.extract(req.text)
        return NERResponse(entities=entities, mode="real")
    return NERResponse(entities=[], mode="mock")


@app.post("/summarize", response_model=SummarizeResponse)
async def summarize(req: SummarizeRequest) -> SummarizeResponse:
    if _mode == "real" and _summarizer is not None:
        try:
            summary = _summarizer.summarize(req.thread)
            return SummarizeResponse(summary=summary, mode="real")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Summarizer error: {exc}") from exc
    return SummarizeResponse(summary="(summary unavailable — mock mode)", mode="mock")
