"""``query`` namespace sub-server.

Hosts the query-execution tool listed in PLAN.md §6.3 Day-1:

* ``execute`` — issue #20. Pipes agent-submitted T-SQL through the
  :mod:`app.security` validator (issue #19, PR #69), executes the
  normalised statement via :class:`~app.clients.sql.SqlDatabaseClient`
  (issue #17), and returns ``{rows, row_count, truncated}`` with a
  structured telemetry log per call.

The placeholder ``ping`` tool from #16 is kept as a cheap liveness
probe so MCP discovery returns the namespace even before the database
is reachable — see :mod:`app.servers.schema` for the rationale.

SQL-client wiring mirrors :mod:`app.servers.schema`: a process-wide
singleton built lazily on first use, with an ``set_sql_client`` seam
for test injection. The allowlist consumed by the validator is also
cached at the module level — populated on first ``execute`` call from
``_metadata.catalog_tables WHERE is_active = 1`` (PLAN.md §9 Decision
D9). A future issue can add a TTL refresh; for now the cache is
process-lifetime and ``set_allowlist(None)`` forces a re-fetch.

``bu_id`` enforcement (PLAN.md §8) is intentionally **not** wired in
this issue. The validator already blocks all non-SELECT statements
and pins references to the catalog allowlist; auto-injecting
``WHERE bu_id = @bu_id`` requires AST rewriting and a request-context
tenant id, both of which are tracked separately. The current grant
on ``uai_readonly`` (``database/04-grant-readonly.sql``) is the
fallback line of defence until that lands.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from fastmcp import FastMCP

from ..clients.sql import QueryResult, SqlDatabaseClient
from ..security import validate
from ..settings import get_settings

logger = logging.getLogger(__name__)

query_server: FastMCP = FastMCP(name="calabrio-mcp-query")


# ---------------------------------------------------------------------------
# SQL-client + allowlist wiring
# ---------------------------------------------------------------------------

# Process-wide singletons. Each is lazily populated on first use so
# ``import app.main`` keeps working in environments without SQL
# configured (e.g. the discovery tests in ``tests/test_discovery.py``).
_sql_client: SqlDatabaseClient | None = None
_allowlist_cache: frozenset[str] | None = None

# Hard ceiling on the allowlist fetch. ``SqlDatabaseClient.execute``
# defaults to ``max_rows=1000``, and silently truncating the catalog
# read would yield a partial allowlist that rejects otherwise-valid
# tables — a very confusing failure mode. We ask for a deliberately
# high cap (100k rows is well above any plausible WFM catalog size)
# and raise loudly if that ceiling is ever hit, forcing an explicit
# decision (raise the cap, split the catalog, or fix the seed).
_ALLOWLIST_FETCH_MAX_ROWS = 100_000


def _build_sql_client() -> SqlDatabaseClient:
    """Construct a :class:`SqlDatabaseClient` from current settings.

    Raises
    ------
    RuntimeError
        If ``MCP_AZURE_SQL_SERVER`` / ``MCP_AZURE_SQL_DATABASE`` are
        unset. The execute tool needs both; failing here turns a
        vague ``ValueError`` from the client into a tool-level error
        message the operator can act on.
    """
    settings = get_settings()
    if not settings.azure_sql_server or not settings.azure_sql_database:
        raise RuntimeError(
            "query.execute requires MCP_AZURE_SQL_SERVER and MCP_AZURE_SQL_DATABASE "
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

    Tests inject mocks through this seam; passing ``None`` forces the
    next :func:`get_sql_client` call to rebuild from settings.
    """
    global _sql_client
    _sql_client = client


async def _fetch_allowlist() -> frozenset[str]:
    """Read the active table allowlist from ``_metadata.catalog_tables``.

    Returns the set of qualified ``schema.table`` strings the
    validator checks every ``query.execute`` SQL against. Only rows
    with ``is_active = 1`` are returned — that is the visibility
    gate per PLAN.md §9 Decision D9.

    The catalog already stores qualified names in ``table_name``
    (e.g. ``'analytics.vw_PersonDetail'``), so we read the column
    verbatim rather than concatenating ``schema_name + '.' + table_name``.
    Seed data is in ``database/03-seed-data.sql``.

    Raises
    ------
    RuntimeError
        If the catalog fetch is truncated by the row cap. A partial
        allowlist would silently reject valid tables — fail loudly
        instead so the operator either raises
        ``_ALLOWLIST_FETCH_MAX_ROWS`` or splits the catalog.
    """
    client = get_sql_client()
    # Pass ``max_rows`` explicitly so we do not inherit the client's
    # 1000-row default. See ``_ALLOWLIST_FETCH_MAX_ROWS`` for why
    # 100k is the ceiling.
    result = await client.execute(
        "SELECT table_name FROM _metadata.catalog_tables WHERE is_active = 1",
        max_rows=_ALLOWLIST_FETCH_MAX_ROWS,
    )
    if result.truncated:
        raise RuntimeError(
            f"_metadata.catalog_tables returned more than "
            f"{_ALLOWLIST_FETCH_MAX_ROWS} active rows; the allowlist would be "
            "partial and silently reject valid tables. Raise "
            "_ALLOWLIST_FETCH_MAX_ROWS in app/servers/query.py or split the catalog."
        )
    return frozenset(row["table_name"] for row in result.rows)


async def get_allowlist() -> frozenset[str]:
    """Return the cached allowlist, fetching on first use."""
    global _allowlist_cache
    if _allowlist_cache is None:
        _allowlist_cache = await _fetch_allowlist()
    return _allowlist_cache


def set_allowlist(allowlist: frozenset[str] | None) -> None:
    """Override (or clear) the cached allowlist.

    Tests inject a known set; an ops-time refresh path can pass
    ``None`` to force a re-fetch on the next ``query.execute``.
    """
    global _allowlist_cache
    _allowlist_cache = allowlist


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_max_rows(value: int, cap: int) -> int:
    """Return ``value`` after asserting it lies in ``[1, cap]``.

    Reject rather than clamp so an agent that asked for 10_000 rows
    sees the failure and adjusts, instead of getting ``cap`` rows
    back and assuming the result was complete. Mirrors the
    ``_require_positive_int`` helper in :mod:`app.servers.schema`.
    """
    # ``bool`` is a subclass of ``int``; reject it explicitly so
    # ``True`` / ``False`` cannot stand in for a real row count.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"'max_rows' must be an int, got {type(value).__name__}")
    if value < 1 or value > cap:
        raise ValueError(f"'max_rows' must be between 1 and {cap}, got {value}")
    return value


def _sql_hash(sql: str) -> str:
    """Return a short hex digest of ``sql`` for telemetry correlation.

    Truncating to 16 hex chars (64 bits) keeps log lines readable
    while leaving collisions astronomically unlikely for the volume
    of queries a single agent session produces. The full digest is
    deterministic across processes so audit pipelines can join on it.
    """
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@query_server.tool
def ping() -> dict[str, str]:
    """Return a static liveness payload.

    Placeholder probe kept from #16 — useful for ACA health checks
    and curl smoke tests. The real query-execution tool is below.
    """
    return {"namespace": "query", "status": "ok"}


@query_server.tool
async def execute(sql: str, max_rows: int | None = None) -> dict[str, Any]:
    """Validate ``sql`` and execute it read-only against Azure SQL.

    The SQL is first put through the :func:`app.security.validate`
    AST gate (issue #19, PR #69) which:

    * rejects anything that is not a single ``SELECT`` / CTE / set-op,
    * blocks every DML / DDL / EXEC / multi-statement / control-flow
      construct,
    * strips comments from the normalised SQL,
    * checks every referenced table against the agent allowlist
      sourced from ``_metadata.catalog_tables`` (rows with
      ``is_active = 1`` per PLAN.md §9 Decision D9).

    The *normalised* SQL (not the original string) is then executed
    via :class:`~app.clients.sql.SqlDatabaseClient`, which fetches
    ``max_rows + 1`` rows so truncation can be flagged without a
    second round-trip.

    Every call emits a structured ``logger.info`` line tagged
    ``telemetry=query_execute`` carrying the SHA-256 hash of the
    SQL, the row count, the truncation flag and the wall-clock
    duration. The acceptance criterion on issue #20 ("emit custom
    event with sql hash + rowcount + duration") is satisfied by
    that log line; an OpenTelemetry exporter wired in a later phase
    (PLAN.md §10/§13) can lift the same ``extra`` payload into an
    App Insights custom event without any changes here.

    Parameters
    ----------
    sql:
        Raw T-SQL. Must be a single read-only statement.
    max_rows:
        Hard cap on returned rows. Defaults to
        ``Settings.query_max_rows_default`` (200); must be between 1
        and ``Settings.query_max_rows_cap`` (1000) inclusive.

    Returns
    -------
    dict
        ``{"rows": [...], "row_count": int, "truncated": bool}``.

    Raises
    ------
    ValueError
        Validator rejected the query (reason in the message), or
        ``max_rows`` is not an int in ``[1, cap]``.
    pyodbc.Error
        DB-level failure (connection refused, permission denied,
        transient network blip). Propagated to the MCP layer as a
        tool error so the agent sees the underlying cause rather
        than a generic 500.
    """
    settings = get_settings()
    if max_rows is None:
        max_rows = settings.query_max_rows_default
    max_rows = _require_max_rows(max_rows, settings.query_max_rows_cap)

    # Wall-clock start: covers allowlist fetch (cache hit/miss),
    # validator, and — on the happy path — the DB round-trip. Both
    # the success *and* rejection telemetry log lines carry the
    # resulting ``duration_ms`` so downstream pipelines see a uniform
    # shape regardless of outcome.
    started_at = time.perf_counter()

    allowlist = await get_allowlist()
    result = validate(sql, allowlist=allowlist)
    if not result.ok:
        duration_ms = round((time.perf_counter() - started_at) * 1000)
        logger.info(
            "query.execute rejected by validator",
            extra={
                "telemetry": "query_execute",
                "outcome": "rejected",
                "reason": result.reason,
                "sql_hash": _sql_hash(sql),
                # Zero-valued mirrors of the success-path fields so
                # ingestion pipelines can flatten both outcomes onto
                # the same schema. ``result.tables`` is ``()`` when
                # ``ok`` is False (per ``ValidationResult`` docs).
                "row_count": 0,
                "truncated": False,
                "duration_ms": duration_ms,
                "max_rows": max_rows,
                "tables": sorted(result.tables),
            },
        )
        raise ValueError(f"query rejected: {result.reason}")

    # The validator contract guarantees ``normalized_sql`` is set
    # when ``ok=True``. The assertion documents that contract for
    # mypy without paying for a runtime check on the hot path.
    assert result.normalized_sql is not None
    client = get_sql_client()

    query_result: QueryResult = await client.execute(
        result.normalized_sql,
        max_rows=max_rows,
    )
    duration_ms = round((time.perf_counter() - started_at) * 1000)
    row_count = len(query_result.rows)

    logger.info(
        "query.execute completed",
        extra={
            "telemetry": "query_execute",
            "outcome": "ok",
            "sql_hash": _sql_hash(result.normalized_sql),
            "row_count": row_count,
            "truncated": query_result.truncated,
            "duration_ms": duration_ms,
            "max_rows": max_rows,
            "tables": sorted(result.tables),
        },
    )
    return {
        "rows": query_result.rows,
        "row_count": row_count,
        "truncated": query_result.truncated,
    }


__all__ = [
    "execute",
    "get_allowlist",
    "get_sql_client",
    "ping",
    "query_server",
    "set_allowlist",
    "set_sql_client",
]
