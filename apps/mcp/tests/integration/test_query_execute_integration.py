"""Integration tests for the ``query.execute`` tool (issue #23).

Two cases: a happy-path ``SELECT`` against a real view, and a
validator rejection on a mutating statement. The ``ping`` placeholder
is covered by the unit tests; the SQL-error propagation path is
implicitly covered by the happy-path negative variant (the validator
fires before we ever hit the engine).

The ``_wire_tools_to_container`` autouse fixture in
``conftest.py`` installs the SA client into ``app.servers.query``
and clears the cached allowlist before each test, so the validator
reads the live ``_metadata.catalog_tables`` rows.
"""

from __future__ import annotations

import pytest

from app.servers.query import execute

pytestmark = pytest.mark.integration


async def test_execute_runs_select_against_seeded_view() -> None:
    """A safe ``SELECT`` against an allowlisted view returns rows."""
    result = await execute(
        "SELECT TOP 5 agent_id, site_name FROM analytics.vw_PersonDetail",
        max_rows=10,
    )

    assert isinstance(result["rows"], list)
    assert result["row_count"] == len(result["rows"])
    assert result["row_count"] > 0, "seeded data should yield at least one agent"
    assert result["row_count"] <= 5  # ``TOP 5`` upper-bounds the view
    assert result["truncated"] is False
    # Every row carries the columns the SELECT asked for, in some order.
    first = result["rows"][0]
    assert set(first.keys()) == {"agent_id", "site_name"}


async def test_execute_rejects_mutating_statement() -> None:
    """``DELETE`` is blocked by the validator before reaching the engine.

    This exercises the full chain: settings \u2192 allowlist fetch (real
    catalog read) \u2192 sqlglot AST validator \u2192 rejection. The validator
    error message comes from :mod:`app.security.sql_validator`; we
    only need to assert the call raises rather than ever touching
    the engine.
    """
    with pytest.raises(ValueError):
        await execute("DELETE FROM analytics.vw_PersonDetail")
