#!/bin/sh
# Writes all application secrets into Vault KV v2.
# Runs once after Vault is healthy. Dev values only.
set -e

echo "[vault-init] Enabling KV v2 at secret/..."
vault secrets enable -version=2 -path=secret kv 2>/dev/null || true

echo "[vault-init] Writing secrets..."

vault kv put secret/postgres \
  user="copilot" \
  password="copilot_dev" \
  db="copilot_db" \
  host="db" \
  port="5432"

vault kv put secret/openai \
  api_key="sk-placeholder-replace-before-demo"

vault kv put secret/jwt \
  signing_key="super-secret-jwt-key-replace-before-demo"

vault kv put secret/minio \
  access_key="minioadmin" \
  secret_key="minioadmin_dev" \
  endpoint="http://minio:9000"

vault kv put secret/langfuse \
  public_key="pk-placeholder" \
  secret_key="sk-placeholder" \
  host="https://cloud.langfuse.com"

echo "[vault-init] All secrets written."
