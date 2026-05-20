"""Phase 1 — Unit tests for the refuse-to-boot guard.

These tests run without Docker. They mock hvac to simulate Vault failures
and assert the lifespan raises RuntimeError, which causes a non-zero exit.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# ── Helper: build a minimal vault-like mock ────────────────────────────────


def _make_vault_client(authenticated: bool = True) -> MagicMock:
    client = MagicMock()
    client.is_authenticated.return_value = authenticated

    secret_data = {
        "postgres": {"user": "u", "password": "p", "db": "d", "host": "h", "port": "5432"},
        "openai": {"api_key": "sk-test"},
        "jwt": {"signing_key": "secret"},
        "minio": {"access_key": "a", "secret_key": "s", "endpoint": "http://minio:9000"},
        "langfuse": {"public_key": "pk", "secret_key": "sk", "host": "https://x.com"},
    }

    def _read(path: str, mount_point: str) -> dict:  # type: ignore[return]
        return {"data": {"data": secret_data[path]}}

    client.secrets.kv.v2.read_secret_version.side_effect = _read
    return client


# ── Tests ──────────────────────────────────────────────────────────────────


def test_vault_fetch_succeeds_when_authenticated() -> None:
    """fetch_vault_secrets returns VaultSecrets when Vault is healthy."""
    from api.app.infra.vault import fetch_vault_secrets

    with patch("api.app.infra.vault.hvac.Client", return_value=_make_vault_client()):
        secrets = fetch_vault_secrets("http://vault:8200", "root")

    assert secrets.db_user == "u"
    assert secrets.openai_api_key == "sk-test"
    assert "asyncpg" in secrets.db_url


def test_vault_fetch_raises_when_unauthenticated() -> None:
    """fetch_vault_secrets raises RuntimeError when token is invalid."""
    from api.app.infra.vault import fetch_vault_secrets

    with (
        patch("api.app.infra.vault.hvac.Client", return_value=_make_vault_client(False)),
        pytest.raises(RuntimeError, match="Vault authentication failed"),
    ):
        fetch_vault_secrets("http://vault:8200", "bad-token")


def test_eval_thresholds_passes_when_valid(tmp_path: Path) -> None:
    """_check_eval_thresholds passes when all values > 0."""
    thresholds = tmp_path / "eval_thresholds.yaml"
    thresholds.write_text(yaml.dump({"ragas": {"faithfulness": 0.7, "answer_relevancy": 0.7}}))

    import api.main as main_mod

    original = main_mod.THRESHOLDS_FILE
    main_mod.THRESHOLDS_FILE = thresholds
    try:
        main_mod._check_eval_thresholds()  # must not raise
    finally:
        main_mod.THRESHOLDS_FILE = original


def test_eval_thresholds_raises_when_zero(tmp_path: Path) -> None:
    """_check_eval_thresholds raises RuntimeError when any value is 0."""
    thresholds = tmp_path / "eval_thresholds.yaml"
    thresholds.write_text(yaml.dump({"ragas": {"faithfulness": 0.0}}))

    import api.main as main_mod

    original = main_mod.THRESHOLDS_FILE
    main_mod.THRESHOLDS_FILE = thresholds
    try:
        with pytest.raises(RuntimeError, match="zero or disabled"):
            main_mod._check_eval_thresholds()
    finally:
        main_mod.THRESHOLDS_FILE = original


def test_eval_thresholds_raises_when_file_missing(tmp_path: Path) -> None:
    """_check_eval_thresholds raises RuntimeError when file does not exist."""
    import api.main as main_mod

    original = main_mod.THRESHOLDS_FILE
    main_mod.THRESHOLDS_FILE = tmp_path / "nonexistent.yaml"
    try:
        with pytest.raises(RuntimeError, match="not found"):
            main_mod._check_eval_thresholds()
    finally:
        main_mod.THRESHOLDS_FILE = original
