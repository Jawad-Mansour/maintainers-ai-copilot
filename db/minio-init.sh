#!/bin/sh
# Creates required MinIO buckets after MinIO is healthy.
set -e

echo "[minio-init] Configuring MinIO client..."
mc alias set local http://minio:9000 minioadmin minioadmin_dev

echo "[minio-init] Creating buckets..."
mc mb local/models --ignore-existing
mc mb local/evals --ignore-existing
mc mb local/chunk-snapshots --ignore-existing

echo "[minio-init] Buckets ready: models, evals, chunk-snapshots"
