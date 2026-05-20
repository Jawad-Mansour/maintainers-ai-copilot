"""Model server — stub for Phase 1.

Phase 7 replaces these mock responses with real DistilBERT + NER inference.
"""

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Model Server", version="0.1.0")


class ClassifyRequest(BaseModel):
    text: str


class RerankRequest(BaseModel):
    query: str
    passages: list[str]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mode": "mock"}


@app.post("/classify")
async def classify(req: ClassifyRequest) -> dict[str, object]:
    # Phase 7 replaces with real DistilBERT inference
    return {"label": "bug", "confidence": 0.0, "mode": "mock"}


@app.post("/rerank")
async def rerank(req: RerankRequest) -> dict[str, object]:
    # Phase 3 uses this; Phase 7 replaces with real cross-encoder
    scores = [round(1.0 - i * 0.1, 2) for i in range(len(req.passages))]
    return {"scores": scores, "mode": "mock"}


@app.post("/ner")
async def ner(req: ClassifyRequest) -> dict[str, object]:
    # Phase 4 uses this; Phase 7 replaces with real spaCy NER
    return {"entities": [], "mode": "mock"}
