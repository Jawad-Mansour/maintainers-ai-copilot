"""Alembic env.py — reads DB URL from Vault at migration time."""

from __future__ import annotations

import os
from logging.config import fileConfig

import hvac
from alembic import context
from sqlalchemy import engine_from_config, pool

import app.infra.db.models  # noqa: F401 — ensures models are registered on Base.metadata
from app.infra.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_db_url() -> str:
    vault_addr = os.environ["VAULT_ADDR"]
    vault_token = os.environ["VAULT_TOKEN"]
    db_host = os.environ.get("DB_HOST", "db")

    client = hvac.Client(url=vault_addr, token=vault_token)
    if not client.is_authenticated():
        raise RuntimeError("Vault authentication failed during migration.")

    data = client.secrets.kv.v2.read_secret_version(path="postgres", mount_point="secret")["data"][
        "data"
    ]

    return (
        f"postgresql://{data['user']}:{data['password']}"
        f"@{db_host}:{data.get('port', '5432')}/{data['db']}"
    )


def run_migrations_offline() -> None:
    url = _get_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_db_url()

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
