"""MinIO client — stores chunk snapshots per conversation."""

from __future__ import annotations

import asyncio
import io
import json
import uuid as uuid_lib
from datetime import UTC, datetime

from minio import Minio
from minio.error import S3Error

from app.infra.observability import get_logger

logger = get_logger(__name__)

CHUNK_BUCKET = "chunk-snapshots"


def build_minio(endpoint: str, access_key: str, secret_key: str) -> Minio:
    host = endpoint.replace("http://", "").replace("https://", "")
    secure = endpoint.startswith("https://")
    return Minio(host, access_key=access_key, secret_key=secret_key, secure=secure)


def _ensure_bucket(client: Minio) -> None:
    try:
        if not client.bucket_exists(CHUNK_BUCKET):
            client.make_bucket(CHUNK_BUCKET)
    except S3Error as exc:
        logger.warning("minio_bucket_check_failed", error=str(exc))


async def save_chunk_snapshot(
    client: Minio,
    conversation_id: str,
    chunks: list[dict],
) -> str:
    """Persist retrieved chunks as JSON. Returns the object key."""
    snapshot_id = str(uuid_lib.uuid4())
    key = f"{conversation_id}/{snapshot_id}.json"
    payload = json.dumps(
        {
            "conversation_id": conversation_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "chunks": chunks,
        },
        default=str,
    ).encode()

    def _upload() -> None:
        _ensure_bucket(client)
        client.put_object(
            CHUNK_BUCKET,
            key,
            io.BytesIO(payload),
            length=len(payload),
            content_type="application/json",
        )

    await asyncio.to_thread(_upload)
    logger.info("chunk_snapshot_saved", key=key, n=len(chunks))
    return key
