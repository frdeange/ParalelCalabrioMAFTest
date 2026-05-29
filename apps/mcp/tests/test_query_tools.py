"""Unit tests for :mod:`app.servers.query` — issue #20.

The :class:`~app.clients.sql.SqlDatabaseClient` is mocked everywhere;
no real database is contacted. The allowlist is injected via
:func:`app.servers.query.set_allowlist` so each test controls exactly
which tables the validator accepts.

The tests cover:

* the happy path (validator ok → mock ``execute`` → tool dict shape);
* validator rejection (raises with the validator reason, telemetry
  logged with ``outcome=rejected``);
* ``max_rows`` boundary cases (default, cap, rejection of int/bool/
  out-of-range values, respect for ``MCP_QUERY_MAX_ROWS_*``);
* the **normalised** SQL — not the original string — is what reaches
  ``SqlDatabaseClient.execute``;
* truncation flag and ``row_count`` propagation;
* allowlist caching (``get_allowlist`` fetches once, then reuses);
* propagation of ``SqlDatabaseClient`` failures;
* telemetry log payload shape (``sql_hash``, ``row_count``,
  ``truncated``, ``duration_ms``, ``tables``);
* discovery of the ``query_execute`` tool through the in-memory
  ``fastmcp.Client``.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pyodbc
import pytest
from fastmcp import Client

from app.clients.sql import QueryResult
from app.main import mcp
from app.servers.query import (
    execute,
    get_allowlist,
    get_sql_client,
    set_allowlist,
    set_sql_client,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_query_module_state() -> Any:
    """Clear the SQL-client + allowlist singletons around each test."""
    set_sql_client(None)
    set_allowlist(None)
    try:
        yield
    finally:
        set_sql_client(None)
        set_allowlist(None)


def _make_mock_client(*results: QueryResult) -> MagicMock:
    """Return a mock client whose ``execute`` yields ``results`` in order."""
    client = MagicMock()
    client.execute = AsyncMock(side_effect=list(results))
    return client


def _executed_args(mock_client: MagicMock, call_index: int = 0) -> tuple[Any, ...]:
    """Return the positional args passed to ``client.execute`` for that call."""
    args, _ = mock_client.execute.await_args_list[call_index]
    return tuple(args)


def _executed_kwargs(mock_client: MagicMock, call_index: int = 0) -> dict[str, Any]:
    """Return the keyword args passed to ``client.execute`` for that call."""
    _, kwargs = mock_client.execute.await_args_list[call_index]
    return dict(kwargs)


# ---------------------------------------------------------------------------
# SQL-client wiring
# ---------------------------------------------------------------------------


def test_set_sql_client_overrides_singleton() -> None:
    """``set_sql_client`` installs the given instance for later reads."""
    sentinel = MagicMock()
    set_sql_client(sentinel)
    assert get_sql_client() is sentinel


def test_set_sql_client_none_clears_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing ``None`` resets the cache; next ``get_sql_client`` rebuilds."""
    sentinel = MagicMock()
    set_sql_client(sentinel)
    set_sql_client(None)

    # Without the env vars ``_build_sql_client`` raises RuntimeError —
    # locking in the "no SQL configured" failure mode the discovery
    # tests rely on.
    monkeypatch.delenv("MCP_AZURE_SQL_SERVER", raising=False)
    monkeypatch.delenv("MCP_AZURE_SQL_DATABASE", raising=False)
    with pytest.raises(RuntimeError, match="MCP_AZURE_SQL_SERVER"):
        get_sql_client()


def test_get_sql_client_builds_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """With env configured, ``get_sql_client`` returns a real client.

    Constructing the client must not require an actual database;
    ``SqlDatabaseClient.__init__`` defers credential acquisition.
    """
    monkeypatch.setenv("MCP_AZURE_SQL_SERVER", "srv.database.windows.net")
    monkeypatch.setenv("MCP_AZURE_SQL_DATABASE", "wfm")
    client = get_sql_client()
    assert client is not None
    # Subsequent call returns the cached instance, not a fresh one.
    assert get_sql_client() is client


# ---------------------------------------------------------------------------
# Allowlist caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_allowlist_fetches_from_catalog_on_first_call() -> None:
    """First read fires a SELECT against ``_metadata.catalog_tables``."""
    catalog_rows = QueryResult(
        rows=[
            {"table_name": "analytics.vw_PersonDetail"},
            {"table_name": "analytics.vw_AbsenceRequest"},
        ],
        truncated=False,
    )
    mock_client = _make_mock_client(catalog_rows)
    set_sql_client(mock_client)

    allowlist = await get_allowlist()

    assert allowlist == frozenset(
        {"analytics.vw_PersonDetail", "analytics.vw_AbsenceRequest"}
    )
    # SQL must filter on ``is_active = 1`` (PLAN.md §9 Decision D9).
    sql = _executed_args(mock_client)[0]
    assert "_metadata.catalog_tables" in sql
    assert "is_active = 1" in sql
    # An explicit high ``max_rows`` must be passed so the fetch never
    # inherits ``SqlDatabaseClient``'s 1000-row default and silently
    # truncates the allowlist (PR #71 review).
    kwargs = _executed_kwargs(mock_client)
    assert kwargs["max_rows"] >= 10_000


@pytest.mark.asyncio
async def test_get_allowlist_raises_when_catalog_fetch_truncates() -> None:
    """A truncated catalog read must fail loudly, not yield a partial allowlist.

    Otherwise ``query.execute`` would silently reject valid tables
    that fell off the truncated tail — the failure mode the PR #71
    review flagged.
    """
    # ``truncated=True`` simulates the catalog growing past
    # ``_ALLOWLIST_FETCH_MAX_ROWS``.
    catalog_rows = QueryResult(
        rows=[{"table_name": "analytics.vw_PersonDetail"}],
        truncated=True,
    )
    mock_client = _make_mock_client(catalog_rows)
    set_sql_client(mock_client)

    with pytest.raises(RuntimeError, match="partial"):
        await get_allowlist()


@pytest.mark.asyncio
async def test_get_allowlist_caches_subsequent_calls() -> None:
    """The catalog query runs once per process; later reads are cached."""
    catalog_rows = QueryResult(
        rows=[{"table_name": "analytics.vw_PersonDetail"}],
        truncated=False,
    )
    mock_client = _make_mock_client(catalog_rows)
    set_sql_client(mock_client)

    first = await get_allowlist()
    second = await get_allowlist()

    assert first is second
    assert mock_client.execute.await_count == 1


@pytest.mark.asyncio
async def test_set_allowlist_override_skips_catalog_fetch() -> None:
    """Tests / ops paths can inject an allowlist and bypass the catalog."""
    injected = frozenset({"analytics.vw_PersonDetail"})
    set_allowlist(injected)

    # No mock client wired — if the code tried to fetch we would get
    # a RuntimeError from ``_build_sql_client``.
    assert await get_allowlist() is injected


# ---------------------------------------------------------------------------
# execute — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_returns_rows_row_count_truncated() -> None:
    """Acceptance: returns ``{rows, row_count, truncated}``."""
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    rows = [{"agent_id": 1, "name": "Ada"}, {"agent_id": 2, "name": "Bea"}]
    mock_client = _make_mock_client(QueryResult(rows=rows, truncated=False))
    set_sql_client(mock_client)

    result = await execute("SELECT agent_id, name FROM analytics.vw_PersonDetail")

    assert result == {"rows": rows, "row_count": 2, "truncated": False}


@pytest.mark.asyncio
async def test_execute_passes_normalized_sql_to_client() -> None:
    """The normalised (comment-stripped) SQL is what reaches the client.

    PLAN.md §9 audit pipelines join on the SQL text that actually ran,
    not the agent-typed string with its scratch comments.
    """
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    mock_client = _make_mock_client(QueryResult(rows=[], truncated=False))
    set_sql_client(mock_client)

    original = (
        "/* agent scratchpad */\n"
        "SELECT agent_id FROM analytics.vw_PersonDetail -- inline note"
    )
    await execute(original)

    sent_sql = _executed_args(mock_client)[0]
    assert "scratchpad" not in sent_sql
    assert "inline note" not in sent_sql
    assert "analytics.vw_persondetail" in sent_sql.lower()


@pytest.mark.asyncio
async def test_execute_uses_default_max_rows_from_settings() -> None:
    """Calling without ``max_rows`` defaults to ``query_max_rows_default``."""
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    mock_client = _make_mock_client(QueryResult(rows=[], truncated=False))
    set_sql_client(mock_client)

    await execute("SELECT 1 FROM analytics.vw_PersonDetail")

    # ``SqlDatabaseClient.execute`` is called with ``max_rows=200`` (the
    # default in Settings) — passed as a keyword argument by the tool.
    assert _executed_kwargs(mock_client)["max_rows"] == 200


@pytest.mark.asyncio
async def test_execute_forwards_explicit_max_rows() -> None:
    """Explicit ``max_rows`` is forwarded verbatim to the client."""
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    mock_client = _make_mock_client(QueryResult(rows=[], truncated=False))
    set_sql_client(mock_client)

    await execute("SELECT 1 FROM analytics.vw_PersonDetail", max_rows=7)

    assert _executed_kwargs(mock_client)["max_rows"] == 7


@pytest.mark.asyncio
async def test_execute_propagates_truncated_flag() -> None:
    """``QueryResult.truncated=True`` surfaces in the tool response."""
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    rows = [{"x": i} for i in range(3)]
    mock_client = _make_mock_client(QueryResult(rows=rows, truncated=True))
    set_sql_client(mock_client)

    result = await execute("SELECT x FROM analytics.vw_PersonDetail", max_rows=3)

    assert result["truncated"] is True
    assert result["row_count"] == 3


# ---------------------------------------------------------------------------
# execute — validator rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_rejects_non_select() -> None:
    """A DELETE statement is killed before any client call."""
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    mock_client = _make_mock_client()  # no results queued — must not be called
    set_sql_client(mock_client)

    with pytest.raises(ValueError, match="query rejected"):
        await execute("DELETE FROM analytics.vw_PersonDetail")

    mock_client.execute.assert_not_called()


@pytest.mark.asyncio
async def test_execute_rejects_table_outside_allowlist() -> None:
    """Tables not in the allowlist are rejected with a typed error."""
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    mock_client = _make_mock_client()
    set_sql_client(mock_client)

    with pytest.raises(ValueError, match="query rejected"):
        await execute("SELECT * FROM analytics.vw_Forbidden")

    mock_client.execute.assert_not_called()


@pytest.mark.asyncio
async def test_execute_rejection_message_carries_validator_reason() -> None:
    """The validator's ``reason`` is preserved verbatim in the error."""
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    set_sql_client(_make_mock_client())

    with pytest.raises(ValueError) as exc_info:
        await execute("DROP TABLE analytics.vw_PersonDetail")

    # The wording comes from ``app.security.sql_validator``; we assert
    # only the substring that proves it propagated through, not the
    # exact phrasing (so the validator can refine the wording later
    # without breaking this test).
    assert "query rejected" in str(exc_info.value)


# ---------------------------------------------------------------------------
# execute — max_rows validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value, match",
    [
        pytest.param(0, "between 1 and", id="zero"),
        pytest.param(-1, "between 1 and", id="negative"),
        pytest.param(1_001, "between 1 and", id="above-cap"),
        pytest.param("10", "must be an int", id="string"),
        pytest.param(1.5, "must be an int", id="float"),
        pytest.param(True, "must be an int", id="bool-true"),
        pytest.param(False, "must be an int", id="bool-false"),
    ],
)
@pytest.mark.asyncio
async def test_execute_rejects_invalid_max_rows(bad_value: Any, match: str) -> None:
    """``max_rows`` must be a positive int within the configured cap."""
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    mock_client = _make_mock_client()
    set_sql_client(mock_client)

    with pytest.raises(ValueError, match=match):
        await execute("SELECT 1 FROM analytics.vw_PersonDetail", max_rows=bad_value)

    mock_client.execute.assert_not_called()


@pytest.mark.asyncio
async def test_execute_accepts_max_rows_at_cap() -> None:
    """The hard cap is *inclusive* — ``max_rows = cap`` must succeed."""
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    mock_client = _make_mock_client(QueryResult(rows=[], truncated=False))
    set_sql_client(mock_client)

    await execute("SELECT 1 FROM analytics.vw_PersonDetail", max_rows=1_000)

    assert _executed_kwargs(mock_client)["max_rows"] == 1_000


@pytest.mark.asyncio
async def test_execute_respects_env_cap_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lowering ``MCP_QUERY_MAX_ROWS_CAP`` tightens the per-call ceiling."""
    monkeypatch.setenv("MCP_QUERY_MAX_ROWS_CAP", "50")
    monkeypatch.setenv("MCP_QUERY_MAX_ROWS_DEFAULT", "10")
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    mock_client = _make_mock_client(QueryResult(rows=[], truncated=False))
    set_sql_client(mock_client)

    # 50 is now the inclusive cap; 51 must fail.
    with pytest.raises(ValueError, match="between 1 and 50"):
        await execute("SELECT 1 FROM analytics.vw_PersonDetail", max_rows=51)


# ---------------------------------------------------------------------------
# execute — telemetry log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_logs_telemetry_on_success(caplog: pytest.LogCaptureFixture) -> None:
    """A structured ``telemetry=query_execute`` log line is emitted."""
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    rows = [{"agent_id": 1}]
    mock_client = _make_mock_client(QueryResult(rows=rows, truncated=False))
    set_sql_client(mock_client)

    caplog.set_level(logging.INFO, logger="app.servers.query")
    await execute("SELECT agent_id FROM analytics.vw_PersonDetail")

    telemetry_records = [
        r for r in caplog.records if getattr(r, "telemetry", None) == "query_execute"
    ]
    assert telemetry_records, "no telemetry log emitted"
    record = telemetry_records[-1]
    assert record.outcome == "ok"  # type: ignore[attr-defined]
    assert record.row_count == 1  # type: ignore[attr-defined]
    assert record.truncated is False  # type: ignore[attr-defined]
    assert isinstance(record.duration_ms, int)  # type: ignore[attr-defined]
    assert record.duration_ms >= 0  # type: ignore[attr-defined]
    assert "analytics.vw_PersonDetail" in record.tables  # type: ignore[attr-defined]
    # Hash is the leading 16 hex chars of SHA-256 of the normalised SQL.
    assert len(record.sql_hash) == 16  # type: ignore[attr-defined]
    assert all(c in "0123456789abcdef" for c in record.sql_hash)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_execute_logs_telemetry_on_rejection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Rejected queries emit telemetry with the **same shape** as success.

    The rejection log line carries ``row_count=0``, ``truncated=False``,
    ``tables=[]``, ``max_rows`` and a measured ``duration_ms`` so
    downstream pipelines can flatten both outcomes onto a single
    schema (PR #71 review).
    """
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    set_sql_client(_make_mock_client())

    caplog.set_level(logging.INFO, logger="app.servers.query")
    with pytest.raises(ValueError):
        await execute("DELETE FROM analytics.vw_PersonDetail", max_rows=42)

    telemetry_records = [
        r for r in caplog.records if getattr(r, "telemetry", None) == "query_execute"
    ]
    assert telemetry_records, "no telemetry log emitted on rejection"
    record = telemetry_records[-1]
    assert record.outcome == "rejected"  # type: ignore[attr-defined]
    assert record.reason  # type: ignore[attr-defined]
    # Hash is taken over the *original* SQL on rejection (there is no
    # normalised form to hash) — covers the agent-typed text.
    expected_hash = hashlib.sha256(
        b"DELETE FROM analytics.vw_PersonDetail"
    ).hexdigest()[:16]
    assert record.sql_hash == expected_hash  # type: ignore[attr-defined]
    # Symmetric shape with the success path — zero-valued mirrors.
    assert record.row_count == 0  # type: ignore[attr-defined]
    assert record.truncated is False  # type: ignore[attr-defined]
    assert record.tables == []  # type: ignore[attr-defined]
    assert record.max_rows == 42  # type: ignore[attr-defined]
    assert isinstance(record.duration_ms, int)  # type: ignore[attr-defined]
    assert record.duration_ms >= 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# execute — SQL-client error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_propagates_pyodbc_errors() -> None:
    """``pyodbc.Error`` from the client bubbles up unchanged.

    The MCP layer surfaces it to the agent so the actual cause
    (permission denied, transient blip, …) is visible — not a generic
    "internal error".
    """
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    mock_client = MagicMock()
    mock_client.execute = AsyncMock(side_effect=pyodbc.Error("permission denied"))
    set_sql_client(mock_client)

    with pytest.raises(pyodbc.Error, match="permission denied"):
        await execute("SELECT 1 FROM analytics.vw_PersonDetail")


# ---------------------------------------------------------------------------
# Discovery — the tool is reachable through the mounted root server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_execute_is_discoverable() -> None:
    """``query_execute`` appears in MCP discovery alongside ``query_ping``."""
    async with Client(mcp) as client:
        tools = await client.list_tools()

    names = {tool.name for tool in tools}
    assert "query_execute" in names
    assert "query_ping" in names


@pytest.mark.asyncio
async def test_query_ping_returns_ok_payload() -> None:
    """Cheap sanity check on the placeholder probe kept from #16."""
    async with Client(mcp) as client:
        result = await client.call_tool("query_ping", {})
    assert result.data == {"namespace": "query", "status": "ok"}


@pytest.mark.asyncio
async def test_query_execute_roundtrip_via_in_memory_client() -> None:
    """End-to-end through the mount layer: dispatch → tool → mock client."""
    set_allowlist(frozenset({"analytics.vw_PersonDetail"}))
    rows = [{"agent_id": 42}]
    mock_client = _make_mock_client(QueryResult(rows=rows, truncated=False))
    set_sql_client(mock_client)

    async with Client(mcp) as client:
        result = await client.call_tool(
            "query_execute",
            {"sql": "SELECT agent_id FROM analytics.vw_PersonDetail"},
        )

    assert result.data == {"rows": rows, "row_count": 1, "truncated": False}
