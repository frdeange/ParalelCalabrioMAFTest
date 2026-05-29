# ADR-0002 — Schema metadata strategy: `_metadata.catalog_*` tables (not `sys.extended_properties`)

**Status**: Accepted
**Date**: 2026-05-29
**Decider**: Project owner
**Related**: ADR-0001 (overall architecture), [PLAN.md §9](../../PLAN.md#9-schema-strategy-db--llm), [apps/mcp/app/servers/schema.py](../../apps/mcp/app/servers/schema.py), [database/01-schemas-and-tables.sql](../../database/01-schemas-and-tables.sql), [database/03-seed-data.sql](../../database/03-seed-data.sql), [database/scripts/check_metadata_drift.py](../../database/scripts/check_metadata_drift.py)

> **Filename note**: kept as `ADR-0002-schema-extended-properties.md` to preserve the stable reference that PLAN.md already pins. The decision itself is the **opposite** — we did **not** adopt `sp_addextendedproperty` / `sys.extended_properties`.

---

## Context

The MCP server (Phase 2) needs a machine-readable description of the WFM schema to feed the LLM via `schema.list_tables` / `schema.search_tables` / `schema.describe_table` / `schema.get_distinct_values`. Two persistence options surfaced during the planning iteration:

1. **SQL Server native — `sys.extended_properties`** (annotations attached via `sp_addextendedproperty`, name = `MS_Description`). The "obvious" T-SQL way.
2. **First-class catalog tables in a dedicated `_metadata` schema** (`catalog_tables`, `catalog_columns`, `catalog_joins`), seeded from the same `database/*.sql` migration scripts as the rest of the model.

The choice has downstream consequences for the MCP tools' read path, the seed/migration workflow, the read-only grant policy on `uai_readonly`, and the ability to extend metadata with WFM-specific fields the LLM finds useful (keywords for fuzzy search, display names, visibility flags) that extended properties do not naturally support.

## Decision

We adopt **option 2**: the catalog of LLM-facing metadata lives in three relational tables under the `_metadata` schema, with `_metadata.tool_audit` as a fourth append-only sibling for call tracing.

```
_metadata.catalog_tables   -- table-level description + keywords + is_active flag
_metadata.catalog_columns  -- column-level description + display_name
_metadata.catalog_joins    -- declared join paths between tables/views
_metadata.tool_audit       -- per-call audit trail (append-only)
```

DDL lives in [database/01-schemas-and-tables.sql](../../database/01-schemas-and-tables.sql); seed in [database/03-seed-data.sql](../../database/03-seed-data.sql); read-only `SELECT` grant on the schema lives in [database/04-grant-readonly.sql](../../database/04-grant-readonly.sql) (plus a narrow `INSERT` on `tool_audit` for the MCP service principal). `sys.extended_properties` is **not** populated by us — we only consult it from the drift checker, in the reverse direction.

To keep the catalog honest, [database/scripts/check_metadata_drift.py](../../database/scripts/check_metadata_drift.py) runs as a dedicated step in [.github/workflows/mcp-ci.yml](../../.github/workflows/mcp-ci.yml) against the same `mcr.microsoft.com/mssql/server:2022-latest` testcontainer the integration suite uses. The checker compares `_metadata.catalog_columns` against `INFORMATION_SCHEMA.COLUMNS`, fails the build on any of: column documented but missing in live schema, column present in live schema but undocumented, type mismatch, nullability mismatch. The result is an actionable diff in the CI log.

## Positive consequences

- **Inspectability** — `SELECT * FROM _metadata.catalog_tables` instead of three-way joins on `sys.tables` + `sys.columns` + `sys.extended_properties` filtered by `name = N'MS_Description'`.
- **Editability** — plain `UPDATE` / `MERGE` statements instead of `sp_updateextendedproperty` ceremony, one property name per call.
- **Versioning** — the metadata travels in the same SQL files (`01-schemas-and-tables.sql`, `03-seed-data.sql`) as the schema it describes; one PR captures both.
- **Bonus columns** — `keywords` (powers `schema.search_tables` fuzzy match) and `display_name` (friendlier UI labels) have no natural home in extended properties.
- **RBAC simplicity** — `uai_readonly` only needs `SELECT ON SCHEMA::[_metadata]` plus `INSERT` on `tool_audit`; no `VIEW DEFINITION` or system-view grants required.
- **Drift gate** — the catalog cannot silently desync from the live schema; CI fails fast with a clear diff.

## Negative consequences

- **Two sources of truth on paper** — `INFORMATION_SCHEMA` (live) vs `_metadata.catalog_*` (curated). Mitigated by the CI drift gate, which is fast (~6-10s warm) and runs on every PR touching `database/**` or `apps/mcp/**`.
- **Manual seed maintenance** — adding a column to a table also means adding a row to `catalog_columns`. The drift checker turns this from a forgotten footgun into a build failure with line-level guidance.
- **Walking away from a SQL Server idiomatic feature** — anyone familiar with `sp_addextendedproperty` will pause when they look at our schema. Mitigated by this ADR and PLAN.md §9.

## Alternatives considered

### A. `sys.extended_properties` (Approach C in the original draft)

The "obvious" T-SQL native option. Discarded because every advantage we wanted from it (inspectability, editability, versioning, drift detection) was either non-existent or required wrapping ceremony so heavy that the win evaporated. Crucially, neither `keywords` nor `display_name` map to a single extended property without an ad-hoc naming convention (`MS_Description`, `MCP_Keywords`, `MCP_DisplayName`, ...) that we would then have to police anyway — a re-invention of catalog tables, but worse.

### B. Friendly-name aliases (re-name layer)

Map "WFM persons view" to `analytics.vw_PersonDetail` at the MCP edge so the LLM never sees the technical name. Discarded under decision **D9** in PLAN.md: modern LLMs read SQL identifiers fine and the alias layer would introduce a second naming taxonomy to maintain. The catalog tables expose the real names + descriptions and the LLM is asked to reason about them directly.

### C. YAML/JSON files checked into the repo (no SQL at all)

Discarded because the MCP server would then need a file-loader path in addition to its SQL client, the drift checker would compare files-vs-DB (more fragile than DB-vs-DB), and we would still need _some_ table for `tool_audit`. Keeping all metadata in SQL means one connection, one transactional read path, one CI gate.

## Implementation status

- [x] `_metadata.catalog_tables` / `catalog_columns` / `catalog_joins` / `tool_audit` DDL — Phase 2 #16
- [x] Seed data for the WFM views shipped today — Phase 2 #21 (database/03-seed-data.sql)
- [x] MCP `schema.*` tools read from these tables — Phase 2 #18
- [x] `query.execute` writes to `tool_audit` — Phase 2 #20
- [x] CI drift gate — Phase 2 #22 ([database/scripts/check_metadata_drift.py](../../database/scripts/check_metadata_drift.py), [.github/workflows/mcp-ci.yml](../../.github/workflows/mcp-ci.yml))

## References

- [PLAN.md §9 — Schema strategy (DB ↔ LLM)](../../PLAN.md#9-schema-strategy-db--llm)
- [apps/mcp/README.md](../../apps/mcp/README.md)
- [docs/mcp-tool-catalog.md](../../docs/mcp-tool-catalog.md)
- [database/README.md](../../database/README.md)
