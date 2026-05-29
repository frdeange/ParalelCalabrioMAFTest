"""Unit tests for ``check_metadata_drift``.

These tests never touch a real database. A ``FakeCursor`` returns
hard-coded rows for each query the script issues, the connect path is
monkeypatched, and the rest is pure-Python logic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

import check_metadata_drift as cmd

# ---------------------------------------------------------------------------
# Fake cursor / connection — minimal protocol implementations.
# ---------------------------------------------------------------------------


class FakeCursor:
    """Returns canned rows based on which SQL string is executed."""

    def __init__(self, responses: dict[str, list[tuple[Any, ...]]]) -> None:
        self._responses = responses
        self._pending: list[tuple[Any, ...]] = []
        self.executed: list[str] = []
        self.closed = False

    def execute(self, sql: str, /) -> FakeCursor:
        self.executed.append(sql)
        for key, rows in self._responses.items():
            if key in sql:
                self._pending = list(rows)
                return self
        raise AssertionError(f"FakeCursor: no canned response for SQL: {sql!r}")

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows = self._pending
        self._pending = []
        return rows

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def _make_cursor(
    *,
    db_tables: list[tuple[str, str]] | None = None,
    db_columns: list[tuple[str, str, str, str, str]] | None = None,
    catalog_tables: list[tuple[str, str, int]] | None = None,
    catalog_columns: list[tuple[str, str, str]] | None = None,
) -> FakeCursor:
    return FakeCursor(
        {
            "INFORMATION_SCHEMA.TABLES": db_tables or [],
            "INFORMATION_SCHEMA.COLUMNS": db_columns or [],
            "_metadata.catalog_tables": catalog_tables or [],
            "_metadata.catalog_columns": catalog_columns or [],
        }
    )


FROZEN_NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# compute_drift — pure-logic tests
# ---------------------------------------------------------------------------


def test_no_drift_is_ok() -> None:
    """Same set of tables & columns on both sides → ok=true, every bucket empty."""
    db_tables = [
        cmd._DbTable(schema_name="analytics", table_name="analytics.vw_PersonDetail"),
    ]
    db_columns = [
        cmd._DbColumn(
            table_name="analytics.vw_PersonDetail",
            column_name="agent_id",
            data_type="int",
            is_nullable=False,
        ),
    ]
    catalog_tables = [
        cmd._CatalogTable(
            schema_name="analytics",
            table_name="analytics.vw_PersonDetail",
            is_active=True,
        ),
    ]
    catalog_columns = [
        cmd._CatalogColumn(
            table_name="analytics.vw_PersonDetail",
            column_name="agent_id",
            data_type="INT",
        ),
    ]

    report = cmd.compute_drift(
        db_tables, db_columns, catalog_tables, catalog_columns, now=FROZEN_NOW
    )

    assert report["ok"] is True
    assert report["missing_from_catalog"] == {"tables": [], "columns": []}
    assert report["missing_from_database"] == {"tables": [], "columns": []}
    assert report["type_mismatches"] == []
    assert report["checked_at_utc"] == FROZEN_NOW.isoformat()


def test_missing_from_catalog_table_is_reported() -> None:
    db_tables = [
        cmd._DbTable(schema_name="wfm", table_name="wfm.brand_new_table"),
    ]
    report = cmd.compute_drift(db_tables, [], [], [], now=FROZEN_NOW)

    assert report["ok"] is False
    assert report["missing_from_catalog"]["tables"] == [
        {"schema_name": "wfm", "table_name": "wfm.brand_new_table"},
    ]


def test_missing_from_catalog_column_is_reported() -> None:
    """A column on an active catalog table but missing from the catalog
    must surface in ``missing_from_catalog.columns``."""
    db_tables = [
        cmd._DbTable(schema_name="analytics", table_name="analytics.vw_PersonDetail"),
    ]
    db_columns = [
        cmd._DbColumn(
            table_name="analytics.vw_PersonDetail",
            column_name="new_field",
            data_type="varchar",
            is_nullable=True,
        ),
    ]
    catalog_tables = [
        cmd._CatalogTable(
            schema_name="analytics",
            table_name="analytics.vw_PersonDetail",
            is_active=True,
        ),
    ]

    report = cmd.compute_drift(db_tables, db_columns, catalog_tables, [], now=FROZEN_NOW)

    assert report["ok"] is False
    assert report["missing_from_catalog"]["columns"] == [
        {
            "table_name": "analytics.vw_PersonDetail",
            "column_name": "new_field",
            "data_type": "varchar",
            "is_nullable": True,
        }
    ]


def test_missing_from_database_table_is_reported() -> None:
    catalog_tables = [
        cmd._CatalogTable(
            schema_name="analytics",
            table_name="analytics.vw_DroppedView",
            is_active=True,
        ),
    ]
    report = cmd.compute_drift([], [], catalog_tables, [], now=FROZEN_NOW)

    assert report["ok"] is False
    assert report["missing_from_database"]["tables"] == ["analytics.vw_DroppedView"]


def test_missing_from_database_column_is_reported() -> None:
    db_tables = [
        cmd._DbTable(schema_name="analytics", table_name="analytics.vw_PersonDetail"),
    ]
    catalog_tables = [
        cmd._CatalogTable(
            schema_name="analytics",
            table_name="analytics.vw_PersonDetail",
            is_active=True,
        ),
    ]
    catalog_columns = [
        cmd._CatalogColumn(
            table_name="analytics.vw_PersonDetail",
            column_name="renamed_column",
            data_type="INT",
        ),
    ]
    report = cmd.compute_drift(
        db_tables, [], catalog_tables, catalog_columns, now=FROZEN_NOW
    )

    assert report["ok"] is False
    assert report["missing_from_database"]["columns"] == [
        {
            "table_name": "analytics.vw_PersonDetail",
            "column_name": "renamed_column",
        }
    ]


def test_type_mismatch_is_reported() -> None:
    db_tables = [
        cmd._DbTable(schema_name="analytics", table_name="analytics.vw_PersonDetail"),
    ]
    db_columns = [
        cmd._DbColumn(
            table_name="analytics.vw_PersonDetail",
            column_name="agent_id",
            data_type="bigint",
            is_nullable=False,
        ),
    ]
    catalog_tables = [
        cmd._CatalogTable(
            schema_name="analytics",
            table_name="analytics.vw_PersonDetail",
            is_active=True,
        ),
    ]
    catalog_columns = [
        cmd._CatalogColumn(
            table_name="analytics.vw_PersonDetail",
            column_name="agent_id",
            data_type="INT",
        ),
    ]
    report = cmd.compute_drift(
        db_tables, db_columns, catalog_tables, catalog_columns, now=FROZEN_NOW
    )
    assert report["ok"] is False
    assert report["type_mismatches"] == [
        {
            "table_name": "analytics.vw_PersonDetail",
            "column_name": "agent_id",
            "catalog_type": "INT",
            "actual_type": "bigint",
        }
    ]


@pytest.mark.parametrize(
    ("catalog_type", "db_type"),
    [
        ("INT", "int"),
        ("NVARCHAR(150)", "nvarchar"),
        ("DECIMAL(4,2)", "decimal"),
        ("DATE", "DATE"),
    ],
)
def test_normalized_type_compare_matches(catalog_type: str, db_type: str) -> None:
    """Length / precision parens and case differences must NOT count as drift."""
    db_tables = [
        cmd._DbTable(schema_name="analytics", table_name="analytics.vw_PersonDetail"),
    ]
    db_columns = [
        cmd._DbColumn(
            table_name="analytics.vw_PersonDetail",
            column_name="col_x",
            data_type=db_type,
            is_nullable=False,
        ),
    ]
    catalog_tables = [
        cmd._CatalogTable(
            schema_name="analytics",
            table_name="analytics.vw_PersonDetail",
            is_active=True,
        ),
    ]
    catalog_columns = [
        cmd._CatalogColumn(
            table_name="analytics.vw_PersonDetail",
            column_name="col_x",
            data_type=catalog_type,
        ),
    ]
    report = cmd.compute_drift(
        db_tables, db_columns, catalog_tables, catalog_columns, now=FROZEN_NOW
    )
    assert report["type_mismatches"] == []
    assert report["ok"] is True


def test_inactive_catalog_table_does_not_contribute_drift_either_way() -> None:
    """An ``is_active=0`` catalog entry must not:
      - appear in missing_from_database when the DB lacks it, AND
      - cause its DB columns (if the table does exist) to flow into
        missing_from_catalog.columns / type_mismatches.
    """
    db_tables = [
        cmd._DbTable(schema_name="wfm", table_name="wfm.hidden_table"),
    ]
    db_columns = [
        cmd._DbColumn(
            table_name="wfm.hidden_table",
            column_name="anything",
            data_type="bigint",
            is_nullable=False,
        ),
    ]
    catalog_tables = [
        cmd._CatalogTable(
            schema_name="wfm",
            table_name="wfm.hidden_table",
            is_active=False,
        ),
        # ALSO a separate inactive catalog table that does not exist
        # in the DB. Must not be reported as missing_from_database.
        cmd._CatalogTable(
            schema_name="wfm",
            table_name="wfm.dropped_hidden",
            is_active=False,
        ),
    ]
    catalog_columns = [
        cmd._CatalogColumn(
            table_name="wfm.hidden_table",
            column_name="anything",
            data_type="INT",  # would mismatch if active
        ),
    ]
    report = cmd.compute_drift(
        db_tables, db_columns, catalog_tables, catalog_columns, now=FROZEN_NOW
    )
    assert report["ok"] is True
    assert report["missing_from_catalog"] == {"tables": [], "columns": []}
    assert report["missing_from_database"] == {"tables": [], "columns": []}
    assert report["type_mismatches"] == []


def test_output_is_sorted_and_deterministic() -> None:
    """Shuffling the inputs must not change the JSON output."""
    db_tables_unordered = [
        cmd._DbTable(schema_name="wfm", table_name="wfm.zzz_table"),
        cmd._DbTable(schema_name="absence", table_name="absence.aaa_table"),
    ]
    db_tables_sorted = list(reversed(db_tables_unordered))

    r1 = cmd.compute_drift(db_tables_unordered, [], [], [], now=FROZEN_NOW)
    r2 = cmd.compute_drift(db_tables_sorted, [], [], [], now=FROZEN_NOW)

    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    # Confirm actual sort order on the tables bucket.
    assert [t["table_name"] for t in r1["missing_from_catalog"]["tables"]] == [
        "absence.aaa_table",
        "wfm.zzz_table",
    ]


def test_default_now_uses_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``now`` is not provided, current UTC time is used."""
    report = cmd.compute_drift([], [], [], [], now=None)
    # Round-trip parse to ensure ISO format with timezone.
    parsed = datetime.fromisoformat(report["checked_at_utc"])
    assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# Fetchers — filter system schemas, parse YES/NO into bool.
# ---------------------------------------------------------------------------


def test_fetch_db_tables_excludes_system_schemas() -> None:
    cursor = _make_cursor(
        db_tables=[
            ("sys", "tables"),
            ("INFORMATION_SCHEMA", "TABLES"),
            ("_metadata", "catalog_tables"),
            ("analytics", "vw_PersonDetail"),
            ("dbo", "leftover"),
        ]
    )
    result = cmd._fetch_db_tables(cursor)
    names = {t.table_name for t in result}
    assert names == {"analytics.vw_PersonDetail", "dbo.leftover"}


def test_fetch_db_columns_parses_is_nullable() -> None:
    cursor = _make_cursor(
        db_columns=[
            ("analytics", "vw_PersonDetail", "agent_id", "int", "NO"),
            ("analytics", "vw_PersonDetail", "queue", "nvarchar", "YES"),
            ("sys", "anything", "x", "int", "NO"),  # filtered
        ]
    )
    result = cmd._fetch_db_columns(cursor)
    assert len(result) == 2
    by_col = {c.column_name: c for c in result}
    assert by_col["agent_id"].is_nullable is False
    assert by_col["queue"].is_nullable is True


def test_fetch_catalog_tables_parses_is_active() -> None:
    cursor = _make_cursor(
        catalog_tables=[
            ("analytics.vw_X", "analytics", 1),
            ("analytics.vw_Y", "analytics", 0),
        ]
    )
    result = cmd._fetch_catalog_tables(cursor)
    by_name = {t.table_name: t for t in result}
    assert by_name["analytics.vw_X"].is_active is True
    assert by_name["analytics.vw_Y"].is_active is False


def test_fetch_catalog_columns_passes_through() -> None:
    cursor = _make_cursor(
        catalog_columns=[
            ("analytics.vw_X", "col_a", "INT"),
            ("analytics.vw_X", "col_b", "NVARCHAR(50)"),
        ]
    )
    result = cmd._fetch_catalog_columns(cursor)
    assert len(result) == 2
    assert result[0].data_type == "INT"
    assert result[1].data_type == "NVARCHAR(50)"


# ---------------------------------------------------------------------------
# collect_drift_from_connection — exercises the cursor close path.
# ---------------------------------------------------------------------------


def test_collect_drift_from_connection_closes_cursor() -> None:
    cursor = _make_cursor()
    connection = FakeConnection(cursor)
    report = cmd.collect_drift_from_connection(connection, now=FROZEN_NOW)
    assert report["ok"] is True
    assert cursor.closed is True


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------


def _patch_main_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    cursor: FakeCursor,
) -> FakeConnection:
    """Wire env vars + ``_connect`` so ``main()`` runs against a fake."""
    monkeypatch.setenv("DB_SERVER", "fake.database.windows.net")
    monkeypatch.setenv("DB_DATABASE", "fake_db")
    monkeypatch.delenv("DB_MANAGED_IDENTITY_CLIENT_ID", raising=False)
    fake_conn = FakeConnection(cursor)
    monkeypatch.setattr(
        cmd,
        "_connect",
        lambda **_kwargs: fake_conn,
    )
    return fake_conn


def test_main_no_drift_returns_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cursor = _make_cursor()  # everything empty → ok
    _patch_main_dependencies(monkeypatch, cursor)

    code = cmd.main([])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["ok"] is True
    assert captured.err == ""


def test_main_drift_returns_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cursor = _make_cursor(
        db_tables=[("wfm", "drift_table")],
    )
    _patch_main_dependencies(monkeypatch, cursor)

    code = cmd.main([])
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["ok"] is False
    assert payload["missing_from_catalog"]["tables"] == [
        {"schema_name": "wfm", "table_name": "wfm.drift_table"}
    ]


def test_main_allow_drift_forces_exit_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cursor = _make_cursor(db_tables=[("wfm", "drift_table")])
    _patch_main_dependencies(monkeypatch, cursor)

    code = cmd.main(["--allow-drift"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["ok"] is False  # report still reflects reality


def test_main_summary_writes_to_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cursor = _make_cursor(db_tables=[("wfm", "drift_table")])
    _patch_main_dependencies(monkeypatch, cursor)

    code = cmd.main(["--summary"])
    captured = capsys.readouterr()

    assert code == 1
    assert "Metadata drift check" in captured.err
    assert "missing_from_catalog.tables: 1" in captured.err
    # JSON still on stdout.
    json.loads(captured.out)


def test_main_missing_env_returns_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DB_SERVER", raising=False)
    monkeypatch.delenv("DB_DATABASE", raising=False)
    code = cmd.main([])
    captured = capsys.readouterr()
    assert code == 2
    assert "DB_SERVER" in captured.err
    assert captured.out == ""


def test_main_json_output_is_sorted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cursor = _make_cursor(
        db_tables=[("wfm", "zz"), ("absence", "aa")],
    )
    _patch_main_dependencies(monkeypatch, cursor)
    cmd.main([])
    raw = capsys.readouterr().out
    # ``json.dumps(..., sort_keys=True)`` puts ``checked_at_utc`` first.
    assert raw.lstrip().startswith("{\n  \"checked_at_utc\":")


def test_main_closes_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cursor = _make_cursor()
    fake_conn = _patch_main_dependencies(monkeypatch, cursor)
    cmd.main([])
    capsys.readouterr()
    assert fake_conn.closed is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_build_token_struct_round_trips() -> None:
    """The packed bytes must start with a little-endian length prefix
    matching the UTF-16-LE encoding of the token."""
    token = "abc"  # 3 chars → 6 UTF-16-LE bytes
    packed = cmd._build_token_struct(token)
    # First 4 bytes = length-prefix (little-endian uint32).
    length = int.from_bytes(packed[:4], "little")
    assert length == 6
    assert packed[4:] == token.encode("utf-16-le")


def test_normalize_type_strips_parens_and_lowercases() -> None:
    assert cmd._normalize_type("NVARCHAR(150)") == "nvarchar"
    assert cmd._normalize_type("DECIMAL(4,2)") == "decimal"
    assert cmd._normalize_type(" Int ") == "int"


def test_format_summary_truncates_long_lists() -> None:
    """More than 5 entries per bucket → ``… and N more`` suffix."""
    report = {
        "missing_from_catalog": {
            "tables": [{"x": i} for i in range(10)],
            "columns": [],
        },
        "missing_from_database": {"tables": [], "columns": []},
        "type_mismatches": [],
        "ok": False,
        "checked_at_utc": "2026-05-29T00:00:00+00:00",
    }
    out = cmd._format_summary(report)
    assert "and 5 more" in out
