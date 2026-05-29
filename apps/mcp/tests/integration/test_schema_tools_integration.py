"""Integration tests for the ``schema`` namespace tools (issue #23).

Each of the four agent-facing tools gets a positive (happy path
against the bootstrapped catalog) and a negative (boundary / failure)
case. The ``ping`` placeholder is exercised by the unit tests; this
suite only covers the four catalog-backed tools that actually touch
the database.

The ``_wire_tools_to_container`` autouse fixture in
``conftest.py`` already installs the SA-auth client into
``app.servers.schema`` before each test runs, so the tools call the
real engine without any mock-patching here.
"""

from __future__ import annotations

import pytest

from app.servers.schema import (
    describe_table,
    get_distinct_values,
    list_tables,
    search_tables,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# list_tables
# ---------------------------------------------------------------------------


async def test_list_tables_returns_seeded_active_views() -> None:
    """All four ``analytics.*`` views from ``03-seed-data.sql`` are listed."""
    result = await list_tables()

    assert result["count"] == 4
    names = {row["table_name"] for row in result["tables"]}
    assert names == {
        "analytics.vw_PersonDetail",
        "analytics.vw_AbsenceRequest",
        "analytics.vw_OvertimeRequest",
        "analytics.vw_Scheduling",
    }
    # Order must be deterministic (``ORDER BY table_name`` in the tool's SQL).
    table_names_in_order = [row["table_name"] for row in result["tables"]]
    assert table_names_in_order == sorted(table_names_in_order)


async def test_list_tables_hides_inactive_rows(_integration_client) -> None:  # type: ignore[no-untyped-def]
    """``is_active = 0`` rows must not be returned (PLAN.md \u00a79 D9 visibility gate)."""
    # Temporarily mark one view inactive, exercise the tool, then
    # restore the flag so subsequent tests see the seeded baseline.
    await _integration_client.execute(
        "UPDATE _metadata.catalog_tables SET is_active = 0 "
        "WHERE table_name = ?",
        ("analytics.vw_Scheduling",),
    )
    try:
        result = await list_tables()
        names = {row["table_name"] for row in result["tables"]}
        assert result["count"] == 3
        assert "analytics.vw_Scheduling" not in names
    finally:
        await _integration_client.execute(
            "UPDATE _metadata.catalog_tables SET is_active = 1 "
            "WHERE table_name = ?",
            ("analytics.vw_Scheduling",),
        )


# ---------------------------------------------------------------------------
# search_tables
# ---------------------------------------------------------------------------


async def test_search_tables_finds_by_name_substring() -> None:
    """A name-substring match returns the right view, ranked first."""
    result = await search_tables("person")

    assert result["count"] >= 1
    first = result["tables"][0]
    # Name matches sort before keyword/description matches per the
    # tool's ``ORDER BY CASE WHEN LOWER(table_name) LIKE ?``.
    assert first["table_name"] == "analytics.vw_PersonDetail"


async def test_search_tables_returns_empty_when_no_match() -> None:
    """A search term with no hits returns an empty list, not an error."""
    result = await search_tables("zzz_no_such_thing_zzz")

    assert result == {
        "query": "zzz_no_such_thing_zzz",
        "tables": [],
        "count": 0,
    }


# ---------------------------------------------------------------------------
# describe_table
# ---------------------------------------------------------------------------


async def test_describe_table_returns_columns_and_joins() -> None:
    """An active catalog table yields its full header + columns + joins."""
    result = await describe_table("analytics.vw_PersonDetail")

    assert result["table_name"] == "analytics.vw_PersonDetail"
    assert result["schema_name"] == "analytics"
    assert isinstance(result["columns"], list)
    assert result["columns"], "expected catalog_columns rows for vw_PersonDetail"
    assert "agent_id" in {col["column_name"] for col in result["columns"]}
    # ``joins`` may be empty for some views; just assert it is the right shape.
    assert isinstance(result["joins"], list)


async def test_describe_table_rejects_unknown_catalog_entry() -> None:
    """A table that isn't in ``_metadata.catalog_tables`` raises ``ValueError``."""
    with pytest.raises(ValueError, match="unknown or inactive"):
        await describe_table("analytics.vw_DoesNotExist")


# ---------------------------------------------------------------------------
# get_distinct_values
# ---------------------------------------------------------------------------


async def test_get_distinct_values_returns_seeded_categoricals() -> None:
    """A real categorical column returns its distinct seeded values."""
    result = await get_distinct_values(
        table_name="analytics.vw_PersonDetail",
        column_name="site_name",
        top_n=10,
    )

    # ``03-seed-data.sql`` seeds three sites: Stockholm / London / Madrid.
    assert set(result["values"]) >= {"Stockholm", "London", "Madrid"}
    assert result["truncated"] is False


async def test_get_distinct_values_rejects_uncatalogued_column() -> None:
    """A ``(table, column)`` pair that's not in the catalog is rejected."""
    with pytest.raises(ValueError, match="unknown or inactive catalog entry"):
        await get_distinct_values(
            table_name="analytics.vw_PersonDetail",
            column_name="this_column_does_not_exist",
            top_n=10,
        )
