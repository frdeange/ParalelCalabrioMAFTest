"""Drift check against the bootstrapped testcontainer (issue #22).

Uses the same session-scoped mssql container the schema/query tests
spin up, opens a raw ``pyodbc`` connection on it (the drift script's
``_connect`` is Entra-only and unusable inside the container) and
calls :func:`check_metadata_drift.collect_drift_from_connection`
against the seeded catalog.

Schema scope
------------
The catalog (``database/03-seed-data.sql``) only describes
``analytics.vw_*`` views \u2014 the underlying ``wfm.*`` / ``absence.*``
base tables are deliberately *not* in the catalog because the MCP
server only ever queries the analytics surface. Without scoping,
the drift checker would flag every base table as ``missing_from_catalog``
on a baseline run. The test therefore hard-codes
``include_schemas={"analytics"}`` when calling
:func:`check_metadata_drift.collect_drift_from_connection`. The
equivalent operator/CI seam is ``--include-schema analytics`` (or the
``DRIFT_CHECK_INCLUDE_SCHEMAS`` env var), but those parse in
:func:`check_metadata_drift.main` and are not exercised here; the
workflow runs this test with only ``MCP_RUN_INTEGRATION=1`` set.

Two scenarios are exercised:

* **Baseline** \u2014 the schema files in ``database/01-03*.sql`` and the
  catalog seeded in the same scripts agree, so the drift report
  must come back ``ok=True``. This is the gate the CI step
  enforces: a fresh checkout against a fresh container must always
  pass.
* **Detection** \u2014 a fake catalog row is inserted, the report must
  flip to ``ok=False`` and surface the orphan in
  ``missing_from_database.columns``. Proves the script actually
  notices the drift rather than always returning clean.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyodbc
import pytest

# The drift script lives under ``database/scripts/`` and is published
# as its own ``wfm-drift-check`` distribution. We don't want to
# couple ``apps/mcp`` to that package via an install dep just for one
# integration test, so we put the script's directory on ``sys.path``
# and import the module directly. The script is a single-file module
# with no relative imports, so this is safe.
_DRIFT_SCRIPTS_DIR = Path(__file__).resolve().parents[4] / "database" / "scripts"
if str(_DRIFT_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_DRIFT_SCRIPTS_DIR))

import check_metadata_drift  # noqa: E402  (path-injected import)

from .conftest import _ConnInfo  # noqa: E402

pytestmark = pytest.mark.integration

# Catalog covers only the curated ``analytics.vw_*`` surface; see the
# module docstring for why this scope is the right one to enforce.
_INCLUDE_SCHEMAS = frozenset({"analytics"})


@pytest.fixture
def _drift_conn(_mssql_container: _ConnInfo) -> Iterator[pyodbc.Connection]:
    """Open a raw pyodbc connection for the drift checker to read from.

    The drift script consumes any object satisfying its ``_Connection``
    protocol (``cursor()`` \u2192 protocol cursor with ``execute`` /
    ``fetchall`` / ``description``). A vanilla ``pyodbc.Connection``
    satisfies that.

    Per-test scope so failures in one test cannot leak a half-closed
    cursor into the next.
    """
    conn = pyodbc.connect(_mssql_container.odbc_conn_str(), autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def test_drift_check_baseline_is_clean(_drift_conn: pyodbc.Connection) -> None:
    """The seeded catalog must agree with the seeded schema on a fresh container.

    This is the gate the CI step ("Drift check (mssql testcontainer)")
    enforces on every PR: if a schema migration lands without a
    matching catalog bump (or vice versa), this assertion fails and
    the build is red until ``database/03-seed-data.sql`` or the
    matching DDL in ``database/01-schemas-and-tables.sql`` /
    ``database/02-views.sql`` is updated.
    """
    report = check_metadata_drift.collect_drift_from_connection(
        _drift_conn, include_schemas=_INCLUDE_SCHEMAS
    )

    assert report["ok"] is True, (
        f"baseline drift detected; report={report!r}. "
        "Either the catalog seed in database/03-seed-data.sql or the "
        "schema in database/01-02*.sql moved without the other being updated."
    )
    # Sanity: the report shape carries the documented buckets even when clean.
    for bucket in ("missing_from_catalog", "missing_from_database"):
        assert report[bucket]["tables"] == []
        assert report[bucket]["columns"] == []
    assert report["type_mismatches"] == []


def test_drift_check_detects_orphan_catalog_column(
    _drift_conn: pyodbc.Connection,
) -> None:
    """An invented catalog column that the live DB doesn't have flips ``ok=False``.

    Proves the script is actually doing the comparison rather than
    always returning clean. We poke a fake row directly into
    ``_metadata.catalog_columns`` (pointing at an active catalog
    table within the ``analytics`` scope so the drift logic considers
    it), assert the report flips, and then delete the row to leave
    the catalog as the next test finds it.
    """
    fake_column = "this_column_does_not_exist_in_the_view"
    fake_table = "analytics.vw_PersonDetail"

    with _drift_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO _metadata.catalog_columns "
            "(table_name, column_name, data_type, is_nullable, description, display_name) "
            "VALUES (?, ?, N'INT', CAST(0 AS BIT), N'Synthetic drift', N'Synthetic')",
            (fake_table, fake_column),
        )
    try:
        report = check_metadata_drift.collect_drift_from_connection(
            _drift_conn, include_schemas=_INCLUDE_SCHEMAS
        )

        assert report["ok"] is False, (
            f"drift checker missed an orphan catalog column; report={report!r}"
        )
        orphans = report["missing_from_database"]["columns"]
        match: dict[str, Any] | None = next(
            (
                entry
                for entry in orphans
                if entry.get("table_name") == fake_table
                and entry.get("column_name") == fake_column
            ),
            None,
        )
        assert match is not None, (
            "the synthetic orphan column was not surfaced in "
            f"missing_from_database.columns; got {orphans!r}"
        )
    finally:
        with _drift_conn.cursor() as cur:
            cur.execute(
                "DELETE FROM _metadata.catalog_columns "
                "WHERE table_name = ? AND column_name = ?",
                (fake_table, fake_column),
            )
