"""Integration-suite fixtures: mssql testcontainer + bootstrapped schema.

A single session-scoped fixture spins up
``mcr.microsoft.com/mssql/server:2022-latest``, runs the project's
``database/01-03*.sql`` files (skipping ``04`` which is Entra-only),
and exposes a SA-auth ``_SaSqlClient`` that mirrors the
:class:`~app.clients.sql.SqlDatabaseClient` contract closely enough
to drop into ``set_sql_client(...)`` on both the ``schema`` and
``query`` sub-servers.

Why a custom client instead of ``SqlDatabaseClient``
----------------------------------------------------
:class:`SqlDatabaseClient` is **hard-locked** to Entra auth (issue
#17 policy — no password path exists). The SQL Server docker image
only supports SA / SQL Auth. Threading a password through the
production client would mean introducing the very code path the
policy forbids, so the test layer uses a tiny parallel client
instead. The two clients share the same public surface
(``async def execute(sql, params=None, max_rows=...) -> QueryResult``)
and the tools call it identically.

Performance budget (issue #23 acceptance: <90 s in CI)
------------------------------------------------------
* mssql container cold-pull on a clean runner: ~30-45 s
* startup (sa healthcheck): ~10-15 s
* schema bootstrap (3 SQL files, split on ``GO``): ~3-5 s
* the 10 tests below: ~3 s in total

Total worst-case ~65 s, well under the 90 s acceptance gate, with
plenty of head-room on warm runners where the image is cached.
"""

from __future__ import annotations

import logging
import re
import secrets
import string
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Module-level skip if docker / testcontainers aren't usable
# ---------------------------------------------------------------------------
# These tests can only run on a host with a working Docker daemon.
# Importing ``testcontainers`` does not by itself prove docker works,
# but a missing import is a definitive "skip the whole module" signal.
# The container's ``start()`` call will raise later if the daemon
# refuses connections; we surface that as a fixture-level skip rather
# than a test failure.

pyodbc = pytest.importorskip("pyodbc", reason="pyodbc not installed")
try:
    from testcontainers.mssql import SqlServerContainer  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - guard for CI without the extra installed
    pytest.skip(
        "testcontainers[mssql] is not installed; "
        "install the dev extras to run integration tests",
        allow_module_level=True,
    )

# Late imports so the skip above runs *before* we touch ``app.*`` —
# importing app modules is cheap but keeps the failure mode tidy.
from app.clients.sql import QueryResult  # noqa: E402
from app.servers import query as query_module  # noqa: E402
from app.servers import schema as schema_module  # noqa: E402

logger = logging.getLogger(__name__)

# SA password for the ephemeral container. Generated fresh on
# every test-session import so nothing secret-shaped ever lives in
# source control (closes the GitGuardian alert raised on the first
# revision of this file, which hard-coded the value).
#
# Composition: one random char from each of the four complexity
# classes that SQL Server requires (≥ 3 of {upper, lower, digit,
# symbol} and ≥ 8 chars) plus a ``token_urlsafe(24)`` tail that
# contributes ~192 bits of entropy. We avoid any hard-coded literal
# that looks password-shaped so secret-scanners don't flag the
# generator itself as a false positive. The container is bound to
# a random localhost port and torn down at session exit, so the
# credential never leaves the local docker network.
_SA_PASSWORD = "".join(
    (
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#%^&*"),
        secrets.token_urlsafe(24),
    )
)

# Lines that delimit T-SQL batches. We split on ``^GO\s*$`` because
# ``GO`` is a sqlcmd directive, not a TSQL statement — pyodbc cannot
# execute a multi-batch script in a single ``cursor.execute`` call.
_GO_BATCH_BOUNDARY = re.compile(r"(?im)^\s*GO\s*$")

# Bootstrap order. ``04-grant-readonly.sql`` is **deliberately**
# excluded — it uses ``CREATE USER ... FROM EXTERNAL PROVIDER`` which
# only works on Azure SQL with Entra ID. Integration tests connect as
# sa and don't need the grants.
_BOOTSTRAP_SQL_FILES: tuple[str, ...] = (
    "01-schemas-and-tables.sql",
    "02-views.sql",
    "03-seed-data.sql",
)

# Path to the project's ``database/`` folder, relative to this file.
# ``apps/mcp/tests/integration/conftest.py`` \u2192 ``../../../../database``
# (4 levels up: integration \u2192 tests \u2192 mcp \u2192 apps \u2192 repo root).
_DATABASE_DIR = Path(__file__).resolve().parents[4] / "database"


# ---------------------------------------------------------------------------
# SA-auth client (test-only shim)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ConnInfo:
    """Connection details for the running mssql container."""

    host: str
    port: int
    database: str
    user: str
    password: str

    def odbc_conn_str(self, driver: str = "ODBC Driver 18 for SQL Server") -> str:
        """Assemble an ODBC connection string for ``pyodbc.connect``.

        Uses SA auth (``UID``/``PWD``). ``TrustServerCertificate=yes``
        is set because the mssql container ships a self-signed cert
        and we don't want CI to fail on cert validation. The
        production client never does this — see
        :mod:`app.clients.sql` for the locked-down version.
        """
        return (
            f"Driver={{{driver}}};"
            f"Server=tcp:{self.host},{self.port};"
            f"Database={self.database};"
            f"UID={self.user};PWD={self.password};"
            "Encrypt=yes;TrustServerCertificate=yes;Connection Timeout=30;"
        )


class _SaSqlClient:
    """Async ``execute``-only shim mirroring :class:`SqlDatabaseClient`.

    Same public surface (``async def execute(sql, params=None, max_rows=...)``)
    but with SA auth via raw ``pyodbc``. Only exists so the tools can
    be exercised against a real engine without bypassing the
    Entra-only policy that governs the production client.
    """

    def __init__(self, conn_info: _ConnInfo) -> None:
        self._conn_str = conn_info.odbc_conn_str()

    async def execute(
        self,
        query: str,
        params: Sequence[Any] | None = None,
        max_rows: int = 1_000,
    ) -> QueryResult:
        """Run ``query`` and return up to ``max_rows`` rows.

        Synchronous under the hood — :class:`SqlDatabaseClient` uses
        ``asyncio.to_thread`` to keep its async surface; we don't
        bother here because the integration suite is small and the
        test runner is single-threaded anyway. The ``async def``
        signature is what matters: the tools ``await`` it.
        """
        if max_rows < 1:
            raise ValueError(f"max_rows must be >= 1, got {max_rows!r}")
        with (
            pyodbc.connect(self._conn_str, autocommit=True) as conn,
            conn.cursor() as cur,
        ):
            if params:
                cur.execute(query, tuple(params))
            else:
                cur.execute(query)
            # Non-query statements (INSERT/UPDATE/DELETE) leave
            # ``description`` unset and make ``fetchmany`` raise
            # ``No results. Previous SQL was not a query.``— short-
            # circuit on the description first so test setup helpers
            # can call ``execute`` for both reads and writes.
            if cur.description is None:
                return QueryResult(rows=[], truncated=False)
            # Fetch one row beyond the cap so truncation can be
            # flagged without a second round-trip. Mirrors the
            # production client's strategy.
            fetched = cur.fetchmany(max_rows + 1)
            truncated = len(fetched) > max_rows
            rows = fetched[:max_rows]
            columns = [col[0] for col in cur.description]
            return QueryResult(
                rows=[dict(zip(columns, row, strict=True)) for row in rows],
                truncated=truncated,
            )


# ---------------------------------------------------------------------------
# Container + bootstrap
# ---------------------------------------------------------------------------


def _split_into_batches(sql: str) -> list[str]:
    """Split a sqlcmd-style script into individual pyodbc-executable batches.

    ``GO`` lines are batch separators for sqlcmd / SSMS, not T-SQL
    statements. ``pyodbc.Cursor.execute`` refuses scripts that
    contain ``GO`` on its own line. We split on that delimiter and
    drop empty fragments.
    """
    parts = _GO_BATCH_BOUNDARY.split(sql)
    return [chunk for chunk in (p.strip() for p in parts) if chunk]


def _bootstrap_schema(conn_info: _ConnInfo) -> None:
    """Run the project's ``01-03*.sql`` files against the container.

    The files are deliberately idempotent (every ``CREATE`` is wrapped
    in ``IF NOT EXISTS`` / ``IF OBJECT_ID ... IS NULL``), so re-running
    against a warm container is safe. We still spin a fresh container
    per session for hermeticity.
    """
    conn_str = conn_info.odbc_conn_str()
    for filename in _BOOTSTRAP_SQL_FILES:
        path = _DATABASE_DIR / filename
        script = path.read_text(encoding="utf-8")
        batches = _split_into_batches(script)
        logger.info(
            "integration bootstrap: %s (%d batches)", filename, len(batches)
        )
        with (
            pyodbc.connect(conn_str, autocommit=True) as conn,
            conn.cursor() as cur,
        ):
            for batch in batches:
                cur.execute(batch)
                # Drain any rowsets the script may emit (``03-seed``
                # has a few diagnostic SELECTs at the end of each
                # MERGE block). Without this the next ``execute``
                # would fail with "previous SQL was not a query".
                while cur.nextset():
                    pass


@pytest.fixture(scope="session")
def _mssql_container() -> Iterator[_ConnInfo]:
    """Start the mssql testcontainer once per pytest session.

    Session-scoped so the ~30 s startup cost amortises across all
    integration tests, keeping the suite under the 90 s acceptance
    budget (issue #23). On a warm runner with the image cached, the
    whole bring-up is closer to 15 s.
    """
    image = "mcr.microsoft.com/mssql/server:2022-latest"
    # ``testcontainers[mssql]`` historically accepted a ``dialect=``
    # kwarg used by the built-in SQLAlchemy URL helper. We don't use
    # that helper (we hand-build an ODBC string for pyodbc) so omit
    # the kwarg — it became optional in 4.x and is incompatible with
    # some 4.10+ snapshots.
    container = SqlServerContainer(image, password=_SA_PASSWORD)
    try:
        container.start()
    except Exception as exc:  # pragma: no cover - env-dependent
        # The user (or CI via ``MCP_RUN_INTEGRATION=1``) explicitly
        # opted into this suite, so a broken Docker daemon / image
        # pull / port-bind failure must fail the run loudly. Skipping
        # here would let CI report green with zero end-to-end
        # coverage — exactly the gap the integration gate is meant
        # to close. Reviewer (Copilot, PR #72) flagged this.
        pytest.fail(
            f"could not start mssql container ({exc!r}); "
            "integration tests were opted in via MCP_RUN_INTEGRATION=1, "
            "so this is a hard failure rather than a skip.",
            pytrace=False,
        )
    try:
        info = _ConnInfo(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(1433)),
            database="master",  # the image's default DB; bootstrap creates schemas
            user="sa",
            password=_SA_PASSWORD,
        )
        # The container is "started" as soon as the docker daemon
        # accepts the run command, but mssql itself needs a few more
        # seconds to finish recovering ``master``. Probe with a cheap
        # ``SELECT 1`` until it answers or we hit the deadline.
        deadline = time.monotonic() + 60.0
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with pyodbc.connect(info.odbc_conn_str(), timeout=5) as conn:
                    conn.cursor().execute("SELECT 1").fetchone()
                break
            except pyodbc.Error as err:  # pragma: no cover - timing-dependent
                last_err = err
                time.sleep(1.0)
        else:  # pragma: no cover - timing-dependent
            # Same rationale as the ``start()`` branch above: the
            # suite is opt-in, so a container that never becomes
            # reachable means the gate hasn't run — fail loudly.
            pytest.fail(
                f"mssql container never accepted connections within 60s: {last_err!r}",
                pytrace=False,
            )
        _bootstrap_schema(info)
        yield info
    finally:
        container.stop()


@pytest.fixture(scope="session")
def _integration_client(_mssql_container: _ConnInfo) -> _SaSqlClient:
    """Return the SA-auth client wired to the bootstrapped container."""
    return _SaSqlClient(_mssql_container)


@pytest.fixture(autouse=True)
def _wire_tools_to_container(_integration_client: _SaSqlClient) -> Iterator[None]:
    """Inject the SA client into both sub-servers around every test.

    Function-scoped so each test starts with a fresh allowlist cache
    (``query.execute`` lazily fetches it from
    ``_metadata.catalog_tables`` on first call). The teardown clears
    the singletons so a follow-on unit test in the same session
    doesn't accidentally inherit the integration wiring.
    """
    schema_module.set_sql_client(_integration_client)  # type: ignore[arg-type]
    query_module.set_sql_client(_integration_client)  # type: ignore[arg-type]
    query_module.set_allowlist(None)  # force a fresh catalog read
    try:
        yield
    finally:
        schema_module.set_sql_client(None)
        query_module.set_sql_client(None)
        query_module.set_allowlist(None)
