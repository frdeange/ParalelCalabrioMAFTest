"""Shared fixtures for the MCP test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip MCP_* env vars so :func:`app.settings.get_settings` boots
    from defaults regardless of the host environment (CI, dev container,
    developer's ``.env``).
    """
    for var in ("MCP_PATH", "MCP_STATELESS", "MCP_LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)
