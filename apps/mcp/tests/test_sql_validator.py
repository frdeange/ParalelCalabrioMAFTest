"""Tests for :mod:`app.security.sql_validator` (issue #19).

Coverage targets:
- Every node in the ALLOWED top-level set parses and validates.
- Every node in the FORBIDDEN blocklist is rejected.
- Multi-statement detection.
- Comment-stripping in normalised SQL.
- Allowlist behaviour (qualified vs bare names, case-insensitivity).
- Defensive input checks (empty / non-str / unparseable).
- Table extraction (CTE aliases excluded, dedup preserves first-seen
  order, schema-qualified vs bare).
"""

from __future__ import annotations

import pytest

from app.security.sql_validator import ValidationResult, validate

# ---------------------------------------------------------------------------
# Allowed
# ---------------------------------------------------------------------------

_ALLOWED_QUERIES = [
    pytest.param("SELECT 1", id="select-literal"),
    pytest.param("SELECT TOP 10 * FROM dbo.users", id="select-top"),
    pytest.param(
        "SELECT u.id, u.name FROM dbo.users AS u WHERE u.bu_id = 1",
        id="select-where",
    ),
    pytest.param(
        "SELECT u.id, COUNT(*) AS n FROM dbo.users u GROUP BY u.id HAVING COUNT(*) > 1",
        id="select-group-having",
    ),
    pytest.param(
        "SELECT u.id FROM dbo.users u JOIN sales.orders o ON u.id = o.uid",
        id="select-join",
    ),
    pytest.param(
        "SELECT u.id FROM dbo.users u ORDER BY u.id DESC",
        id="select-orderby",
    ),
    pytest.param(
        "WITH c AS (SELECT id FROM dbo.users) SELECT * FROM c",
        id="select-cte",
    ),
    pytest.param("SELECT 1 UNION SELECT 2", id="select-union"),
    pytest.param("SELECT 1 INTERSECT SELECT 1", id="select-intersect"),
    pytest.param("SELECT 1 EXCEPT SELECT 2", id="select-except"),
    pytest.param("(SELECT * FROM dbo.users)", id="select-subquery-top"),
    pytest.param(
        "SELECT id FROM dbo.users WHERE id IN (SELECT uid FROM sales.orders)",
        id="select-subquery-in",
    ),
]


@pytest.mark.parametrize("sql", _ALLOWED_QUERIES)
def test_validate_allows_pure_selects(sql: str) -> None:
    result = validate(sql)
    assert result.ok, f"expected ok, got reason: {result.reason}"
    assert result.reason is None
    assert result.normalized_sql, "normalised SQL should be set when ok"


# ---------------------------------------------------------------------------
# Blocked by AST node type
# ---------------------------------------------------------------------------

_BLOCKED_QUERIES = [
    pytest.param("INSERT INTO dbo.users (id) VALUES (1)", "Insert", id="insert"),
    pytest.param("UPDATE dbo.users SET name = 'x' WHERE id = 1", "Update", id="update"),
    pytest.param("DELETE FROM dbo.users WHERE id = 1", "Delete", id="delete"),
    pytest.param(
        "MERGE dbo.users AS t USING dbo.staging AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.name = s.name;",
        "Merge",
        id="merge",
    ),
    pytest.param("DROP TABLE dbo.users", "Drop", id="drop"),
    pytest.param("CREATE TABLE dbo.users (id INT)", "Create", id="create"),
    pytest.param("ALTER TABLE dbo.users ADD col INT", "Alter", id="alter"),
    pytest.param("TRUNCATE TABLE dbo.users", "TruncateTable", id="truncate"),
    pytest.param("GRANT SELECT ON dbo.users TO public", "Grant", id="grant"),
    pytest.param("EXEC sp_who", "Execute", id="exec"),
    pytest.param("EXECUTE sp_who", "Execute", id="execute"),
    pytest.param("USE master", "Use", id="use"),
    pytest.param("DECLARE @x INT = 1", "Declare", id="declare"),
]


@pytest.mark.parametrize("sql, expected_node", _BLOCKED_QUERIES)
def test_validate_rejects_forbidden_nodes(sql: str, expected_node: str) -> None:
    result = validate(sql)
    assert not result.ok
    assert result.reason is not None
    # Either the top-level check or the forbidden-walk check fires
    # first; both surface the offending node name in the reason.
    assert expected_node in result.reason, result.reason
    assert result.normalized_sql is None
    assert result.tables == ()


# ---------------------------------------------------------------------------
# Multi-statement & comment-injection bypass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("SELECT 1; DROP TABLE dbo.users", id="select-then-drop"),
        pytest.param("SELECT 1; SELECT 2", id="two-selects"),
        pytest.param(
            "SELECT 1 /* harmless */; DELETE FROM dbo.users",
            id="comment-then-delete",
        ),
        pytest.param("SELECT 1; -- DROP TABLE dbo.users\nSELECT 2", id="line-comment-bypass"),
    ],
)
def test_multi_statement_rejected(sql: str) -> None:
    result = validate(sql)
    assert not result.ok
    assert "multi-statement" in (result.reason or "")


def test_trailing_semicolon_is_not_multi_statement() -> None:
    """A lone trailing ``;`` parses as a single SELECT, not two stmts."""
    result = validate("SELECT 1;")
    assert result.ok, result.reason


def test_single_statement_with_inline_comments_normalised() -> None:
    """Comments inside a single SELECT must be stripped on output."""
    result = validate("SELECT /* hi */ id FROM dbo.users -- trailing")
    assert result.ok
    assert result.normalized_sql is not None
    assert "/*" not in result.normalized_sql
    assert "--" not in result.normalized_sql
    assert "hi" not in result.normalized_sql
    assert "trailing" not in result.normalized_sql


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


def test_allowlist_accepts_listed_qualified_table() -> None:
    result = validate(
        "SELECT * FROM analytics.vw_persondetail",
        allowlist={"analytics.vw_persondetail"},
    )
    assert result.ok, result.reason
    assert result.tables == ("analytics.vw_persondetail",)


def test_allowlist_is_case_insensitive() -> None:
    result = validate(
        "SELECT * FROM Analytics.VW_PersonDetail",
        allowlist={"analytics.vw_persondetail"},
    )
    assert result.ok, result.reason


def test_allowlist_qualified_entry_matches_bare_reference() -> None:
    """An allowlist entry ``schema.table`` also matches a bare ``table`` ref."""
    result = validate(
        "SELECT * FROM users",
        allowlist={"dbo.users"},
    )
    assert result.ok, result.reason


def test_allowlist_rejects_unknown_table() -> None:
    result = validate(
        "SELECT * FROM dbo.users JOIN dbo.secrets ON 1=1",
        allowlist={"dbo.users"},
    )
    assert not result.ok
    assert "allowlist" in (result.reason or "")
    assert "secrets" in (result.reason or "").lower()


def test_allowlist_none_skips_check() -> None:
    """``allowlist=None`` (the default) accepts any table."""
    result = validate("SELECT * FROM any.table_we_made_up")
    assert result.ok, result.reason


# ---------------------------------------------------------------------------
# Defensive input handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sql", ["", "   ", "\n\t"])
def test_empty_input_rejected(sql: str) -> None:
    result = validate(sql)
    assert not result.ok
    assert "non-empty" in (result.reason or "")


def test_non_string_rejected() -> None:
    result = validate(123)  # type: ignore[arg-type]
    assert not result.ok
    assert "must be a str" in (result.reason or "")


def test_unparseable_sql_returns_reason() -> None:
    result = validate("SELECT (((")
    assert not result.ok
    # Either a parse error or a top-level mismatch; both are acceptable
    # outcomes — what matters is we never raise.
    assert result.reason


def test_only_semicolons_rejected() -> None:
    result = validate(";;;")
    assert not result.ok
    assert "no parseable statement" in (result.reason or "")


# ---------------------------------------------------------------------------
# Table extraction
# ---------------------------------------------------------------------------


def test_cte_alias_not_returned_as_table() -> None:
    """CTE self-references must not appear in the extracted table list."""
    result = validate(
        "WITH c AS (SELECT id FROM dbo.users) "
        "SELECT * FROM c JOIN sales.orders o ON c.id = o.uid"
    )
    assert result.ok, result.reason
    # ``c`` is a CTE alias; ``dbo.users`` and ``sales.orders`` are real.
    tables_lower = {t.lower() for t in result.tables}
    assert "c" not in tables_lower
    assert "dbo.users" in tables_lower
    assert "sales.orders" in tables_lower


def test_duplicate_tables_deduplicated_preserving_order() -> None:
    result = validate(
        "SELECT * FROM dbo.users a, dbo.users b WHERE a.id < b.id"
    )
    assert result.ok, result.reason
    assert result.tables == ("dbo.users",)


def test_returns_validation_result_dataclass() -> None:
    """Smoke test on the public dataclass shape (frozen, immutable)."""
    result = validate("SELECT 1")
    assert isinstance(result, ValidationResult)
    with pytest.raises((AttributeError, Exception)):
        result.ok = False  # type: ignore[misc]
