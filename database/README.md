# Database — Calabrio WFM schema

> T-SQL scripts for Azure SQL Database. Schema + views + seed + extended properties.

## Files

| File | Purpose |
|------|---------|
| `01-schemas-and-tables.sql` | Creates schemas `wfm`, `absence`, `overtime`, `scheduling`, `_metadata` + core tables |
| `02-views.sql` | Derived views |
| `03-seed-data.sql` | Demo data: 1 BU `CWFM-DEMO`, 3 sites, 50 agents |
| `04-grant-readonly.sql` | Custom role `wfm_reader` and grants |
| `05-metadata-schema.sql` | **(Phase 2)** Tables `_metadata.agent_allowlist` and `_metadata.tool_audit` |
| `06-extended-properties.sql` | **(Phase 2)** `sp_addextendedproperty` MS_Description for visible tables/columns |

## Execution order

```bash
# in numeric order
sqlcmd -S <server>.database.windows.net -d calabriowfm -G -i database/01-schemas-and-tables.sql
sqlcmd -S <server>.database.windows.net -d calabriowfm -G -i database/02-views.sql
sqlcmd -S <server>.database.windows.net -d calabriowfm -G -i database/03-seed-data.sql
sqlcmd -S <server>.database.windows.net -d calabriowfm -G -i database/04-grant-readonly.sql
# 05 and 06 once they are created in Phase 2
```

## Schema strategy

See [PLAN.md §9](../PLAN.md#9-schema-strategy-db--llm).
