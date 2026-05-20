"""Vault client — fetches all application secrets at startup.

All secrets live in Vault KV v2 at secret/<path>.
The app never reads secrets from environment variables directly.
"""

from __future__ import annotations

import hvac


class VaultSecrets:
    """Holds all secrets fetched from Vault at startup."""

    def __init__(self, data: dict[str, dict[str, str]]) -> None:
        pg = data["postgres"]
        self.db_user: str = pg["user"]
        self.db_password: str = pg["password"]
        self.db_name: str = pg["db"]
        self.db_host: str = pg.get("host", "db")
        self.db_port: int = int(pg.get("port", "5432"))

        self.openai_api_key: str = data["openai"]["api_key"]
        self.jwt_signing_key: str = data["jwt"]["signing_key"]

        minio = data["minio"]
        self.minio_access_key: str = minio["access_key"]
        self.minio_secret_key: str = minio["secret_key"]
        self.minio_endpoint: str = minio.get("endpoint", "http://minio:9000")

        lf = data["langfuse"]
        self.langfuse_public_key: str = lf["public_key"]
        self.langfuse_secret_key: str = lf["secret_key"]
        self.langfuse_host: str = lf.get("host", "https://cloud.langfuse.com")

    @property
    def db_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def db_url_sync(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


def fetch_vault_secrets(vault_addr: str, vault_token: str) -> VaultSecrets:
    """Connect to Vault and fetch all secrets.

    Raises RuntimeError if Vault is unreachable or token is invalid.
    This is intentional: the app must refuse to boot on Vault failure.
    """
    client = hvac.Client(url=vault_addr, token=vault_token)

    if not client.is_authenticated():
        raise RuntimeError(
            f"Vault authentication failed. Check VAULT_ADDR={vault_addr} and VAULT_TOKEN."
        )

    paths = ["postgres", "openai", "jwt", "minio", "langfuse"]
    secrets: dict[str, dict[str, str]] = {}

    for path in paths:
        try:
            resp = client.secrets.kv.v2.read_secret_version(path=path, mount_point="secret")
            secrets[path] = resp["data"]["data"]
        except Exception as exc:
            raise RuntimeError(f"Failed to read Vault secret '{path}': {exc}") from exc

    return VaultSecrets(secrets)
