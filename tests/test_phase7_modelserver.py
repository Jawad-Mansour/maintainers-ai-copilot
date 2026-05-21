"""Phase 7 — Unit tests for real modelserver modules.

These tests mock the heavy ML models so they run without GPU or trained weights.
Integration tests (with real weights) are covered by the docker-compose smoke test.
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── weights.py ────────────────────────────────────────────────────────────────


def test_weights_not_found_on_missing_model_card(tmp_path: Path) -> None:
    from minio.error import S3Error

    from modelserver.app.weights import WeightsNotFound, download_and_verify

    mc = MagicMock()
    mc.fget_object.side_effect = S3Error(
        code="NoSuchKey", message="", resource="", request_id="", host_id="", response=MagicMock()
    )

    with pytest.raises(WeightsNotFound):
        download_and_verify(mc)


def test_weights_sha256_mismatch_raises_runtime_error(tmp_path: Path, monkeypatch) -> None:
    from modelserver.app.weights import download_and_verify

    card = {
        "weights_sha256": "deadbeef" * 8,  # wrong hash — won't match real file
        "tfidf_sha256": "",
        "lr_sha256": "",
    }

    mc = MagicMock()

    def fake_fget(bucket, obj_name, dest):
        if "model_card" in obj_name:
            Path(dest).write_text(json.dumps(card))
        elif "tfidf" in obj_name or "lr_model" in obj_name:
            Path(dest).write_bytes(b"")
        else:
            # distilbert weight files — write real bytes so hash can be computed
            Path(dest).write_bytes(b"fake model data")

    mc.fget_object.side_effect = fake_fget
    mc.list_objects.return_value = [MagicMock(object_name="distilbert/model.safetensors")]

    monkeypatch.setattr("modelserver.app.weights.WEIGHTS_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        download_and_verify(mc)


# ── classifier.py ─────────────────────────────────────────────────────────────


def test_classifier_predict_returns_label_and_confidence() -> None:
    # Mock torch and transformers so this test runs without GPU packages.
    mock_torch = MagicMock()

    # Wire softmax()[0] to return something whose argmax() gives 0 and [0] gives 0.9.
    mock_probs = MagicMock()
    mock_probs.argmax.return_value = 0
    mock_probs.__getitem__ = MagicMock(return_value=0.9)
    mock_softmax_result = MagicMock()
    mock_softmax_result.__getitem__ = MagicMock(return_value=mock_probs)
    mock_torch.softmax.return_value = mock_softmax_result

    mock_card = {"id2label": {"0": "bug", "1": "enhancement", "2": "documentation"}}

    # Clear any cached import so our sys.modules patch takes effect.
    sys.modules.pop("modelserver.app.classifier", None)

    with (
        patch.dict(sys.modules, {"torch": mock_torch, "transformers": MagicMock()}),
        patch("modelserver.app.classifier.DistilBertTokenizerFast.from_pretrained") as mock_tok,
        patch(
            "modelserver.app.classifier.DistilBertForSequenceClassification.from_pretrained"
        ) as mock_model_cls,
    ):
        mock_tok.return_value = MagicMock()
        mock_model_cls.return_value = MagicMock()

        from modelserver.app.classifier import Classifier

        clf = Classifier(mock_card)
        label, confidence = clf.predict("null pointer exception in merge")

    assert label == "bug"
    assert 0.0 < confidence <= 1.0


# ── classical.py ──────────────────────────────────────────────────────────────


def test_classical_classifier_predict(tmp_path: Path, monkeypatch) -> None:
    # Use real (minimal) sklearn objects — MagicMock is not picklable.
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    from modelserver.app.classical import ClassicalClassifier

    texts = [
        "bug crash error null pointer",
        "enhancement feature add support",
        "documentation docs update readme",
    ]
    labels = ["bug", "enhancement", "documentation"]

    vectorizer = TfidfVectorizer()
    X = vectorizer.fit_transform(texts)

    lr = LogisticRegression(max_iter=1000, random_state=42)
    lr.fit(X, labels)

    tfidf_path = tmp_path / "tfidf_vectorizer.pkl"
    lr_path = tmp_path / "lr_model.pkl"

    with open(tfidf_path, "wb") as f:
        pickle.dump(vectorizer, f)
    with open(lr_path, "wb") as f:
        pickle.dump(lr, f)

    monkeypatch.setattr("modelserver.app.classical.WEIGHTS_DIR", tmp_path)

    clf = ClassicalClassifier()
    label, confidence = clf.predict("add support for new dtype enhancement")

    assert label in ["bug", "enhancement", "documentation"]
    assert 0.0 < confidence <= 1.0


# ── reranker.py ───────────────────────────────────────────────────────────────


def test_reranker_returns_score_per_passage() -> None:
    # Mock sentence_transformers so the module imports without the package installed.
    sys.modules.pop("modelserver.app.reranker", None)

    with (
        patch.dict(sys.modules, {"sentence_transformers": MagicMock()}),
        patch("modelserver.app.reranker.CrossEncoder") as mock_ce,
    ):
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.4, 0.7]
        mock_ce.return_value = mock_model

        from modelserver.app.reranker import Reranker

        r = Reranker()
        scores = r.rerank("null pointer bug", ["passage A", "passage B", "passage C"])

    assert len(scores) == 3
    assert scores[0] == pytest.approx(0.9)
    assert scores[2] == pytest.approx(0.7)


def test_reranker_empty_passages() -> None:
    sys.modules.pop("modelserver.app.reranker", None)

    with (
        patch.dict(sys.modules, {"sentence_transformers": MagicMock()}),
        patch("modelserver.app.reranker.CrossEncoder"),
    ):
        from modelserver.app.reranker import Reranker

        r = Reranker()
        assert r.rerank("query", []) == []


# ── ner.py ────────────────────────────────────────────────────────────────────


def test_ner_extracts_known_package() -> None:
    from modelserver.app.ner import NERPipeline

    pipeline = NERPipeline()
    entities = pipeline.extract("pandas raises ValueError when merging on NaN keys")
    labels = {e["label"] for e in entities}
    texts = {e["text"] for e in entities}
    assert "PACKAGE" in labels
    assert "pandas" in texts


def test_ner_extracts_exception() -> None:
    from modelserver.app.ner import NERPipeline

    pipeline = NERPipeline()
    entities = pipeline.extract("This raises a ValueError in the core.")
    texts = {e["text"] for e in entities}
    assert "ValueError" in texts


def test_ner_deduplicates_entities() -> None:
    from modelserver.app.ner import NERPipeline

    pipeline = NERPipeline()
    entities = pipeline.extract("pandas pandas pandas")
    pandas_hits = [e for e in entities if e["text"] == "pandas"]
    assert len(pandas_hits) == 1


# ── summarizer.py ─────────────────────────────────────────────────────────────


def test_summarizer_returns_string() -> None:
    from modelserver.app.summarizer import Summarizer

    with patch("modelserver.app.summarizer.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "This is a summary."
        mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])
        mock_openai.return_value = mock_client

        s = Summarizer("sk-test")
        result = s.summarize("Issue: crash on merge. Comments: fixed in PR #1234.")

    assert result == "This is a summary."


# ── main.py endpoints (mock mode) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_returns_mock_mode_when_no_weights() -> None:
    from httpx import ASGITransport, AsyncClient

    import modelserver.main as ms_main
    from modelserver.main import app

    # ASGITransport does not trigger lifespan — set state directly
    ms_main._mode = "mock"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health")

    assert resp.status_code == 200
    assert resp.json()["mode"] == "mock"


# ── Phase 7-C: API boot check ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_boots_when_modelserver_in_mock_mode_and_flag_off(
    tmp_path: Path, monkeypatch
) -> None:
    """REQUIRE_REAL_MODELSERVER=false (default) — API boots even in mock mode."""
    # chdir to a temp directory so Settings does not load the project .env
    # (which contains extra fields like POSTGRES_PORT that extra="forbid" rejects).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_TOKEN", "root")

    import api.config as cfg_mod

    cfg_mod._settings = None
    s = cfg_mod.Settings()
    assert s.require_real_modelserver is False
    cfg_mod._settings = None


def test_api_setting_can_be_enabled(tmp_path: Path, monkeypatch) -> None:
    """When env var is set, require_real_modelserver becomes True."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_TOKEN", "root")
    monkeypatch.setenv("REQUIRE_REAL_MODELSERVER", "true")

    from api import config

    config._settings = None
    s = config.get_settings()
    assert s.require_real_modelserver is True
    config._settings = None
