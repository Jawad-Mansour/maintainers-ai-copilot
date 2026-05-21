"""DistilBERT sequence classifier — loads fine-tuned weights from /tmp/weights/."""

from __future__ import annotations

from pathlib import Path

import torch
from transformers import DistilBertForSequenceClassification, DistilBertTokenizerFast

WEIGHTS_DIR = Path("/tmp/weights/distilbert_weights")


class Classifier:
    def __init__(self, model_card: dict) -> None:
        self._tokenizer = DistilBertTokenizerFast.from_pretrained(str(WEIGHTS_DIR))
        self._model = DistilBertForSequenceClassification.from_pretrained(str(WEIGHTS_DIR))
        self._model.eval()
        # id2label from model card — keys stored as strings in JSON
        self._id2label: dict[int, str] = {
            int(k): v for k, v in model_card.get("id2label", {}).items()
        }
        if not self._id2label:
            # Fall back to model config if card didn't include it
            self._id2label = {i: v for i, v in enumerate(self._model.config.id2label.values())}

    def predict(self, text: str) -> tuple[str, float]:
        """Return (label, confidence) for the given text."""
        enc = self._tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True,
        )
        with torch.no_grad():
            logits = self._model(**enc).logits
        probs = torch.softmax(logits, dim=-1)[0]
        idx = int(probs.argmax())
        return self._id2label.get(idx, "unknown"), float(probs[idx])
