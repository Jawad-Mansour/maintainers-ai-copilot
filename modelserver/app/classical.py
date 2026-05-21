"""Classical classifier — TF-IDF + Logistic Regression loaded from /tmp/weights/."""

from __future__ import annotations

import pickle
from pathlib import Path

WEIGHTS_DIR = Path("/tmp/weights")


class ClassicalClassifier:
    def __init__(self) -> None:
        with open(WEIGHTS_DIR / "tfidf_vectorizer.pkl", "rb") as fh:
            self._vectorizer = pickle.load(fh)  # noqa: S301
        with open(WEIGHTS_DIR / "lr_model.pkl", "rb") as fh:
            self._model = pickle.load(fh)  # noqa: S301

    def predict(self, text: str) -> tuple[str, float]:
        """Return (label, confidence) using TF-IDF + LR."""
        X = self._vectorizer.transform([text])
        proba = self._model.predict_proba(X)[0]
        idx = int(proba.argmax())
        return str(self._model.classes_[idx]), float(proba[idx])
