"""Drift check between ``_metadata.catalog_*`` and ``INFORMATION_SCHEMA``.

Rationale (PLAN.md §9 / Decision D9)
------------------------------------
The original draft of issue #21 also asked to (a) create
``_metadata.agent_allowlist`` and (b) apply ``MS_Description`` extended
properties via ``sp_addextendedproperty``. Decision D9 in PLAN.md §9
pivoted the architecture from extended properties + a separate
allowlist to plain catalog tables (``_metadata.catalog_tables``,
``_metadata.catalog_columns``, ``_metadata.catalog_joins``), with
``catalog_tables.is_active = 1`` acting as the visibility / allowlist
gate. (a) and (b) therefore became dead code and are intentionally
**not** implemented here. Only the drift-detection part of the
original acceptance criteria survives, and that is what this script
covers.

What this script does
---------------------
Connects to Azure SQL via Entra (``DefaultAzureCredential``), reads
``INFORMATION_SCHEMA.TABLES`` + ``INFORMATION_SCHEMA.COLUMNS`` and the
catalog tables, and emits a JSON drift report with three buckets:

* ``missing_from_catalog`` — exists in the live DB but no catalog row.
* ``missing_from_database`` — catalog row references something that is
  not in the live DB any more (dropped table / renamed column).
* ``type_mismatches`` — both sides know the column but the data types
  disagree.

The script is **read-only**: every statement is a ``SELECT``. No
``INSERT`` / ``UPDATE`` / DDL / ``sp_addextendedproperty`` runs.
Re-running the script is therefore trivially idempotent — which
satisfies issue #21 acceptance criterion 1 by construction.

CI consumption
--------------
The script exits ``0`` when the report is clean (``ok: true``) and
``1`` otherwise. ``--allow-drift`` forces exit ``0`` and is used by
the CI workflow (#22) when a schema migration is in flight and the
catalog has not yet been bumped.

Auth (mirrors ``apps/mcp/app/clients/sql.py``, inlined on purpose)
-------------------------------------------------------------------
``DefaultAzureCredential`` → access token for
``https://database.windows.net/.default`` → packed as UTF-16-LE bytes
with a 4-byte length prefix → handed to ``pyodbc.connect`` via the
``SQL_COPT_SS_ACCESS_TOKEN`` (1256) pre-connect attribute. There is
no password / connection-string path. The MCP module is *not*
imported (cross-package coupling); this script is fully self-contained
in ``database/scripts/``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# ODBC / Entra constants (mirrors apps/mcp/app/clients/sql.py)
# ---------------------------------------------------------------------------
# Microsoft ODBC pre-connect attribute. Value is the raw access-token
# bytes; the driver reads it once during ``SQLDriverConnect`` and uses
# it instead of UID/PWD. Source: msodbcsql.h / Microsoft docs.
_SQL_COPT_SS_ACCESS_TOKEN = 1256

# OAuth2 scope for the Azure SQL resource. The trailing ``/.default``
# is the v2.0 scope marker.
_AZURE_SQL_SCOPE = "https://database.windows.net/.default"

# Default ODBC driver, matching the dev container + mcp-ci.yml install.
_DEFAULT_DRIVER = "ODBC Driver 18 for SQL Server"

# Characters that have no business in a SQL Server hostname, database
# name or ODBC driver name. Mirrors ``_UNSAFE_ODBC_CHARS`` in
# ``apps/mcp/app/clients/sql.py``. Any of them in an input would let a
# misconfiguration smuggle a second clause into the connection string
# (e.g. ``DB_SERVER="x.db;Authentication=ActiveDirectoryPassword;UID=evil;PWD=..."``)
# and defeat the Entra-only invariant the script otherwise enforces.
_UNSAFE_ODBC_CHARS = re.compile(r"[;={}\"'\x00\r\n\t]")


def _reject_unsafe_identifier(field_name: str, value: str) -> None:
    """Raise ``ValueError`` if ``value`` contains an ODBC delimiter.

    Applied to ``DB_SERVER`` / ``DB_DATABASE`` / driver before they
    get interpolated into the connection string. Catches
    ``DB_SERVER="x.db;Authentication=...;UID=evil"`` style attempts at
    the input boundary so the script keeps the same posture as
    :mod:`apps.mcp.app.clients.sql` (issue #17): no free-form
    connection string, no password code path.
    """
    match = _UNSAFE_ODBC_CHARS.search(value)
    if match is not None:
        raise ValueError(
            f"check_metadata_drift: {field_name!r} contains forbidden character "
            f"{match.group()!r}. Hostnames, database names and ODBC driver "
            "names must not contain any of: ; = { } \" ' or control chars."
        )

# Schemas excluded from the "missing from catalog" calculation. ``sys``
# and ``INFORMATION_SCHEMA`` are SQL Server system schemas; ``_metadata``
# is our own scaffolding (catalog + audit log) and intentionally not
# self-catalogued. ``dbo`` is intentionally NOT in this list — if
# something ever lands in ``dbo`` we want to know about it so it can be
# explicitly opted in or moved.
_SYSTEM_SCHEMAS = frozenset({"sys", "INFORMATION_SCHEMA", "_metadata"})

# ---------------------------------------------------------------------------
# Read-only SQL the script runs. All target tables / views are in
# system schemas or ``_metadata``; the ``uai_readonly`` role used in
# CI already has ``SELECT`` on ``[_metadata]``.
# ---------------------------------------------------------------------------
_DB_TABLES_QUERY = (
    "SELECT TABLE_SCHEMA, TABLE_NAME "
    "FROM INFORMATION_SCHEMA.TABLES "
    "WHERE TABLE_TYPE IN (N'BASE TABLE', N'VIEW')"
)
_DB_COLUMNS_QUERY = (
    "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE "
    "FROM INFORMATION_SCHEMA.COLUMNS"
)
_CATALOG_TABLES_QUERY = (
    "SELECT table_name, schema_name, is_active FROM _metadata.catalog_tables"
)
_CATALOG_COLUMNS_QUERY = (
    "SELECT table_name, column_name, data_type FROM _metadata.catalog_columns"
)


# ---------------------------------------------------------------------------
# Protocols / typed records — keeps the pure logic decoupled from
# pyodbc so tests can substitute a fake cursor without touching a real
# database.
# ---------------------------------------------------------------------------
class _Cursor(Protocol):
    """Minimal subset of ``pyodbc.Cursor`` we depend on."""

    def execute(self, sql: str, /) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    def close(self) -> None: ...


class _Connection(Protocol):
    """Minimal subset of ``pyodbc.Connection`` we depend on."""

    def cursor(self) -> _Cursor: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _DbTable:
    """One row from ``INFORMATION_SCHEMA.TABLES`` (filtered)."""

    schema_name: str
    table_name: str  # fully qualified: ``"schema.table"``


@dataclass(frozen=True, slots=True)
class _DbColumn:
    """One row from ``INFORMATION_SCHEMA.COLUMNS`` (filtered)."""

    table_name: str  # fully qualified
    column_name: str
    data_type: str
    is_nullable: bool


@dataclass(frozen=True, slots=True)
class _CatalogTable:
    """One row from ``_metadata.catalog_tables``."""

    schema_name: str
    table_name: str  # fully qualified per the seed (``analytics.vw_X``)
    is_active: bool


@dataclass(frozen=True, slots=True)
class _CatalogColumn:
    """One row from ``_metadata.catalog_columns``."""

    table_name: str  # fully qualified
    column_name: str
    data_type: str


# ---------------------------------------------------------------------------
# Connection helpers (Entra-only, mirrors apps/mcp/app/clients/sql.py)
# ---------------------------------------------------------------------------
def _build_token_struct(token: str) -> bytes:
    """Pack an OAuth access token into the layout expected by
    ``SQL_COPT_SS_ACCESS_TOKEN``.

    Microsoft ODBC Driver 18 wants the token as UTF-16-LE bytes
    prefixed with a 4-byte little-endian length. ``struct.pack("=i{n}s",
    n, blob)`` builds exactly that layout (``=`` = native byte order
    without alignment padding; equals little-endian for ``i`` on every
    platform pyodbc supports).
    """
    blob = token.encode("utf-16-le")
    return struct.pack(f"=i{len(blob)}s", len(blob), blob)


def _connect(
    server: str,
    database: str,
    *,
    managed_identity_client_id: str = "",
    driver: str = _DEFAULT_DRIVER,
) -> _Connection:  # pragma: no cover - exercised only against a real Azure SQL
    """Open an Entra-authenticated ``pyodbc`` connection.

    Imports of ``pyodbc`` and ``azure.identity`` are deferred so unit
    tests can monkeypatch ``_connect`` without paying the import cost
    of the Azure SDK / the ODBC driver shim.
    """
    import pyodbc
    from azure.identity import DefaultAzureCredential

    if not server or not server.strip():
        raise ValueError("DB_SERVER is required (e.g. 'myserver.database.windows.net')")
    if not database or not database.strip():
        raise ValueError("DB_DATABASE is required (e.g. 'calabriowfm')")
    # Belt-and-braces against connection-string injection via env vars.
    # Tested in :mod:`tests.test_check_metadata_drift` via the public
    # helper :func:`_reject_unsafe_identifier`; the call here is the
    # last line of defence before ``conn_str`` interpolation.
    _reject_unsafe_identifier("DB_SERVER", server)
    _reject_unsafe_identifier("DB_DATABASE", database)
    _reject_unsafe_identifier("driver", driver)

    credential = DefaultAzureCredential(
        managed_identity_client_id=managed_identity_client_id or None,
        # Same lockdown rationale as apps/mcp/app/clients/sql.py: no
        # password code path is allowed (issue #17). The script runs
        # either in CI under a Managed Identity attached to the
        # runner / job (System-Assigned MI or a UAMI pinned via
        # ``DB_MANAGED_IDENTITY_CLIENT_ID``) or locally under ``az
        # login``. Workload-Identity / OIDC federation is *not*
        # currently in scope for this script — if CI later switches
        # to it, flip ``exclude_workload_identity_credential`` to
        # ``False`` and update this comment in the same change.
        exclude_environment_credential=True,
        exclude_interactive_browser_credential=True,
        exclude_visual_studio_code_credential=True,
        exclude_shared_token_cache_credential=True,
        exclude_powershell_credential=True,
        exclude_developer_cli_credential=True,
        exclude_broker_credential=True,
        exclude_workload_identity_credential=True,
    )
    token = credential.get_token(_AZURE_SQL_SCOPE)
    token_struct = _build_token_struct(token.token)

    conn_str = (
        f"Driver={{{driver}}};"
        f"Server=tcp:{server},1433;"
        f"Database={database};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct})


# ---------------------------------------------------------------------------
# Fetchers — thin layer over the cursor protocol, normalising into the
# typed records used by ``compute_drift``.
# ---------------------------------------------------------------------------
def _fetch_db_tables(cursor: _Cursor) -> list[_DbTable]:
    """Return all base tables + views, excluding system schemas."""
    cursor.execute(_DB_TABLES_QUERY)
    return [
        _DbTable(schema_name=row[0], table_name=f"{row[0]}.{row[1]}")
        for row in cursor.fetchall()
        if row[0] not in _SYSTEM_SCHEMAS
    ]


def _fetch_db_columns(cursor: _Cursor) -> list[_DbColumn]:
    """Return all columns, excluding system schemas."""
    cursor.execute(_DB_COLUMNS_QUERY)
    return [
        _DbColumn(
            table_name=f"{row[0]}.{row[1]}",
            column_name=row[2],
            data_type=row[3],
            is_nullable=str(row[4]).strip().upper() == "YES",
        )
        for row in cursor.fetchall()
        if row[0] not in _SYSTEM_SCHEMAS
    ]


def _fetch_catalog_tables(cursor: _Cursor) -> list[_CatalogTable]:
    """Return all catalog table rows (active and inactive)."""
    cursor.execute(_CATALOG_TABLES_QUERY)
    return [
        _CatalogTable(
            table_name=row[0],
            schema_name=row[1],
            is_active=bool(row[2]),
        )
        for row in cursor.fetchall()
    ]


def _fetch_catalog_columns(cursor: _Cursor) -> list[_CatalogColumn]:
    """Return all catalog column rows."""
    cursor.execute(_CATALOG_COLUMNS_QUERY)
    return [
        _CatalogColumn(
            table_name=row[0],
            column_name=row[1],
            data_type=row[2],
        )
        for row in cursor.fetchall()
    ]


# ---------------------------------------------------------------------------
# Drift computation — pure function, the heart of the script.
# ---------------------------------------------------------------------------
def _normalize_type(t: str) -> str:
    """Return a comparable form of a SQL type string.

    The catalog stores rich types like ``NVARCHAR(150)`` /
    ``DECIMAL(4,2)``, while ``INFORMATION_SCHEMA.COLUMNS.DATA_TYPE``
    returns only the base type (``nvarchar``, ``decimal``). A literal
    case-insensitive compare would therefore flag almost every column
    as drifted, making the script useless. We strip the size /
    precision parens and lowercase, so ``NVARCHAR(150)`` and
    ``nvarchar`` both normalise to ``"nvarchar"``. Length / precision
    drift inside the parens is intentionally NOT flagged here — that
    is a known limitation and would require parsing
    ``CHARACTER_MAXIMUM_LENGTH`` / ``NUMERIC_PRECISION`` separately.
    """
    return t.split("(", 1)[0].strip().lower()


def _schema_of(table_name: str) -> str:
    """Return the schema prefix of a fully-qualified ``schema.table``.

    Used by the ``include_schemas`` filter so column rows (which only
    carry ``schema.table``) can be matched against the schema scope
    without re-fetching ``INFORMATION_SCHEMA``. Returns the empty
    string when the input has no dot — such rows are never matched
    by an include-list filter, which is the safe default.
    """
    head, sep, _ = table_name.partition(".")
    return head if sep else ""


def compute_drift(
    db_tables: Sequence[_DbTable],
    db_columns: Sequence[_DbColumn],
    catalog_tables: Sequence[_CatalogTable],
    catalog_columns: Sequence[_CatalogColumn],
    *,
    now: datetime | None = None,
    include_schemas: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Compare the live DB state to the catalog and return a drift report.

    Pure function over the four input row lists — every output list is
    deterministically sorted so the JSON payload is stable across runs
    (acceptance criterion: ``Output is machine-readable JSON for CI
    integration``).

    Filtering rules:

    * "Missing from catalog" considers a DB object missing only when
      it has no catalog row at all (active or inactive). An inactive
      catalog entry is an explicit "we know about this table and chose
      to hide it from the agent" — it must not surface as drift.
    * Columns of inactive catalog tables are skipped on both sides.
    * "Missing from database" / "type mismatches" only consider catalog
      rows where ``is_active = 1``. An inactive catalog table whose
      underlying view has been dropped is not drift, it is intentional.
    * When ``include_schemas`` is given, only rows whose schema is in
      the set are considered — on **both** sides. This is the seam
      that lets a catalog cover only a curated subset of the DB
      (e.g. ``analytics.vw_*`` views) without every system table
      surfacing as drift. ``None`` (the default) preserves the
      pre-filter behaviour for callers that already constrain the
      input lists themselves.
    """
    if include_schemas is not None:
        db_tables = [t for t in db_tables if t.schema_name in include_schemas]
        db_columns = [c for c in db_columns if _schema_of(c.table_name) in include_schemas]
        catalog_tables = [
            t for t in catalog_tables if t.schema_name in include_schemas
        ]
        catalog_columns = [
            c for c in catalog_columns if _schema_of(c.table_name) in include_schemas
        ]

    active_table_names = {t.table_name for t in catalog_tables if t.is_active}
    known_table_names = {t.table_name for t in catalog_tables}
    db_table_names = {t.table_name for t in db_tables}

    missing_from_catalog_tables = sorted(
        (
            {"schema_name": t.schema_name, "table_name": t.table_name}
            for t in db_tables
            if t.table_name not in known_table_names
        ),
        key=lambda r: (r["schema_name"], r["table_name"]),
    )

    missing_from_database_tables = sorted(active_table_names - db_table_names)

    # Columns are only checked on active catalog tables. Inactive
    # tables are out of scope for the agent and therefore out of scope
    # for drift.
    db_col_index: dict[tuple[str, str], _DbColumn] = {
        (c.table_name, c.column_name): c
        for c in db_columns
        if c.table_name in active_table_names
    }
    cat_col_index: dict[tuple[str, str], _CatalogColumn] = {
        (c.table_name, c.column_name): c
        for c in catalog_columns
        if c.table_name in active_table_names
    }

    missing_from_catalog_columns = sorted(
        (
            {
                "table_name": c.table_name,
                "column_name": c.column_name,
                "data_type": c.data_type,
                "is_nullable": c.is_nullable,
            }
            for key, c in db_col_index.items()
            if key not in cat_col_index
        ),
        key=lambda r: (r["table_name"], r["column_name"]),
    )

    missing_from_database_columns = sorted(
        (
            {"table_name": c.table_name, "column_name": c.column_name}
            for key, c in cat_col_index.items()
            if key not in db_col_index
        ),
        key=lambda r: (r["table_name"], r["column_name"]),
    )

    type_mismatches = sorted(
        (
            {
                "table_name": c.table_name,
                "column_name": c.column_name,
                "catalog_type": c.data_type,
                "actual_type": db_col_index[key].data_type,
            }
            for key, c in cat_col_index.items()
            if key in db_col_index
            and _normalize_type(c.data_type) != _normalize_type(db_col_index[key].data_type)
        ),
        key=lambda r: (r["table_name"], r["column_name"]),
    )

    ok = not (
        missing_from_catalog_tables
        or missing_from_catalog_columns
        or missing_from_database_tables
        or missing_from_database_columns
        or type_mismatches
    )

    return {
        "missing_from_catalog": {
            "tables": missing_from_catalog_tables,
            "columns": missing_from_catalog_columns,
        },
        "missing_from_database": {
            "tables": missing_from_database_tables,
            "columns": missing_from_database_columns,
        },
        "type_mismatches": type_mismatches,
        "ok": ok,
        "checked_at_utc": (now or datetime.now(UTC)).isoformat(),
    }


def collect_drift_from_connection(
    connection: _Connection,
    *,
    now: datetime | None = None,
    include_schemas: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Open a cursor on ``connection``, fetch both sides, return the report.

    Convenience seam between ``_connect`` and ``compute_drift``; lets
    the tests exercise the full fetch path with a fake connection
    while keeping ``main`` small. ``include_schemas`` is forwarded
    verbatim to :func:`compute_drift` (see the filter rules there).
    """
    cursor = connection.cursor()
    try:
        db_tables = _fetch_db_tables(cursor)
        db_columns = _fetch_db_columns(cursor)
        catalog_tables = _fetch_catalog_tables(cursor)
        catalog_columns = _fetch_catalog_columns(cursor)
    finally:
        cursor.close()
    return compute_drift(
        db_tables,
        db_columns,
        catalog_tables,
        catalog_columns,
        now=now,
        include_schemas=include_schemas,
    )


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------
def _format_summary(report: dict[str, Any]) -> str:
    """Build a one-screen human summary of a drift report.

    Used by ``--summary``; written to **stderr** so the JSON on stdout
    stays machine-parseable.
    """
    sections = [
        ("missing_from_catalog.tables", report["missing_from_catalog"]["tables"]),
        ("missing_from_catalog.columns", report["missing_from_catalog"]["columns"]),
        ("missing_from_database.tables", report["missing_from_database"]["tables"]),
        ("missing_from_database.columns", report["missing_from_database"]["columns"]),
        ("type_mismatches", report["type_mismatches"]),
    ]
    lines = [
        f"Metadata drift check — ok={report['ok']} at {report['checked_at_utc']}",
    ]
    for label, items in sections:
        lines.append(f"  {label}: {len(items)}")
        for entry in items[:5]:
            lines.append(f"    - {entry}")
        if len(items) > 5:
            lines.append(f"    … and {len(items) - 5} more")
    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser — split out for testability.

    The script *always* writes the JSON report to stdout — there is
    no toggle for that. A previous draft exposed a ``--json`` flag
    that defaulted to ``True`` and could not be disabled; the flag
    was dropped because it was a no-op that just inflated the CLI
    surface. JSON-on-stdout is the contract.
    """
    parser = argparse.ArgumentParser(
        prog="check_metadata_drift",
        description=(
            "Compare _metadata.catalog_* against INFORMATION_SCHEMA and "
            "emit a JSON drift report on stdout. Read-only; safe to re-run."
        ),
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        default=False,
        help="Also write a human one-screen summary to stderr.",
    )
    parser.add_argument(
        "--allow-drift",
        action="store_true",
        default=False,
        help="Exit 0 even when drift is detected (used by CI during migrations).",
    )
    parser.add_argument(
        "--include-schema",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "Restrict the comparison to the given schema. Repeatable. "
            "Defaults to the value of ``DRIFT_CHECK_INCLUDE_SCHEMAS`` "
            "(comma-separated) when set, otherwise the full DB is "
            "considered. Use this to scope drift to the curated\n"
            "agent-facing surface (e.g. ``--include-schema analytics``)."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point.

    Connects, computes the drift report, writes JSON to stdout (and an
    optional human summary to stderr), and returns ``0`` if the report
    is clean or ``--allow-drift`` is set, ``1`` otherwise.
    """
    args = _build_arg_parser().parse_args(argv)

    server = os.environ.get("DB_SERVER", "").strip()
    database = os.environ.get("DB_DATABASE", "").strip()
    mi_client_id = os.environ.get("DB_MANAGED_IDENTITY_CLIENT_ID", "").strip()

    # Schema scope: CLI flag wins; otherwise fall back to the env
    # var so CI can wire it without modifying the call site. ``None``
    # (no flag, no env var) preserves the original "compare
    # everything" behaviour.
    include_schemas: frozenset[str] | None = None
    if args.include_schema:
        include_schemas = frozenset(s.strip() for s in args.include_schema if s.strip())
    else:
        env_val = os.environ.get("DRIFT_CHECK_INCLUDE_SCHEMAS", "").strip()
        if env_val:
            include_schemas = frozenset(
                s.strip() for s in env_val.split(",") if s.strip()
            )

    if not server or not database:
        print(
            "ERROR: DB_SERVER and DB_DATABASE must both be set in the environment.",
            file=sys.stderr,
        )
        return 2

    connection = _connect(
        server=server,
        database=database,
        managed_identity_client_id=mi_client_id,
    )
    try:
        report = collect_drift_from_connection(
            connection, include_schemas=include_schemas
        )
    finally:
        connection.close()

    # JSON to stdout is the script's only data contract; see the
    # rationale in :func:`_build_arg_parser`.
    sys.stdout.write(json.dumps(report, sort_keys=True, indent=2))
    sys.stdout.write("\n")

    if args.summary:
        sys.stderr.write(_format_summary(report))
        sys.stderr.write("\n")

    if report["ok"] or args.allow_drift:
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
