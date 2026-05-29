"""Unit tests for :mod:`app.servers.schema` — issue #18.

We mock the :class:`~app.clients.sql.SqlDatabaseClient` instance and
assert what each tool sends to it. No real database is contacted.

Each test that calls a tool injects its mock through
:func:`app.servers.schema.set_sql_client` and the autouse
``_reset_schema_sql_client`` fixture clears it again afterwards so
state does not leak between tests.

The tests deliberately verify *the SQL text the tools send to the
client*. That text is the public contract between the MCP server and
the catalog metadata schema seeded by ``database/03-seed-data.sql`` —
a silent rename of, say, ``_metadata.catalog_tables.is_active`` would
need both PRs to land together, and these assertions are the canary
for that contract.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client

from app.clients.sql import QueryResult
from app.servers import schema as schema_module
from app.servers.schema import (
    describe_table,
    get_distinct_values,
    get_sql_client,
    list_tables,
    schema_server,
    search_tables,
    set_sql_client,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_schema_sql_client() -> Any:
    """Reset the module-level SQL-client singleton around each test."""
    set_sql_client(None)
    try:
        yield
    finally:
        set_sql_client(None)


def _make_mock_client(*results: QueryResult) -> MagicMock:
    """Return a mock client whose ``execute`` yields ``results`` in order."""
    client = MagicMock()
    client.execute = AsyncMock(side_effect=list(results))
    return client


def _executed_sql(mock_client: MagicMock, call_index: int = 0) -> str:
    """Return the SQL text passed to ``client.execute`` for that call."""
    args, _ = mock_client.execute.await_args_list[call_index]
    return str(args[0])


def _executed_params(mock_client: MagicMock, call_index: int = 0) -> tuple[Any, ...]:
    """Return the params tuple passed to ``client.execute`` for that call."""
    args, _ = mock_client.execute.await_args_list[call_index]
    return args[1] if len(args) >= 2 else ()


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

    rebuilt = MagicMock()
    monkeypatch.setattr(schema_module, "_build_sql_client", lambda: rebuilt)
    assert get_sql_client() is rebuilt


def test_get_sql_client_raises_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ``MCP_AZURE_SQL_*`` env vars yield a clear runtime error."""
    monkeypatch.delenv("MCP_AZURE_SQL_SERVER", raising=False)
    monkeypatch.delenv("MCP_AZURE_SQL_DATABASE", raising=False)

    with pytest.raises(RuntimeError, match="MCP_AZURE_SQL_SERVER"):
        get_sql_client()


def test_get_sql_client_builds_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """With config present, a real :class:`SqlDatabaseClient` is built."""
    monkeypatch.setenv("MCP_AZURE_SQL_SERVER", "srv.database.windows.net")
    monkeypatch.setenv("MCP_AZURE_SQL_DATABASE", "wfm")
    monkeypatch.setenv("MCP_AZURE_SQL_MANAGED_IDENTITY_CLIENT_ID", "")
    monkeypatch.setenv("MCP_ENVIRONMENT", "local")

    # Avoid the real DefaultAzureCredential construction.
    monkeypatch.setattr(
        "app.clients.sql.DefaultAzureCredential",
        lambda **_kwargs: MagicMock(),
    )

    client = get_sql_client()
    # The client is cached — second call returns the same instance.
    assert get_sql_client() is client


# ---------------------------------------------------------------------------
# list_tables
# ---------------------------------------------------------------------------


async def test_list_tables_returns_catalog_rows() -> None:
    rows = [
        {
            "table_name": "analytics.vw_AbsenceRequest",
            "schema_name": "analytics",
            "description": "Absence requests",
            "keywords": "absence,leave",
        },
        {
            "table_name": "analytics.vw_PersonDetail",
            "schema_name": "analytics",
            "description": "Employee profile",
            "keywords": "agent,person",
        },
    ]
    set_sql_client(_make_mock_client(QueryResult(rows=rows, truncated=False)))

    result = await list_tables()

    assert result == {"tables": rows, "count": 2}


async def test_list_tables_filters_by_is_active() -> None:
    mock_client = _make_mock_client(QueryResult(rows=[], truncated=False))
    set_sql_client(mock_client)

    await list_tables()

    sql = _executed_sql(mock_client)
    assert "_metadata.catalog_tables" in sql
    assert "is_active = 1" in sql
    assert "ORDER BY table_name" in sql


# ---------------------------------------------------------------------------
# search_tables — input validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   "])
async def test_search_tables_rejects_empty_query(bad: str) -> None:
    set_sql_client(_make_mock_client())
    with pytest.raises(ValueError, match="query"):
        await search_tables(query=bad)


async def test_search_tables_rejects_non_string_query() -> None:
    set_sql_client(_make_mock_client())
    with pytest.raises(ValueError, match="query"):
        await search_tables(query=123)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [0, -1, 51, 1000])
async def test_search_tables_rejects_out_of_range_top_k(bad: int) -> None:
    set_sql_client(_make_mock_client())
    with pytest.raises(ValueError, match="top_k"):
        await search_tables(query="abc", top_k=bad)


async def test_search_tables_rejects_boolean_top_k() -> None:
    """``True`` is a Python ``int`` but never a valid count."""
    set_sql_client(_make_mock_client())
    with pytest.raises(ValueError, match="top_k"):
        await search_tables(query="abc", top_k=True)


# ---------------------------------------------------------------------------
# search_tables — happy path
# ---------------------------------------------------------------------------


async def test_search_tables_builds_lowercased_like_pattern() -> None:
    mock_client = _make_mock_client(QueryResult(rows=[], truncated=False))
    set_sql_client(mock_client)

    await search_tables(query="  Overtime ", top_k=5)

    sql = _executed_sql(mock_client)
    params = _executed_params(mock_client)
    # top_k first, then four copies of the LIKE pattern (three OR
    # branches + one in the ORDER BY ranking expression).
    assert params == (5, "%overtime%", "%overtime%", "%overtime%", "%overtime%")
    assert "TOP (?)" in sql
    assert "LOWER(table_name)" in sql
    assert "LOWER(description)" in sql
    assert "LOWER(keywords)" in sql
    assert "is_active = 1" in sql


async def test_search_tables_returns_count_and_query() -> None:
    rows = [
        {
            "table_name": "analytics.vw_OvertimeRequest",
            "schema_name": "analytics",
            "description": "OT",
            "keywords": "overtime",
        }
    ]
    set_sql_client(_make_mock_client(QueryResult(rows=rows, truncated=False)))

    result = await search_tables(query="overtime")

    assert result == {"query": "overtime", "tables": rows, "count": 1}


# ---------------------------------------------------------------------------
# describe_table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   "])
async def test_describe_table_rejects_empty_name(bad: str) -> None:
    set_sql_client(_make_mock_client())
    with pytest.raises(ValueError, match="table_name"):
        await describe_table(table_name=bad)


async def test_describe_table_unknown_table_raises() -> None:
    set_sql_client(_make_mock_client(QueryResult(rows=[], truncated=False)))
    with pytest.raises(ValueError, match="unknown or inactive"):
        await describe_table(table_name="analytics.no_such_table")


async def test_describe_table_aggregates_header_columns_and_joins() -> None:
    header = {
        "table_name": "analytics.vw_PersonDetail",
        "schema_name": "analytics",
        "description": "Employee profile",
        "keywords": "agent,person",
    }
    column_rows = [
        {
            "column_name": "agent_id",
            "data_type": "INT",
            "is_nullable": False,
            "description": "Agent identifier",
            "display_name": "Agent ID",
        },
        {
            "column_name": "full_name",
            "data_type": "NVARCHAR(150)",
            "is_nullable": False,
            "description": "Legal name",
            "display_name": "Agent Name",
        },
    ]
    join_rows = [
        {
            "source_table": "analytics.vw_PersonDetail",
            "target_table": "analytics.vw_AbsenceRequest",
            "join_column": "agent_id",
            "join_type": "INNER",
        }
    ]

    mock_client = _make_mock_client(
        QueryResult(rows=[header], truncated=False),
        QueryResult(rows=column_rows, truncated=False),
        QueryResult(rows=join_rows, truncated=False),
    )
    set_sql_client(mock_client)

    result = await describe_table(table_name="analytics.vw_PersonDetail")

    assert result == {
        "table_name": header["table_name"],
        "schema_name": header["schema_name"],
        "description": header["description"],
        "keywords": header["keywords"],
        "columns": column_rows,
        "joins": join_rows,
    }
    # 3 calls in the expected order against the expected tables.
    assert mock_client.execute.await_count == 3
    assert "_metadata.catalog_tables" in _executed_sql(mock_client, 0)
    assert "_metadata.catalog_columns" in _executed_sql(mock_client, 1)
    assert "_metadata.catalog_joins" in _executed_sql(mock_client, 2)
    # Joins query passes the table name on **both** sides so we collect
    # incoming and outgoing relationships.
    assert _executed_params(mock_client, 2) == (
        "analytics.vw_PersonDetail",
        "analytics.vw_PersonDetail",
    )


# ---------------------------------------------------------------------------
# get_distinct_values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("table_name", ""),
        ("table_name", "   "),
        ("column_name", ""),
        ("column_name", "   "),
    ],
)
async def test_get_distinct_values_rejects_empty_names(field: str, value: str) -> None:
    set_sql_client(_make_mock_client())
    kwargs: dict[str, Any] = {
        "table_name": "analytics.vw_PersonDetail",
        "column_name": "status",
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=field):
        await get_distinct_values(**kwargs)


@pytest.mark.parametrize("bad", [0, -1, 1001, 99999])
async def test_get_distinct_values_rejects_out_of_range_top_n(bad: int) -> None:
    set_sql_client(_make_mock_client())
    with pytest.raises(ValueError, match="top_n"):
        await get_distinct_values(
            table_name="analytics.vw_PersonDetail",
            column_name="status",
            top_n=bad,
        )


async def test_get_distinct_values_rejects_unknown_catalog_pair() -> None:
    set_sql_client(_make_mock_client(QueryResult(rows=[], truncated=False)))
    with pytest.raises(ValueError, match="unknown or inactive"):
        await get_distinct_values(
            table_name="analytics.vw_PersonDetail",
            column_name="nope",
        )


async def test_get_distinct_values_happy_path() -> None:
    catalog_row = {"schema_name": "analytics", "column_name": "status"}
    value_rows = [
        {"value": "Approved"},
        {"value": "Pending"},
        {"value": "Rejected"},
    ]
    mock_client = _make_mock_client(
        QueryResult(rows=[catalog_row], truncated=False),
        QueryResult(rows=value_rows, truncated=False),
    )
    set_sql_client(mock_client)

    result = await get_distinct_values(
        table_name="analytics.vw_AbsenceRequest",
        column_name="status",
        top_n=10,
    )

    assert result == {
        "table_name": "analytics.vw_AbsenceRequest",
        "column_name": "status",
        "values": ["Approved", "Pending", "Rejected"],
        "count": 3,
        "truncated": False,
    }
    # Data query uses the schema/table/column from the catalog, wrapped
    # in [ ] brackets, and TOP (?) bound to top_n.
    data_sql = _executed_sql(mock_client, 1)
    data_params = _executed_params(mock_client, 1)
    assert "[analytics].[vw_AbsenceRequest]" in data_sql
    assert "[status]" in data_sql
    assert "IS NOT NULL" in data_sql
    assert "ORDER BY [status]" in data_sql
    assert data_params == (10,)


async def test_get_distinct_values_propagates_truncated_flag() -> None:
    catalog_row = {"schema_name": "analytics", "column_name": "team_name"}
    mock_client = _make_mock_client(
        QueryResult(rows=[catalog_row], truncated=False),
        QueryResult(rows=[{"value": "Alpha"}], truncated=True),
    )
    set_sql_client(mock_client)

    result = await get_distinct_values(
        table_name="analytics.vw_PersonDetail",
        column_name="team_name",
        top_n=1,
    )

    assert result["truncated"] is True


async def test_get_distinct_values_rejects_unsafe_catalog_identifier() -> None:
    """If catalog rows ever contained a bad identifier, the inner guard
    rejects it before any SQL is rendered against the data table.
    """
    bad_catalog_row = {"schema_name": "analytics;DROP", "column_name": "status"}
    mock_client = _make_mock_client(QueryResult(rows=[bad_catalog_row], truncated=False))
    set_sql_client(mock_client)

    with pytest.raises(ValueError, match="schema_name"):
        await get_distinct_values(
            table_name="analytics.vw_AbsenceRequest",
            column_name="status",
        )
    # Only the catalog query ran; the data query never reached the client.
    assert mock_client.execute.await_count == 1


# ---------------------------------------------------------------------------
# Discovery — the 4 new tools are visible through FastMCP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_lists_all_four_schema_tools() -> None:
    async with Client(schema_server) as client:
        tools = await client.list_tools()
    names = {tool.name for tool in tools}
    assert {"list_tables", "search_tables", "describe_table", "get_distinct_values"}.issubset(names)
