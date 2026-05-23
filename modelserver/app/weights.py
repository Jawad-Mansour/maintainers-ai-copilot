"""Download model artifacts from MinIO and verify SHA-256 checksums.

Boot policy:
- Weights not present in MinIO → WeightsNotFound (caller starts in mock mode)
- Weights present but SHA-256 mismatch → RuntimeError (refuse to boot — data integrity failure)
- Weights present and valid → returns parsed model card dict
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from minio import Minio
from minio.error import S3Error

WEIGHTS_DIR = Path("/tmp/weights")
BUCKET = "models"

_DISTILBERT_PREFIX = "distilbert/"
_CLASSICAL_PREFIX = "classical/"


class WeightsNotFound(Exception):
    """Raised when model artifacts do not exist in MinIO yet (training not run)."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _download_object(mc: Minio, object_name: str, dest: Path) -> None:
    mc.fget_object(BUCKET, object_name, str(dest))


def download_and_verify(mc: Minio) -> dict:
    """Download all artifacts from MinIO and verify SHA-256 checksums.

    Returns the model card dict on success.
    Raises WeightsNotFound if the model card doesn't exist yet.
    Raises RuntimeError on checksum mismatch (hard boot failure).
    """
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    distilbert_dir = WEIGHTS_DIR / "distilbert_weights"
    distilbert_dir.mkdir(exist_ok=True)

    # ── 1. Model card (existence check) ──────────────────────────────────────
    card_path = WEIGHTS_DIR / "model_card.json"
    try:
        _download_object(mc, "distilbert/model_card.json", card_path)
    except S3Error as exc:
        if exc.code in ("NoSuchKey", "NoSuchBucket"):
            raise WeightsNotFound(
                "model_card.json not found in MinIO — run training first"
            ) from exc
        raise

    with open(card_path) as fh:
        card: dict = json.load(fh)

    # ── 2. DistilBERT weight files ────────────────────────────────────────────
    try:
        objects = list(mc.list_objects(BUCKET, prefix=_DISTILBERT_PREFIX, recursive=True))
    except S3Error as exc:
        raise WeightsNotFound(f"Cannot list distilbert/ in MinIO: {exc}") from exc

    for obj in objects:
        name = obj.object_name
        if name.endswith("model_card.json"):
            continue
        filename = Path(name).name
        _download_object(mc, name, distilbert_dir / filename)

    # Verify main weights file
    weights_sha = card.get("weights_sha256", "")
    if not weights_sha:
        print(
            "[WARNING] model_card.json missing 'weights_sha256' "
            "— skipping DistilBERT integrity check",
            flush=True,
        )
    if weights_sha:
        model_file = distilbert_dir / "model.safetensors"
        if not model_file.exists():
            model_file = distilbert_dir / "pytorch_model.bin"
        if model_file.exists():
            actual = _sha256_file(model_file)
            if actual != weights_sha:
                raise RuntimeError(
                    f"DistilBERT weights SHA-256 mismatch.\n"
                    f"  expected: {weights_sha}\n"
                    f"  actual:   {actual}\n"
                    "Re-run training or re-upload the correct artifact."
                )

    # ── 3. TF-IDF vectorizer ──────────────────────────────────────────────────
    tfidf_path = WEIGHTS_DIR / "tfidf_vectorizer.pkl"
    try:
        _download_object(mc, "classical/tfidf_vectorizer.pkl", tfidf_path)
    except S3Error as exc:
        raise WeightsNotFound(f"tfidf_vectorizer.pkl not found: {exc}") from exc

    tfidf_sha = card.get("tfidf_sha256", "")
    if not tfidf_sha:
        print(
            "[WARNING] model_card.json missing 'tfidf_sha256' — skipping TF-IDF integrity check",
            flush=True,
        )
    if tfidf_sha:
        actual = _sha256_file(tfidf_path)
        if actual != tfidf_sha:
            raise RuntimeError(
                f"TF-IDF vectorizer SHA-256 mismatch: expected {tfidf_sha}, got {actual}"
            )

    # ── 4. LR model ───────────────────────────────────────────────────────────
    lr_path = WEIGHTS_DIR / "lr_model.pkl"
    try:
        _download_object(mc, "classical/lr_model.pkl", lr_path)
    except S3Error as exc:
        raise WeightsNotFound(f"lr_model.pkl not found: {exc}") from exc

    lr_sha = card.get("lr_sha256", "")
    if not lr_sha:
        print(
            "[WARNING] model_card.json missing 'lr_sha256' — skipping LR model integrity check",
            flush=True,
        )
    if lr_sha:
        actual = _sha256_file(lr_path)
        if actual != lr_sha:
            raise RuntimeError(f"LR model SHA-256 mismatch: expected {lr_sha}, got {actual}")

    return card
