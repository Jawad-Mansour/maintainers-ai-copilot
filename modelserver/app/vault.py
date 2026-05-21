"""Vault client for the modelserver — fetches MinIO and OpenAI secrets."""

from __future__ import annotations

import hvac


class ModelServerSecrets:
    def __init__(self, minio: dict[str, str], openai: dict[str, str]) -> None:
        endpoint = minio.get("endpoint", "minio:9000")
        # Strip http:// prefix — Minio client takes host:port
        self.minio_endpoint: str = endpoint.replace("http://", "").replace("https://", "")
        self.minio_secure: bool = endpoint.startswith("https://")
        self.minio_access_key: str = minio["access_key"]
        self.minio_secret_key: str = minio["secret_key"]
        self.openai_api_key: str = openai["api_key"]


def fetch_secrets(vault_addr: str, vault_token: str) -> ModelServerSecrets:
    client = hvac.Client(url=vault_addr, token=vault_token)
    if not client.is_authenticated():
        raise RuntimeError(
            f"Vault authentication failed. Check VAULT_ADDR={vault_addr} and VAULT_TOKEN."
        )

    def _read(path: str) -> dict[str, str]:
        try:
            resp = client.secrets.kv.v2.read_secret_version(path=path, mount_point="secret")
            return resp["data"]["data"]
        except Exception as exc:
            raise RuntimeError(f"Failed to read Vault secret '{path}': {exc}") from exc

    return ModelServerSecrets(minio=_read("minio"), openai=_read("openai"))
