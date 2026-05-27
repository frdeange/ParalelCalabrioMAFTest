"""Shared pytest fixtures for the backend test suite.

The module-level ``os.environ.setdefault`` calls below run **before any
test file is collected** — that's how we satisfy ``Settings()``'s required
variables at import time without coupling tests to the developer's shell.
Individual tests can override or unset specific vars via ``monkeypatch``.
"""

from __future__ import annotations

import os

# Minimum required env for ``Settings()`` (see PLAN.md §14). ``setdefault``
# means real environment values (e.g. CI secrets) win when present.
_DEFAULTS: dict[str, str] = {
    "FOUNDRY_PROJECT_ENDPOINT": "https://test.foundry.local/api/projects/test",
    "MCP_SERVER_URL": "https://test.local/mcp-api-dev/mcp/",
    "AZURE_COSMOS_ENDPOINT": "https://test.documents.azure.com:443/",
    "APPLICATIONINSIGHTS_CONNECTION_STRING": (
        "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
        "IngestionEndpoint=https://test.in.applicationinsights.azure.com/"
    ),
    "HMAC_SHARED_SECRET": "test-secret-do-not-use-in-prod",
}

for _key, _value in _DEFAULTS.items():
    os.environ.setdefault(_key, _value)
