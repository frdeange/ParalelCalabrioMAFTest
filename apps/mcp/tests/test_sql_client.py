"""Unit tests for :mod:`app.clients.sql`.

All tests run with ``pyodbc.connect`` and ``DefaultAzureCredential``
fully mocked — no real database is contacted. Integration testing
against a live Azure SQL instance lives in issue #23 (full pytest
suite) and Phase 5's ``azd up`` smoke tests.

What we lock in here:

* The connection string contains **no** ``UID``, ``PWD`` or
  ``Authentication=`` clauses. This is the security guardrail
  required by issue #17 and would silently regress if a future
  refactor reintroduced a password code path.
* The access token is acquired with the correct scope and packed
  with a 4-byte length prefix.
* The token is cached and refreshed only when within the
  ``_TOKEN_REFRESH_BUFFER_SECONDS`` window of expiry.
* Truncation works (fetches ``max_rows + 1``, returns ``max_rows``,
  flag set).
* ``description is None`` paths (DDL-style queries) return an empty
  result without crashing.
"""

from __future__ import annotations

import struct
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from azure.core.credentials import AccessToken

from app.clients.sql import (
    DEFAULT_MAX_ROWS,
    QueryResult,
    SqlDatabaseClient,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(value: str = "fake-token", *, expires_in: int = 3600) -> AccessToken:
    """Build an :class:`AccessToken` that looks valid for ``expires_in`` seconds."""
    return AccessToken(token=value, expires_on=int(time.time()) + expires_in)


def _fake_cursor(
    columns: list[str] | None,
    rows: list[tuple[Any, ...]],
) -> MagicMock:
    """Build a ``pyodbc.Cursor`` mock that behaves like a context manager.

    Setting ``description`` to ``None`` simulates statements that do
    not return a result set (DDL, certain stored procs).
    """
    cursor = MagicMock()
    if columns is None:
        cursor.description = None
    else:
        cursor.description = [(name, None, None, None, None, None, None) for name in columns]
    cursor.fetchmany.return_value = rows
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    return cursor


def _fake_connection(cursor: MagicMock) -> MagicMock:
    """Build a ``pyodbc.Connection`` mock with the given cursor."""
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    return conn


def _make_client(credential: MagicMock | None = None) -> SqlDatabaseClient:
    return SqlDatabaseClient(
        server="srv.database.windows.net",
        database="wfm",
        credential=credential or MagicMock(),
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_server_raises(bad: str) -> None:
    with pytest.raises(ValueError, match="server"):
        SqlDatabaseClient(server=bad, database="wfm", credential=MagicMock())


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_database_raises(bad: str) -> None:
    with pytest.raises(ValueError, match="database"):
        SqlDatabaseClient(server="srv", database=bad, credential=MagicMock())


def test_default_credential_used_when_none_passed() -> None:
    """No injected credential ⇒ build a :class:`DefaultAzureCredential`.

    Pinned via patch so the test does not actually run the credential
    chain (which would try managed-identity probes in CI).
    """
    with patch("app.clients.sql.DefaultAzureCredential") as cred_cls:
        cred_cls.return_value = MagicMock()
        SqlDatabaseClient(server="srv", database="wfm")
        cred_cls.assert_called_once()


def test_default_credential_locks_down_environment_credential() -> None:
    """``AZURE_CLIENT_SECRET`` is a password by another name. The
    credential chain must never honour it, regardless of
    ``MCP_ENVIRONMENT``.
    """
    with patch("app.clients.sql.DefaultAzureCredential") as cred_cls:
        SqlDatabaseClient(server="srv", database="wfm")
        kwargs = cred_cls.call_args.kwargs
        assert kwargs["exclude_environment_credential"] is True
        assert kwargs["exclude_interactive_browser_credential"] is True
        assert kwargs["exclude_visual_studio_code_credential"] is True
        assert kwargs["exclude_shared_token_cache_credential"] is True
        assert kwargs["exclude_powershell_credential"] is True
        assert kwargs["exclude_developer_cli_credential"] is True
        assert kwargs["exclude_broker_credential"] is True
        assert kwargs["exclude_workload_identity_credential"] is True


def test_default_credential_local_uses_cli_only() -> None:
    """In ``environment=local``: ``az login`` allowed, MI blocked."""
    with patch("app.clients.sql.DefaultAzureCredential") as cred_cls:
        SqlDatabaseClient(server="srv", database="wfm", environment="local")
        kwargs = cred_cls.call_args.kwargs
        assert kwargs["exclude_cli_credential"] is False
        assert kwargs["exclude_managed_identity_credential"] is True


def test_default_credential_azure_uses_mi_only() -> None:
    """In production: MI allowed, ``az login`` blocked."""
    with patch("app.clients.sql.DefaultAzureCredential") as cred_cls:
        SqlDatabaseClient(server="srv", database="wfm", environment="azure")
        kwargs = cred_cls.call_args.kwargs
        assert kwargs["exclude_cli_credential"] is True
        assert kwargs["exclude_managed_identity_credential"] is False


def test_managed_identity_client_id_is_pinned() -> None:
    """UAMI client id flows through to the credential constructor."""
    uami_id = "11111111-2222-3333-4444-555555555555"
    with patch("app.clients.sql.DefaultAzureCredential") as cred_cls:
        SqlDatabaseClient(
            server="srv",
            database="wfm",
            environment="azure",
            managed_identity_client_id=uami_id,
        )
        assert cred_cls.call_args.kwargs["managed_identity_client_id"] == uami_id


def test_managed_identity_client_id_empty_passes_none() -> None:
    """Empty / whitespace ought to translate to ``None`` so MSAL picks
    the SAMI rather than silently looking up a UAMI with id ``""``."""
    with patch("app.clients.sql.DefaultAzureCredential") as cred_cls:
        SqlDatabaseClient(server="srv", database="wfm", managed_identity_client_id="  ")
        assert cred_cls.call_args.kwargs["managed_identity_client_id"] is None


# ---------------------------------------------------------------------------
# Connection string — security guardrail
# ---------------------------------------------------------------------------


def test_connection_string_has_no_password_path() -> None:
    """The connection string must never expose a SQL-auth fallback.

    Failing this test means a refactor added ``UID``/``PWD`` or an
    ``Authentication=`` clause, both of which violate the Entra-only
    policy fixed in issue #17.
    """
    client = _make_client()
    conn_str = client._build_connection_string()

    lowered = conn_str.lower()
    assert "uid=" not in lowered
    assert "pwd=" not in lowered
    assert "password=" not in lowered
    assert "authentication=" not in lowered
    assert "trusted_connection=" not in lowered
    # And the bits we *do* expect:
    assert "encrypt=yes" in lowered
    assert "trustservercertificate=no" in lowered
    assert "driver={odbc driver 18 for sql server}" in lowered
    assert "srv.database.windows.net" in conn_str
    assert "Database=wfm" in conn_str


@pytest.mark.parametrize(
    "smuggled",
    [
        "Authentication=ActiveDirectoryPassword",
        "authentication=SqlPassword",
        "UID=admin",
        "User ID=admin",
        "PWD=hunter2",
        "password=hunter2",
        "Trusted_Connection=yes",
    ],
)
def test_strip_password_clauses_removes_smuggled_clauses(smuggled: str) -> None:
    """Belt-and-braces: if a refactor ever pipes a tainted segment in,
    the sanitizer drops it before it reaches the driver.
    """
    tainted = (
        "Driver={ODBC Driver 18 for SQL Server};"
        "Server=tcp:srv,1433;Database=wfm;Encrypt=yes;"
        f"{smuggled};"
    )
    cleaned = SqlDatabaseClient._strip_password_clauses(tainted).lower()
    bad_key = smuggled.split("=", 1)[0].strip().lower()
    assert bad_key not in cleaned, (
        f"sanitizer failed to remove {smuggled!r} from connection string"
    )


# ---------------------------------------------------------------------------
# Token acquisition + caching
# ---------------------------------------------------------------------------


async def test_token_acquired_with_correct_scope() -> None:
    credential = MagicMock()
    credential.get_token.return_value = _make_token()
    client = _make_client(credential)

    await client._acquire_token_struct()

    credential.get_token.assert_called_once_with("https://database.windows.net/.default")


async def test_token_is_packed_with_length_prefix() -> None:
    credential = MagicMock()
    credential.get_token.return_value = _make_token("hello-token")
    client = _make_client(credential)

    packed = await client._acquire_token_struct()

    expected_bytes = "hello-token".encode("utf-16-le")
    # Length prefix: 4 bytes little-endian == len(expected_bytes).
    length, = struct.unpack("=i", packed[:4])
    assert length == len(expected_bytes)
    assert packed[4:] == expected_bytes


async def test_token_is_cached_within_buffer_window() -> None:
    """Second call within the validity window reuses the cached token.

    The credential's ``get_token`` should fire exactly once.
    """
    credential = MagicMock()
    credential.get_token.return_value = _make_token(expires_in=3600)
    client = _make_client(credential)

    await client._acquire_token_struct()
    await client._acquire_token_struct()

    assert credential.get_token.call_count == 1


async def test_token_is_refreshed_when_within_buffer() -> None:
    """Token expiring inside the 5-min buffer ⇒ re-fetch on next call."""
    credential = MagicMock()
    # Already inside the refresh buffer (200s < 300s default buffer).
    credential.get_token.side_effect = [
        _make_token("first", expires_in=200),
        _make_token("second", expires_in=3600),
    ]
    client = _make_client(credential)

    await client._acquire_token_struct()
    await client._acquire_token_struct()

    assert credential.get_token.call_count == 2


# ---------------------------------------------------------------------------
# execute() — happy path
# ---------------------------------------------------------------------------


async def test_execute_returns_rows_as_dicts() -> None:
    credential = MagicMock()
    credential.get_token.return_value = _make_token()
    cursor = _fake_cursor(
        columns=["id", "name"],
        rows=[(1, "Alice"), (2, "Bob")],
    )
    connection = _fake_connection(cursor)
    client = _make_client(credential)

    with patch("app.clients.sql.pyodbc.connect", return_value=connection) as mock_connect:
        result = await client.execute("SELECT id, name FROM users")

    assert isinstance(result, QueryResult)
    assert result.rows == [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    assert result.truncated is False
    # ``attrs_before`` must carry the SQL_COPT_SS_ACCESS_TOKEN attr (1256).
    _, kwargs = mock_connect.call_args
    assert 1256 in kwargs["attrs_before"]


async def test_execute_passes_positional_params() -> None:
    credential = MagicMock()
    credential.get_token.return_value = _make_token()
    cursor = _fake_cursor(columns=["id"], rows=[(42,)])
    connection = _fake_connection(cursor)
    client = _make_client(credential)

    with patch("app.clients.sql.pyodbc.connect", return_value=connection):
        await client.execute("SELECT id FROM users WHERE bu_id = ?", params=(7,))

    cursor.execute.assert_called_once_with("SELECT id FROM users WHERE bu_id = ?", 7)


async def test_execute_with_no_params_does_not_pass_args() -> None:
    """No params ⇒ ``cursor.execute(query)`` with a single arg.

    pyodbc treats ``cursor.execute(query, None)`` as "one None
    positional parameter" rather than "no parameters". This guards
    against that footgun.
    """
    credential = MagicMock()
    credential.get_token.return_value = _make_token()
    cursor = _fake_cursor(columns=["x"], rows=[(1,)])
    connection = _fake_connection(cursor)
    client = _make_client(credential)

    with patch("app.clients.sql.pyodbc.connect", return_value=connection):
        await client.execute("SELECT 1 AS x")

    cursor.execute.assert_called_once_with("SELECT 1 AS x")


# ---------------------------------------------------------------------------
# execute() — truncation
# ---------------------------------------------------------------------------


async def test_execute_flags_truncation_when_extra_row_returned() -> None:
    credential = MagicMock()
    credential.get_token.return_value = _make_token()
    # Driver returns max_rows + 1 → caller asked for 2.
    cursor = _fake_cursor(
        columns=["id"],
        rows=[(1,), (2,), (3,)],
    )
    connection = _fake_connection(cursor)
    client = _make_client(credential)

    with patch("app.clients.sql.pyodbc.connect", return_value=connection):
        result = await client.execute("SELECT id FROM users", max_rows=2)

    assert result.truncated is True
    assert result.rows == [{"id": 1}, {"id": 2}]


async def test_execute_does_not_flag_truncation_when_exactly_at_limit() -> None:
    credential = MagicMock()
    credential.get_token.return_value = _make_token()
    cursor = _fake_cursor(columns=["id"], rows=[(1,), (2,)])
    connection = _fake_connection(cursor)
    client = _make_client(credential)

    with patch("app.clients.sql.pyodbc.connect", return_value=connection):
        result = await client.execute("SELECT id FROM users", max_rows=2)

    assert result.truncated is False
    assert result.rows == [{"id": 1}, {"id": 2}]


async def test_execute_fetches_max_rows_plus_one() -> None:
    """Verify the ``+1`` trick is what hits the driver."""
    credential = MagicMock()
    credential.get_token.return_value = _make_token()
    cursor = _fake_cursor(columns=["id"], rows=[(1,)])
    connection = _fake_connection(cursor)
    client = _make_client(credential)

    with patch("app.clients.sql.pyodbc.connect", return_value=connection):
        await client.execute("SELECT id FROM users", max_rows=500)

    cursor.fetchmany.assert_called_once_with(501)


# ---------------------------------------------------------------------------
# execute() — DDL-style queries (no result set)
# ---------------------------------------------------------------------------


async def test_execute_returns_empty_result_when_description_is_none() -> None:
    credential = MagicMock()
    credential.get_token.return_value = _make_token()
    cursor = _fake_cursor(columns=None, rows=[])
    connection = _fake_connection(cursor)
    client = _make_client(credential)

    with patch("app.clients.sql.pyodbc.connect", return_value=connection):
        result = await client.execute("CREATE TABLE foo (id INT)")

    assert result == QueryResult(rows=[], truncated=False)
    cursor.fetchmany.assert_not_called()


# ---------------------------------------------------------------------------
# execute() — input validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, -100])
async def test_execute_rejects_non_positive_max_rows(bad: int) -> None:
    client = _make_client()
    with pytest.raises(ValueError, match="max_rows"):
        await client.execute("SELECT 1", max_rows=bad)


def test_default_max_rows_is_reasonable() -> None:
    """Lock the default at 1000 so a future tweak is a deliberate change."""
    assert DEFAULT_MAX_ROWS == 1_000
