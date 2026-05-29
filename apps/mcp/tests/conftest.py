"""Shared fixtures + collection hooks for the MCP test suite.

The env-var scrub runs from ``pytest_configure`` rather than an
autouse fixture so it fires *before* any test module is imported.
This matters because :mod:`app.main` constructs :class:`app.settings.Settings`
at import time (so ``uvicorn app.main:app`` keeps working as a plain
ASGI entrypoint), and an autouse fixture would only run after pytest
has already imported the test module and triggered :class:`Settings`
construction. A stray ``MCP_*`` value in the host environment would
then break collection rather than the individual test.
"""

from __future__ import annotations

import os

import pytest

# Keep this list in sync with the variables declared in
# ``apps/mcp/.env.example``. New scaffold-only knobs must be added here.
_MCP_SCAFFOLD_ENV_VARS = (
    "MCP_PATH",
    "MCP_STATELESS",
    "MCP_LOG_LEVEL",
    "MCP_AZURE_SQL_SERVER",
    "MCP_AZURE_SQL_DATABASE",
    "MCP_AZURE_SQL_MANAGED_IDENTITY_CLIENT_ID",
    "MCP_ENVIRONMENT",
    "MCP_QUERY_MAX_ROWS_DEFAULT",
    "MCP_QUERY_MAX_ROWS_CAP",
)


def pytest_configure(config: pytest.Config) -> None:  # noqa: ARG001 - pytest hook signature
    """Strip ``MCP_*`` env vars before any test module is collected.

    Pre-collection hook so the scrub happens before ``app.main`` (and
    therefore ``Settings()``) is imported by any test module.
    """
    for var in _MCP_SCAFFOLD_ENV_VARS:
        os.environ.pop(var, None)


def pytest_collection_modifyitems(
    config: pytest.Config,  # noqa: ARG001 - pytest hook signature
    items: list[pytest.Item],
) -> None:
    """Skip ``@pytest.mark.integration`` tests unless explicitly opted in.

    Integration tests under ``tests/integration/`` spin up an mssql
    testcontainer (issue #23). They cost ~30-60 seconds of container
    startup and require a running Docker daemon, so we keep them out
    of the default ``pytest`` invocation. Opt in by exporting
    ``MCP_RUN_INTEGRATION=1`` before running the suite (CI does this
    in a dedicated job after the fast unit tests pass).

    We deliberately use a runtime env var rather than a pytest CLI
    flag so the same gate works for ``pytest`` invocations made by
    editor integrations, ``tox`` recipes and CI without needing
    bespoke argv plumbing.
    """
    if os.environ.get("MCP_RUN_INTEGRATION", "").strip() == "1":
        return
    skip_integration = pytest.mark.skip(
        reason="integration tests are opt-in; set MCP_RUN_INTEGRATION=1 to enable",
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
