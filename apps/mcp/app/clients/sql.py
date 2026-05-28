"""Azure SQL access — Entra-only via ``DefaultAzureCredential``.

This module is the **single** code path for talking to the WFM database
from the MCP server. SQL passwords are forbidden by policy (see
issue #17): there is no username / password / Key Vault-secret
fallback, and adding one would be a security regression.

Defence-in-depth posture (ported from ``CalabrioMAFVersion/src/mcp_wfm``)
-----------------------------------------------------------------------
1. We never accept a free-form ODBC connection string. We build it
   from ``server`` + ``database`` ourselves so no ``UID=``/``PWD=``/
   ``Authentication=`` clause can slip in via env.
2. :meth:`_strip_password_clauses` runs anyway on the built string as
   a belt-and-braces guard against a future refactor that adds an
   ``extra_options`` knob.
3. The default credential chain is **locked down** via
   :meth:`_build_default_credential`:

   * In Azure (``environment != "local"``) only Managed Identity is
     allowed.
   * Locally only the developer's ``az login`` credential is allowed.
   * ``EnvironmentCredential`` is *always* disabled — it would honour
     ``AZURE_CLIENT_SECRET``, which is a password by another name.
   * Interactive browser / VS Code / shared-token-cache flows are
     disabled for predictability and to keep prod headless.
4. The Managed Identity client id can be pinned via
   ``MCP_AZURE_SQL_MANAGED_IDENTITY_CLIENT_ID`` so MSAL does not pick
   the wrong UAMI when several are attached to the host.

Authentication recipe
---------------------
1. Acquire an OAuth2 access token for ``https://database.windows.net/.default``
   via the locked-down :class:`DefaultAzureCredential`.
2. Pack the token as UTF-16 LE bytes wrapped in a 4-byte length prefix
   (the structure expected by the Microsoft ODBC Driver 18).
3. Pass the packed bytes via the ``SQL_COPT_SS_ACCESS_TOKEN`` (1256)
   ODBC pre-connect attribute to ``pyodbc.connect``.

Reference: https://learn.microsoft.com/sql/connect/odbc/using-azure-active-directory#aad-access-token

Async strategy
--------------
The :mod:`pyodbc` driver is synchronous. We push blocking calls onto
the default executor via :func:`asyncio.to_thread`, keeping the
public API ``async def`` so FastMCP tools can ``await`` directly. The
``aioodbc`` wrapper is intentionally avoided: it has had no upstream
release since 2022 and adds zero value over the stdlib pattern.

Connection lifecycle
--------------------
One connection per :meth:`SqlDatabaseClient.execute` call. The MCP
server runs ``stateless_http=True`` (PLAN.md §6.3) so there is no
session to reuse a pooled connection across. The ~50–100 ms TLS +
TDS handshake hit per request is acceptable for the POC; pooling can
land later behind the same public API without callers noticing.
"""

from __future__ import annotations

import asyncio
import logging
import re
import struct
import time
from dataclasses import dataclass
from typing import Any

import pyodbc
from azure.core.credentials import AccessToken, TokenCredential
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ODBC constants
# ---------------------------------------------------------------------------

# Microsoft ODBC pre-connect attribute. Value is the raw access-token
# bytes; the driver reads it once during ``SQLDriverConnect`` and uses
# it instead of UID/PWD. Source: msodbcsql.h / Microsoft docs.
_SQL_COPT_SS_ACCESS_TOKEN = 1256

# Scope for the Azure SQL resource. The trailing ``/.default`` is the
# OAuth2 scope marker; the bare resource URI alone would not be a
# valid v2.0 scope.
_AZURE_SQL_SCOPE = "https://database.windows.net/.default"

# Refresh the cached access token this many seconds before its declared
# expiry. 5 minutes is a comfortable buffer against clock skew + the
# small chance of a request straddling the boundary.
_TOKEN_REFRESH_BUFFER_SECONDS = 300

# Default query row cap. Callers may override per-call; this exists
# only to make accidentally unbounded ``SELECT *`` calls survivable.
DEFAULT_MAX_ROWS = 1_000

# Connection-string clauses that, if present, would let a non-Entra
# auth path through the door. The sanitizer drops these clauses
# unconditionally; the test suite asserts they never sneak back in.
_FORBIDDEN_CONN_STR_CLAUSES = re.compile(
    r"(?i)\b(authentication|uid|user\s*id|pwd|password|trusted_connection)\s*="
)

# Characters that have no business in a SQL Server hostname, database
# name or ODBC driver name. Any of them in an input would let a
# misconfiguration smuggle a second clause into the ODBC connection
# string (e.g. ``server="x.db;UID=evil;PWD=..."``). Validating at the
# boundary makes :meth:`SqlDatabaseClient._strip_password_clauses`
# pure defence in depth rather than the primary guard.
_UNSAFE_ODBC_CHARS = re.compile(r"[;={}\"'\x00\r\n\t]")


def _reject_unsafe_identifier(field_name: str, value: str) -> None:
    """Raise ``ValueError`` if ``value`` contains an ODBC delimiter.

    Applied to ``server`` / ``database`` / ``driver`` before they get
    interpolated into the connection string. Catches
    ``MCP_AZURE_SQL_SERVER="x.db;Authentication=...;UID=evil"`` style
    attacks at the *input* boundary, complementing the
    :meth:`SqlDatabaseClient._strip_password_clauses` sanitizer that
    runs on the *output*.
    """
    match = _UNSAFE_ODBC_CHARS.search(value)
    if match is not None:
        raise ValueError(
            f"SqlDatabaseClient: {field_name!r} contains forbidden character "
            f"{match.group()!r}. Hostnames, database names and ODBC driver "
            "names must not contain any of: ; = { } \" ' or control chars."
        )


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Outcome of a single :meth:`SqlDatabaseClient.execute` call.

    Attributes
    ----------
    rows:
        Each row is a ``{column_name: value}`` mapping. Order within
        the list reflects the order rows arrived from the driver
        (which mirrors the ``ORDER BY`` clause when present).
    truncated:
        ``True`` when the query produced **more** rows than
        ``max_rows`` and the result was cut off. Downstream tools
        (e.g. ``query.execute`` in #20) surface this to the agent so
        it can ask the user to refine the query.
    """

    rows: list[dict[str, Any]]
    truncated: bool


class SqlDatabaseClient:
    """Thin async wrapper around :mod:`pyodbc` for Azure SQL.

    Parameters
    ----------
    server:
        Fully qualified server hostname, e.g.
        ``calabriomafpoc-sql.database.windows.net``. Sourced from
        ``MCP_AZURE_SQL_SERVER``.
    database:
        Database name on that server. Sourced from
        ``MCP_AZURE_SQL_DATABASE``.
    credential:
        Token credential. Defaults to :class:`DefaultAzureCredential`,
        which transparently picks managed identity in Azure or the
        developer's ``az login`` credential locally. Injectable so
        tests can pass a :class:`unittest.mock.Mock` and so future
        code can pin a specific credential (e.g. ``ChainedTokenCredential``).
    driver:
        ODBC driver name. Default matches the package installed by the
        dev container's ``post-create.sh`` and ``mcp-ci.yml``.

    Raises
    ------
    ValueError
        If ``server`` or ``database`` is empty / whitespace, or if any
        of ``server`` / ``database`` / ``driver`` contains an ODBC
        delimiter (``;``, ``=``, ``{``, ``}``, quotes or control
        characters).
    """

    def __init__(
        self,
        *,
        server: str,
        database: str,
        credential: TokenCredential | None = None,
        managed_identity_client_id: str = "",
        environment: str = "local",
        driver: str = "ODBC Driver 18 for SQL Server",
    ) -> None:
        if not server or not server.strip():
            raise ValueError(
                "SqlDatabaseClient: 'server' is required. Set MCP_AZURE_SQL_SERVER "
                "in the environment (PLAN.md §14)."
            )
        if not database or not database.strip():
            raise ValueError(
                "SqlDatabaseClient: 'database' is required. Set MCP_AZURE_SQL_DATABASE "
                "in the environment (PLAN.md §14)."
            )
        _reject_unsafe_identifier("server", server)
        _reject_unsafe_identifier("database", database)
        _reject_unsafe_identifier("driver", driver)

        self._server = server
        self._database = database
        self._environment = environment.strip().lower()
        self._managed_identity_client_id = managed_identity_client_id.strip()
        self._credential: TokenCredential = credential or self._build_default_credential()
        self._driver = driver
        self._cached_token: AccessToken | None = None

    # ------------------------------------------------------------------
    # Credential lockdown
    # ------------------------------------------------------------------
    def _build_default_credential(self) -> DefaultAzureCredential:
        """Return a :class:`DefaultAzureCredential` with every
        non-policy credential type excluded.

        The vanilla :class:`DefaultAzureCredential` tries
        :class:`EnvironmentCredential` first, which reads
        ``AZURE_CLIENT_SECRET`` — a password by another name. Issue #17
        explicitly forbids any password code path, so we disable that
        credential along with every interactive / cache-backed flow
        that would only confuse production diagnostics.

        ``environment``-aware:

        * ``local`` → only the developer's ``az login`` credential
          (``AzureCliCredential``) is enabled.
        * anything else ("azure", "prod", …) → only Managed Identity
          is enabled. In Azure containers / App Service / Functions
          this is the System-Assigned MI, or the UAMI pinned via
          ``managed_identity_client_id``.
        """
        is_local = self._environment == "local"
        return DefaultAzureCredential(
            managed_identity_client_id=self._managed_identity_client_id or None,
            # NEVER allow a client-secret-based credential — that is a
            # password by another name and would defeat the Entra-only
            # policy fixed in issue #17.
            exclude_environment_credential=True,
            # Production must be headless; local devs use ``az login``.
            exclude_interactive_browser_credential=True,
            exclude_visual_studio_code_credential=True,
            exclude_shared_token_cache_credential=True,
            exclude_powershell_credential=True,
            exclude_developer_cli_credential=True,
            exclude_broker_credential=True,
            exclude_workload_identity_credential=True,
            # Production excludes ``az login``; local excludes Managed Identity.
            exclude_cli_credential=not is_local,
            exclude_managed_identity_credential=is_local,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def execute(
        self,
        query: str,
        params: tuple[Any, ...] | list[Any] | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> QueryResult:
        """Run ``query`` and return up to ``max_rows`` rows.

        Parameters
        ----------
        query:
            T-SQL text. Parameter placeholders use ``?`` (pyodbc's
            qmark style); named parameters are *not* supported by the
            Microsoft ODBC driver. The caller is responsible for ensuring
            the query is read-only — schema enforcement and ``bu_id``
            injection live in the validator (issue #19).
        params:
            Positional bind values, matched 1:1 to ``?`` placeholders.
            Pass ``None`` when the query has no parameters.
        max_rows:
            Hard cap on returned rows. We fetch ``max_rows + 1`` from
            the driver so we can flag the result as truncated without
            running ``COUNT(*)`` as a second query.

        Returns
        -------
        QueryResult
            Rows as ``list[dict]`` plus a ``truncated`` flag.

        Raises
        ------
        ValueError
            If ``max_rows`` is not strictly positive.
        pyodbc.Error
            Driver-level failures (connection refused, syntax error,
            permission denied, transient network blip). Callers do
            **not** catch these here; the tool layer (issue #20) wraps
            them into MCP-friendly error responses.
        azure.core.exceptions.ClientAuthenticationError
            When :class:`DefaultAzureCredential` cannot acquire a
            token (e.g. ``az login`` expired, managed identity not
            assigned, ``database.windows.net`` not in the token
            scopes).
        """
        if max_rows < 1:
            raise ValueError(f"max_rows must be >= 1, got {max_rows!r}")

        token_struct = await self._acquire_token_struct()
        connection_string = self._build_connection_string()

        return await asyncio.to_thread(
            self._run_query_blocking,
            connection_string,
            token_struct,
            query,
            tuple(params) if params is not None else (),
            max_rows,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _build_connection_string(self) -> str:
        """Assemble the ODBC connection string.

        No ``UID``, no ``PWD``, no ``Authentication=...`` — the access
        token attr supplied via ``attrs_before`` is the *only*
        authentication mechanism (see module docstring). The result is
        run through :meth:`_strip_password_clauses` as belt-and-braces
        defence so a future refactor that adds an ``extra_options``
        knob cannot silently smuggle a password clause in.
        """
        raw = (
            f"Driver={{{self._driver}}};"
            f"Server=tcp:{self._server},1433;"
            f"Database={self._database};"
            "Encrypt=yes;"
            "TrustServerCertificate=no;"
            "Connection Timeout=30;"
        )
        return self._strip_password_clauses(raw)

    @staticmethod
    def _strip_password_clauses(connection_string: str) -> str:
        """Drop every ``Authentication=``/``UID=``/``PWD=`` clause.

        Belt-and-braces guardrail: even if a caller (or a future
        refactor) manages to thread a non-Entra clause into the
        connection string, this sanitizer removes it before it reaches
        the driver. The matching test
        (``test_connection_string_has_no_password_path``) locks the
        invariant in.
        """
        kept: list[str] = []
        for part in connection_string.split(";"):
            stripped = part.strip()
            if not stripped:
                continue
            if _FORBIDDEN_CONN_STR_CLAUSES.match(stripped):
                logger.warning(
                    "SqlDatabaseClient: stripped forbidden connection-string clause %r",
                    stripped.split("=", 1)[0],
                )
                continue
            kept.append(stripped)
        return ";".join(kept) + ";"

    async def _acquire_token_struct(self) -> bytes:
        """Return a token packed for ``SQL_COPT_SS_ACCESS_TOKEN``.

        Caches the underlying :class:`AccessToken` between calls and
        refreshes it ``_TOKEN_REFRESH_BUFFER_SECONDS`` before its
        declared expiry. The credential itself does its own caching,
        but going through it on every call still costs a lock
        acquisition; this layer makes the hot path lock-free.
        """
        token = self._cached_token
        now = time.time()
        if token is None or token.expires_on - now < _TOKEN_REFRESH_BUFFER_SECONDS:
            logger.debug("Acquiring Azure SQL access token (scope=%s)", _AZURE_SQL_SCOPE)
            token = await asyncio.to_thread(self._credential.get_token, _AZURE_SQL_SCOPE)
            self._cached_token = token

        # The driver expects UTF-16 LE bytes prefixed with a 32-bit
        # little-endian length. ``struct.pack("=i{n}s", n, blob)`` builds
        # exactly that layout (``=`` = native byte order without
        # alignment padding, which on every platform pyodbc supports
        # equals little-endian for ``i``).
        token_bytes = token.token.encode("utf-16-le")
        return struct.pack(f"=i{len(token_bytes)}s", len(token_bytes), token_bytes)

    @staticmethod
    def _run_query_blocking(
        connection_string: str,
        token_struct: bytes,
        query: str,
        params: tuple[Any, ...],
        max_rows: int,
    ) -> QueryResult:
        """Synchronous body of :meth:`execute`, run in a worker thread.

        Kept as a ``@staticmethod`` so :func:`asyncio.to_thread` does
        not pin a reference to ``self`` inside the worker (cleaner
        teardown, no accidental state mutation from the thread).
        """
        attrs_before = {_SQL_COPT_SS_ACCESS_TOKEN: token_struct}
        with (
            pyodbc.connect(connection_string, attrs_before=attrs_before) as conn,
            conn.cursor() as cursor,
        ):
            cursor.execute(query, *params) if params else cursor.execute(query)
            # ``cursor.description`` is None for statements that
            # return no result set (DDL, some procs). Guard so we
            # do not blow up indexing into None.
            if cursor.description is None:
                return QueryResult(rows=[], truncated=False)

            columns = [col[0] for col in cursor.description]
            # Fetch one extra row to detect truncation without a
            # second round-trip / COUNT(*).
            raw_rows = cursor.fetchmany(max_rows + 1)

        truncated = len(raw_rows) > max_rows
        if truncated:
            raw_rows = raw_rows[:max_rows]
            logger.warning("SqlDatabaseClient.execute truncated result to %d rows", max_rows)

        rows: list[dict[str, Any]] = [dict(zip(columns, row, strict=True)) for row in raw_rows]
        return QueryResult(rows=rows, truncated=truncated)


__all__ = [
    "DEFAULT_MAX_ROWS",
    "QueryResult",
    "SqlDatabaseClient",
]
