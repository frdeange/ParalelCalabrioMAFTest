# MCP — apps/mcp

> FastMCP server con tools de schema introspection y query execution. Container App #3.

📖 Ver [PLAN.md §6.3](../../PLAN.md#63-mcp-appsmcp) y [PLAN.md §8](../../PLAN.md#8-diseño-del-mcp).

## Estado

**Phase 0** — esqueleto vacío. Implementación en **Phase 2**.

## Tools día 1 (5)

| Namespace | Tool |
|-----------|------|
| `schema` | `list_tables` |
| `schema` | `search_tables` |
| `schema` | `describe_table` |
| `schema` | `get_distinct_values` |
| `query`  | `execute` (read-only, `bu_id` forzado) |

## Estructura prevista

```
app/
├── server.py            # FastMCP + mount(prefix=...)
├── schema_tools.py
├── query_tools.py
├── sql_client.py        # Entra-auth + KV fallback
├── validator.py         # sqlglot AST validator
├── identity.py          # verify HMAC + x-bu-id
└── settings.py
scripts/
├── bootstrap_metadata.py   # crea _metadata schema
└── seed_extended_properties.py
tests/
Dockerfile
pyproject.toml
.env.example
```

## Run (cuando exista)

```bash
cd apps/mcp
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.server:app --port 8001
```

## Variables de entorno

Ver [PLAN.md §14 MCP](../../PLAN.md#14-inventario-de-variables-de-entorno).
