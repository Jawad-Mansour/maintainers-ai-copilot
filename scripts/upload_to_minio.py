"""
Upload training artifacts from a local directory to MinIO.

Run this locally after downloading the Colab artifact folder to your machine:

    python scripts/upload_to_minio.py --artifacts-dir ~/Downloads/maintainers-copilot-artifacts/data

Reads MinIO credentials from the .env file (or environment variables).
"""

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload training artifacts to MinIO")
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        required=True,
        help="Local directory containing training artifacts (data/ from Colab)",
    )
    parser.add_argument("--bucket", default="models", help="MinIO bucket name")
    parser.add_argument("--endpoint", default=None, help="MinIO endpoint (overrides .env)")
    args = parser.parse_args()

    artifacts_dir = args.artifacts_dir.expanduser().resolve()
    if not artifacts_dir.exists():
        print(f"ERROR: artifacts dir not found: {artifacts_dir}")
        sys.exit(1)

    # Load credentials from environment or .env
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    endpoint = args.endpoint or os.environ.get("MINIO_ENDPOINT", "localhost:9000")
    access = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
    secret = os.environ.get("MINIO_SECRET_KEY", "minioadmin_dev")

    try:
        from minio import Minio
    except ImportError:
        print("ERROR: minio not installed. Run: pip install minio")
        sys.exit(1)

    mc = Minio(endpoint, access_key=access, secret_key=secret, secure=False)
    if not mc.bucket_exists(args.bucket):
        mc.make_bucket(args.bucket)
        print(f"Created bucket: {args.bucket}")

    # Define what to upload and where
    uploads: list[tuple[Path, str]] = []

    model_card = artifacts_dir / "model_card.json"
    if model_card.exists():
        uploads.append((model_card, "distilbert/model_card.json"))

    tfidf = artifacts_dir / "tfidf_vectorizer.pkl"
    if tfidf.exists():
        uploads.append((tfidf, "classical/tfidf_vectorizer.pkl"))

    lr_model = artifacts_dir / "lr_model.pkl"
    if lr_model.exists():
        uploads.append((lr_model, "classical/lr_model.pkl"))

    for plot in (artifacts_dir).glob("*.png"):
        uploads.append((plot, f"plots/{plot.name}"))

    weights_dir = artifacts_dir / "distilbert_weights"
    if weights_dir.exists():
        for wf in weights_dir.iterdir():
            uploads.append((wf, f"distilbert/{wf.name}"))

    if not uploads:
        print("WARNING: No artifacts found to upload. Check --artifacts-dir path.")
        sys.exit(1)

    print(f"Uploading {len(uploads)} files to {endpoint}/{args.bucket} ...")
    for local_path, object_name in uploads:
        mc.fput_object(args.bucket, object_name, str(local_path))
        print(f"  OK  {object_name}")

    print(f"\nAll {len(uploads)} artifacts uploaded to MinIO.")
    print("The API will now be able to boot with real weights.")


if __name__ == "__main__":
    main()
