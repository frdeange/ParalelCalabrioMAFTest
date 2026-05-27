# Database — Calabrio WFM schema

> Scripts T-SQL para Azure SQL Database. Schema + views + seed + extended properties.

## Archivos

| Archivo | Propósito |
|---------|-----------|
| `01-schemas-and-tables.sql` | Crea schemas `wfm`, `absence`, `overtime`, `scheduling`, `_metadata` + tablas core |
| `02-views.sql` | Views derivadas |
| `03-seed-data.sql` | Datos demo: 1 BU `CWFM-DEMO`, 3 sites, 50 agents |
| `04-grant-readonly.sql` | Rol custom `wfm_reader` y grants |
| `05-metadata-schema.sql` | **(Phase 2)** Tablas `_metadata.agent_allowlist` y `_metadata.tool_audit` |
| `06-extended-properties.sql` | **(Phase 2)** `sp_addextendedproperty` MS_Description para tablas/columnas visibles |

## Orden de ejecución

```bash
# en orden numérico
sqlcmd -S <server>.database.windows.net -d calabriowfm -G -i database/01-schemas-and-tables.sql
sqlcmd -S <server>.database.windows.net -d calabriowfm -G -i database/02-views.sql
sqlcmd -S <server>.database.windows.net -d calabriowfm -G -i database/03-seed-data.sql
sqlcmd -S <server>.database.windows.net -d calabriowfm -G -i database/04-grant-readonly.sql
# 05 y 06 cuando los creemos en Phase 2
```

## Estrategia de schema

Ver [PLAN.md §9](../PLAN.md#9-estrategia-de-schema-db--llm).
