"""Shared pytest fixtures for the backend test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

# Required env vars (alias names from PLAN.md §14). Kept here so that any
# test that constructs ``Settings()`` does so against a deterministic
# environment regardless of what the developer has in their shell.
_REQUIRED_ENV: dict[str, str] = {
    "FOUNDRY_PROJECT_ENDPOINT": "https://test.foundry.local/api/projects/test",
    "MCP_SERVER_URL": "https://test.local/mcp-api-dev/mcp/",
    "AZURE_COSMOS_ENDPOINT": "https://test.documents.azure.com:443/",
    "APPLICATIONINSIGHTS_CONNECTION_STRING": (
        "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
        "IngestionEndpoint=https://test.in.applicationinsights.azure.com/"
    ),
    "HMAC_SHARED_SECRET": "test-secret-do-not-use-in-prod",
}


@pytest.fixture
def required_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """Populate the minimum required environment for ``Settings()``.

    Tests that need additional vars (or want to clear specific ones) can
    layer their own ``monkeypatch.setenv`` / ``delenv`` calls on top.
    """
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    yield _REQUIRED_ENV.copy()
