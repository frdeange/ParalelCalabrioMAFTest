# MCP — apps/mcp

> FastMCP server with schema introspection and query execution tools. Container App #3.

📖 See [PLAN.md §6.3](../../PLAN.md#63-mcp-appsmcp) and [PLAN.md §8](../../PLAN.md#8-mcp-design).

## Status

**Phase 0** — empty skeleton. Implementation in **Phase 2**.

## Day-1 tools (5)

| Namespace | Tool |
|-----------|------|
| `schema` | `list_tables` |
| `schema` | `search_tables` |
| `schema` | `describe_table` |
| `schema` | `get_distinct_values` |
| `query`  | `execute` (read-only, `bu_id` enforced) |

## Planned structure

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
├── bootstrap_metadata.py   # create _metadata schema
└── seed_extended_properties.py
tests/
Dockerfile
pyproject.toml
.env.example
```

## Run (once it exists)

```bash
cd apps/mcp
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.server:app --port 8001
```

## Environment variables

See [PLAN.md §14 MCP](../../PLAN.md#14-environment-variables-inventory).
