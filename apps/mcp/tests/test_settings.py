"""Tests for :mod:`app.settings`."""

from __future__ import annotations

import pytest

from app.settings import Settings, get_settings


def test_defaults_match_env_example() -> None:
    """Without any env override the settings match ``.env.example``."""
    s = get_settings()
    assert s.path == "/mcp/"
    assert s.stateless is True
    assert s.log_level == "INFO"
    # SQL fields are optional at the Settings layer (validated by
    # ``SqlDatabaseClient.__init__`` when it actually needs them).
    assert s.azure_sql_server is None
    assert s.azure_sql_database is None
    assert s.azure_sql_managed_identity_client_id == ""
    assert s.environment == "local"


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each ``MCP_*`` env var maps onto the matching field."""
    monkeypatch.setenv("MCP_PATH", "/custom/")
    monkeypatch.setenv("MCP_STATELESS", "false")
    monkeypatch.setenv("MCP_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("MCP_AZURE_SQL_SERVER", "foo.database.windows.net")
    monkeypatch.setenv("MCP_AZURE_SQL_DATABASE", "wfm")
    monkeypatch.setenv(
        "MCP_AZURE_SQL_MANAGED_IDENTITY_CLIENT_ID",
        "11111111-2222-3333-4444-555555555555",
    )
    monkeypatch.setenv("MCP_ENVIRONMENT", "azure")

    s = Settings()  # bypass get_settings to make the override path explicit
    assert s.path == "/custom/"
    assert s.stateless is False
    assert s.log_level == "DEBUG"
    assert s.azure_sql_server == "foo.database.windows.net"
    assert s.azure_sql_database == "wfm"
    assert (
        s.azure_sql_managed_identity_client_id
        == "11111111-2222-3333-4444-555555555555"
    )
    assert s.environment == "azure"


def test_extra_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown ``MCP_*`` vars must not blow up Settings construction.

    The dev container ships dozens of env vars; we don't want a stray
    ``MCP_FOO`` from a future phase-2 issue to crash the scaffold.
    """
    monkeypatch.setenv("MCP_FUTURE_KNOB", "xyz")
    Settings()  # should not raise
