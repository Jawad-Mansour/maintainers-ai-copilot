from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Bootstrap settings loaded from environment / .env.
    Only Vault coordinates and service ports live here.
    All real secrets are fetched from Vault at startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    vault_addr: str
    vault_token: str

    # Service hostnames (used inside docker-compose network)
    db_host: str = "db"
    redis_host: str = "redis"
    minio_host: str = "minio"
    modelserver_host: str = "modelserver"

    # Phase 7-C: set to True after uploading trained weights to MinIO
    require_real_modelserver: bool = False


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
