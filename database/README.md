# Database — Calabrio WFM schema

> T-SQL scripts for Azure SQL Database. Schema + views + seed + extended properties.

## Files

| File | Purpose |
|------|---------|
| `01-schemas-and-tables.sql` | Creates schemas `wfm`, `absence`, `overtime`, `scheduling`, `_metadata` + core tables |
| `02-views.sql` | Derived views |
| `03-seed-data.sql` | Demo data: 1 BU `CWFM-DEMO`, 3 sites, 50 agents |
| `04-grant-readonly.sql` | Custom role `wfm_reader` and grants |
| `scripts/check_metadata_drift.py` | Read-only drift checker between `_metadata.catalog_*` and `INFORMATION_SCHEMA` (issue #21) |

## Execution order

```bash
# in numeric order
sqlcmd -S <server>.database.windows.net -d calabriowfm -G -i database/01-schemas-and-tables.sql
sqlcmd -S <server>.database.windows.net -d calabriowfm -G -i database/02-views.sql
sqlcmd -S <server>.database.windows.net -d calabriowfm -G -i database/03-seed-data.sql
sqlcmd -S <server>.database.windows.net -d calabriowfm -G -i database/04-grant-readonly.sql
```

## Schema strategy

See [PLAN.md §9](../PLAN.md#9-schema-strategy-db--llm). Note Decision
D9: the catalog lives in plain `_metadata.catalog_*` tables, **not**
in `sp_addextendedproperty` / `sys.extended_properties`, and there is
no separate `_metadata.agent_allowlist` — `catalog_tables.is_active`
is the visibility gate.

## Drift check

`database/scripts/check_metadata_drift.py` compares the catalog
(`_metadata.catalog_tables`, `_metadata.catalog_columns`) against the
live database (`INFORMATION_SCHEMA.TABLES`, `INFORMATION_SCHEMA.COLUMNS`)
and emits a deterministic JSON drift report. The script is **read-only**
— it never calls `INSERT`, DDL, or `sp_addextendedproperty` — so
re-running it is trivially idempotent (issue #21 acceptance criterion 1).

### Environment

| Variable | Required | Notes |
|----------|----------|-------|
| `DB_SERVER` | yes | Fully qualified host, e.g. `calabriomafpoc-sql.database.windows.net` |
| `DB_DATABASE` | yes | Database name |
| `DB_MANAGED_IDENTITY_CLIENT_ID` | no | Pin a specific UAMI when several are attached |

Auth is Entra-only via `DefaultAzureCredential` (same lockdown as
`apps/mcp/app/clients/sql.py`: no `EnvironmentCredential`, no
interactive flows; local devs use `az login`, CI / Azure use Managed
Identity). No password / connection-string path exists.

### Run locally

```bash
cd database/scripts
pip install -e .[dev]
export DB_SERVER=...
export DB_DATABASE=calabriowfm
python check_metadata_drift.py --summary       # JSON to stdout + human summary to stderr
python check_metadata_drift.py --allow-drift   # exit 0 even if drift is detected
```

### JSON shape

```json
{
  "checked_at_utc": "2026-05-29T12:00:00+00:00",
  "missing_from_catalog": {
    "tables":  [{"schema_name": "wfm", "table_name": "wfm.new_table"}],
    "columns": [{"table_name": "...", "column_name": "...", "data_type": "...", "is_nullable": true}]
  },
  "missing_from_database": {
    "tables":  ["analytics.vw_DroppedView"],
    "columns": [{"table_name": "...", "column_name": "..."}]
  },
  "type_mismatches": [
    {"table_name": "...", "column_name": "...", "catalog_type": "INT", "actual_type": "bigint"}
  ],
  "ok": false
}
```

All lists are sorted; `ok` is `true` iff every bucket is empty. Only
catalog rows with `is_active = 1` participate in the column / missing-from-DB
comparisons — inactive tables are intentional and never count as drift.

### Exit codes (for CI, see #22)

| Code | Meaning |
|------|---------|
| `0` | No drift (or `--allow-drift` set) |
| `1` | Drift detected |
| `2` | Bad environment (`DB_SERVER` / `DB_DATABASE` missing) |
