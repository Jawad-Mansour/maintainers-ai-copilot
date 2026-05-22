#!/bin/sh
set -e
echo "[langfuse-db-init] Checking for langfuse_db..."
EXISTS=$(PGPASSWORD=copilot_dev psql -h db -U copilot -d copilot_db -tAc "SELECT 1 FROM pg_database WHERE datname='langfuse_db'" 2>/dev/null || echo "")
if [ "$EXISTS" = "1" ]; then
  echo "[langfuse-db-init] langfuse_db already exists, skipping."
else
  echo "[langfuse-db-init] Creating langfuse_db..."
  PGPASSWORD=copilot_dev createdb -h db -U copilot langfuse_db
  echo "[langfuse-db-init] Done."
fi
