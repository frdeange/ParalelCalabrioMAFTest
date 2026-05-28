"""``schema`` namespace sub-server.

Hosts the schema-introspection tools listed in PLAN.md §6.3 Day-1:

* ``list_tables``        — issue #18
* ``search_tables``      — issue #18
* ``describe_table``     — issue #18
* ``get_distinct_values`` — issue #18

Tools read from ``_metadata.catalog_tables`` / ``catalog_columns`` /
``catalog_joins`` (seeded by ``database/03-seed-data.sql``). The LLM
never sees ``INFORMATION_SCHEMA`` or ``sys.extended_properties``
directly — the catalog is the **single** allowlist the agent observes
(PLAN.md §9). ``is_active = 0`` rows are invisible.

The placeholder ``ping`` tool from #16 is kept as a cheap liveness
probe. It will be replaced by a richer ``health`` tool in a later
issue if the LLM finds it noisy.

SQL client wiring
-----------------
Schema tools talk to the database through one shared
:class:`~app.clients.sql.SqlDatabaseClient`. The client is built
lazily on first use from :mod:`app.settings` so importing this module
does not require any ``MCP_AZURE_SQL_*`` env var to be set (the
discovery tests in ``tests/test_discovery.py`` rely on this).

Tests inject mocks via :func:`set_sql_client`; the function is also
the documented seam that the future ``main.py`` startup hook will use
once Phase 2 wires connection pooling or shared credentials across
namespaces.
"""

from __future__ import annotations

import re
from typing import Any

from fastmcp import FastMCP

from ..clients.sql import SqlDatabaseClient
from ..settings import get_settings

schema_server: FastMCP = FastMCP(name="calabrio-mcp-schema")


# ---------------------------------------------------------------------------
# SQL client wiring
# ---------------------------------------------------------------------------

# Process-wide singleton. Lazily built on first call to keep ``import
# app.main`` working in environments without SQL configured (CI for the
# discovery tests, local boot before ``az login`` finishes, etc.).
_sql_client: SqlDatabaseClient | None = None


def _build_sql_client() -> SqlDatabaseClient:
    """Construct a :class:`SqlDatabaseClient` from current settings.

    Raises
    ------
    RuntimeError
        If ``MCP_AZURE_SQL_SERVER`` / ``MCP_AZURE_SQL_DATABASE`` are
        unset. The schema tools need both; failing here turns a vague
        ``ValueError`` from the client into a tool-level error message
        the operator can act on.
    """
    settings = get_settings()
    if not settings.azure_sql_server or not settings.azure_sql_database:
        raise RuntimeError(
            "Schema tools require MCP_AZURE_SQL_SERVER and MCP_AZURE_SQL_DATABASE "
            "to be configured (see PLAN.md §14)."
        )
    return SqlDatabaseClient(
        server=settings.azure_sql_server,
        database=settings.azure_sql_database,
        managed_identity_client_id=settings.azure_sql_managed_identity_client_id,
        environment=settings.environment,
    )


def get_sql_client() -> SqlDatabaseClient:
    """Return the process-wide SQL client, building it on first use."""
    global _sql_client
    if _sql_client is None:
        _sql_client = _build_sql_client()
    return _sql_client


def set_sql_client(client: SqlDatabaseClient | None) -> None:
    """Override (or clear) the cached SQL client.

    Tests use this to inject a mock; future startup code may use it to
    pre-warm the client with custom credentials. Passing ``None``
    forces the next call to :func:`get_sql_client` to rebuild from
    settings.
    """
    global _sql_client
    _sql_client = client


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

# Upper bounds on the row-limit knobs the LLM can request. ``top_k``
# is small because ``search_tables`` results are read by the agent in
# full; ``top_n`` is larger because ``get_distinct_values`` is the
# only path the agent has to discover categorical values.
_MAX_TOP_K = 50
_MAX_TOP_N = 1_000

# SQL Server identifier: starts with a letter or underscore, then
# letters/digits/underscores. We do **not** accept dots, brackets,
# quotes or spaces — those are signs of an unqualified injection
# attempt. Anything legitimate that needed those characters would
# already have been quoted by the catalog seed.
_VALID_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _require_nonempty_string(name: str, value: object) -> str:
    """Return ``value`` stripped, after asserting it is a non-empty str."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name!r} must be a non-empty string")
    return value.strip()


def _require_positive_int(name: str, value: int, max_value: int) -> int:
    """Return ``value`` after asserting it lies in ``[1, max_value]``.

    Raises :class:`ValueError` if ``value`` is not a real ``int`` or is
    outside the allowed range. We **reject** rather than silently clamp
    so the agent does not get back a different result than the one it
    asked for — a request for ``top_n=10_000`` should fail loudly, not
    quietly return ``1000``.
    """
    # ``bool`` is a subclass of ``int``; reject it explicitly so True/False
    # cannot be passed where a real count is expected.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name!r} must be an int, got {type(value).__name__}")
    if value < 1 or value > max_value:
        raise ValueError(f"{name!r} must be between 1 and {max_value}, got {value}")
    return value


def _require_valid_identifier(name: str, value: str) -> str:
    """Return ``value`` after asserting it is a safe bare SQL identifier."""
    if not _VALID_IDENT.match(value):
        raise ValueError(
            f"{name!r}={value!r} is not a valid SQL identifier "
            "(letters/underscore, then letters/digits/underscore)."
        )
    return value


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@schema_server.tool
def ping() -> dict[str, str]:
    """Return a static liveness payload.

    Cheap probe kept from #16 — useful for ACA health checks and curl
    smoke tests. The real schema-discovery tools live below.
    """
    return {"namespace": "schema", "status": "ok"}


@schema_server.tool
async def list_tables() -> dict[str, Any]:
    """List every active table/view visible to the agent.

    Returns rows from ``_metadata.catalog_tables`` filtered by
    ``is_active = 1``. Each row carries the qualified ``table_name``
    (e.g. ``analytics.vw_PersonDetail``), its ``schema_name``, a
    one-line ``description`` written for the LLM and a
    comma-separated ``keywords`` string consumed by
    :func:`search_tables`.

    Returns
    -------
    dict
        ``{"tables": [...], "count": int}``. ``tables`` is the row
        list in ``table_name`` order so output is deterministic.
    """
    client = get_sql_client()
    result = await client.execute(
        "SELECT table_name, schema_name, description, keywords "
        "FROM _metadata.catalog_tables "
        "WHERE is_active = 1 "
        "ORDER BY table_name"
    )
    return {"tables": result.rows, "count": len(result.rows)}


@schema_server.tool
async def search_tables(query: str, top_k: int = 10) -> dict[str, Any]:
    """Return the top-``top_k`` tables matching ``query``.

    Case-insensitive substring match against ``table_name +
    description + keywords``. Name matches sort before description /
    keyword matches so the most likely candidate is first.

    Parameters
    ----------
    query:
        Free-text the agent typed (e.g. ``"overtime"``,
        ``"absence requests"``). Whitespace is trimmed; empty input is
        rejected.
    top_k:
        Max rows to return. Must be in ``[1, 50]`` — out-of-range
        values raise ``ValueError`` rather than being silently capped.

    Returns
    -------
    dict
        ``{"query": str, "tables": [...], "count": int}``.
    """
    query_text = _require_nonempty_string("query", query)
    top_k = _require_positive_int("top_k", top_k, _MAX_TOP_K)

    # LIKE-pattern with lower-cased payload so the comparison is
    # case-insensitive regardless of the database collation.
    pattern = f"%{query_text.lower()}%"

    sql = (
        "SELECT TOP (?) table_name, schema_name, description, keywords "
        "FROM _metadata.catalog_tables "
        "WHERE is_active = 1 "
        "  AND ("
        "         LOWER(table_name)  LIKE ?"
        "      OR LOWER(description) LIKE ?"
        "      OR LOWER(keywords)    LIKE ?"
        "  ) "
        "ORDER BY "
        "  CASE WHEN LOWER(table_name) LIKE ? THEN 0 ELSE 1 END, "
        "  table_name"
    )
    client = get_sql_client()
    result = await client.execute(
        sql,
        (top_k, pattern, pattern, pattern, pattern),
    )
    return {"query": query_text, "tables": result.rows, "count": len(result.rows)}


@schema_server.tool
async def describe_table(table_name: str) -> dict[str, Any]:
    """Return the full schema description of ``table_name``.

    Aggregates three catalog queries:

    1. table header from ``_metadata.catalog_tables`` (rejected if
       missing or ``is_active = 0``),
    2. columns from ``_metadata.catalog_columns`` (with data types,
       nullability, descriptions, display names),
    3. joins from ``_metadata.catalog_joins`` (both directions —
       this table as source *or* as target).

    Parameters
    ----------
    table_name:
        Qualified name as stored in the catalog (e.g.
        ``analytics.vw_PersonDetail``). Case-sensitive — the catalog
        is canonical.

    Returns
    -------
    dict
        ``{table_name, schema_name, description, keywords, columns,
        joins}``.

    Raises
    ------
    ValueError
        If ``table_name`` is empty or not present in the catalog (or
        is marked ``is_active = 0``).
    """
    table_name = _require_nonempty_string("table_name", table_name)
    client = get_sql_client()

    header = await client.execute(
        "SELECT table_name, schema_name, description, keywords "
        "FROM _metadata.catalog_tables "
        "WHERE table_name = ? AND is_active = 1",
        (table_name,),
    )
    if not header.rows:
        raise ValueError(f"unknown or inactive table: {table_name!r}")
    head = header.rows[0]

    columns = await client.execute(
        "SELECT column_name, data_type, is_nullable, description, display_name "
        "FROM _metadata.catalog_columns "
        "WHERE table_name = ? "
        "ORDER BY column_name",
        (table_name,),
    )

    # Both directions so the agent can plan joins regardless of which
    # side of the relationship it is starting from.
    joins = await client.execute(
        "SELECT source_table, target_table, join_column, join_type "
        "FROM _metadata.catalog_joins "
        "WHERE source_table = ? OR target_table = ? "
        "ORDER BY source_table, target_table, join_column",
        (table_name, table_name),
    )

    return {
        "table_name": head["table_name"],
        "schema_name": head["schema_name"],
        "description": head["description"],
        "keywords": head["keywords"],
        "columns": columns.rows,
        "joins": joins.rows,
    }


@schema_server.tool
async def get_distinct_values(
    table_name: str,
    column_name: str,
    top_n: int = 50,
) -> dict[str, Any]:
    """Return up to ``top_n`` distinct non-null values of ``column_name``.

    Used by the LLM to discover the small set of categorical values a
    column accepts (e.g. ``status`` ∈ ``{Approved, Pending, …}``)
    before writing the final ``query.execute`` SQL.

    Allowlist gate
    --------------
    The ``(table_name, column_name)`` pair is validated against
    ``_metadata.catalog_columns`` joined to ``catalog_tables`` (with
    ``is_active = 1``) before any read against the data table. The
    catalog row is the **only** source from which we read the
    identifiers later interpolated into the SQL string — the input
    arguments are never spliced as identifiers.

    Parameters
    ----------
    table_name:
        Qualified catalog name (e.g. ``analytics.vw_PersonDetail``).
    column_name:
        Column on that table.
    top_n:
        Max rows to return. Must be in ``[1, 1000]`` — out-of-range
        values raise ``ValueError`` rather than being silently capped.

    Returns
    -------
    dict
        ``{table_name, column_name, values, count, truncated}``.
        ``values`` is a flat list of raw scalars in ascending order;
        ``truncated`` is ``True`` when the underlying column has more
        distinct values than ``top_n``.

    Raises
    ------
    ValueError
        If either name is empty, ``top_n`` is out of range, or the
        ``(table_name, column_name)`` pair is not in the catalog.
    """
    table_name = _require_nonempty_string("table_name", table_name)
    column_name = _require_nonempty_string("column_name", column_name)
    top_n = _require_positive_int("top_n", top_n, _MAX_TOP_N)
    client = get_sql_client()

    catalog = await client.execute(
        "SELECT t.schema_name, c.column_name "
        "FROM _metadata.catalog_columns AS c "
        "INNER JOIN _metadata.catalog_tables AS t "
        "    ON t.table_name = c.table_name "
        "WHERE c.table_name = ? "
        "  AND c.column_name = ? "
        "  AND t.is_active = 1",
        (table_name, column_name),
    )
    if not catalog.rows:
        raise ValueError(f"unknown or inactive catalog entry for {table_name!r}.{column_name!r}")
    catalog_row = catalog.rows[0]
    schema_part = catalog_row["schema_name"]
    canonical_column = catalog_row["column_name"]

    # ``table_name`` in the catalog is stored as ``"schema.bare"`` —
    # strip the schema prefix so we can re-quote each part with
    # ``[ ]`` brackets independently. If a future migration stores the
    # bare name in catalog, this still works (no prefix to strip).
    bare_table = table_name.split(".", 1)[1] if "." in table_name else table_name

    # All three identifiers came from rows we just SELECTed from the
    # catalog (or from the input split exactly the way the catalog
    # would have produced it). They are still re-validated here so
    # that a future tampering of catalog rows cannot smuggle an
    # injection vector into the rendered SQL.
    _require_valid_identifier("schema_name", schema_part)
    _require_valid_identifier("table_name", bare_table)
    _require_valid_identifier("column_name", canonical_column)

    sql = (
        f"SELECT DISTINCT TOP (?) [{canonical_column}] AS value "
        f"FROM [{schema_part}].[{bare_table}] "
        f"WHERE [{canonical_column}] IS NOT NULL "
        f"ORDER BY [{canonical_column}]"
    )
    values = await client.execute(sql, (top_n,))
    return {
        "table_name": table_name,
        "column_name": canonical_column,
        "values": [row["value"] for row in values.rows],
        "count": len(values.rows),
        "truncated": values.truncated,
    }


__all__ = [
    "describe_table",
    "get_distinct_values",
    "get_sql_client",
    "list_tables",
    "ping",
    "schema_server",
    "search_tables",
    "set_sql_client",
]
