# ADR-0004 — MCP namespacing via FastMCP `mount(prefix=...)`

**Status**: Accepted
**Date**: 2026-05-29
**Decider**: Project owner
**Related**: ADR-0001 (overall architecture), [PLAN.md §8](../../PLAN.md#8-mcp-design), [apps/mcp/app/main.py](../../apps/mcp/app/main.py), [apps/mcp/app/servers/schema.py](../../apps/mcp/app/servers/schema.py), [apps/mcp/app/servers/query.py](../../apps/mcp/app/servers/query.py)

---

## Context

The Day-1 MCP exposes **five tools** split across two semantic domains:

- **Schema discovery** — `list_tables`, `search_tables`, `describe_table`, `get_distinct_values`
- **Query execution** — `execute`

We need a wire-level naming convention that (a) keeps tool names short and self-explanatory for the LLM, (b) leaves room for Day-2 domains (`forecast.*`, `analytics.*`) without renaming anything Day-1 ships, and (c) does not require running multiple processes or duplicating the FastMCP transport/auth wiring.

FastMCP 3.x exposes a first-class `mount(server, prefix="...")` API exactly for this. The decision is whether to use it or pick a flatter scheme.

## Decision

We adopt **one root `FastMCP` instance** in [apps/mcp/app/main.py](../../apps/mcp/app/main.py) that mounts two sub-servers, one per domain, with `prefix` namespacing:

```python
# apps/mcp/app/main.py (representative shape)
from fastmcp import FastMCP
from app.servers.schema import schema_app
from app.servers.query import query_app

app = FastMCP("calabrio-mcp")
app.mount(schema_app, prefix="schema")
app.mount(query_app, prefix="query")
```

The wire-facing tool names become:

| Sub-server | Tool function | Exposed name |
|---|---|---|
| `schema` | `list_tables` | `schema.list_tables` |
| `schema` | `search_tables` | `schema.search_tables` |
| `schema` | `describe_table` | `schema.describe_table` |
| `schema` | `get_distinct_values` | `schema.get_distinct_values` |
| `query` | `execute` | `query.execute` |
| `_root` | `ping` | `ping` (heartbeat — see [apps/mcp/README.md](../../apps/mcp/README.md)) |

Each sub-server is a self-contained `FastMCP` instance in its own module under [apps/mcp/app/servers/](../../apps/mcp/app/servers/), with its own `set_sql_client(...)` injection point so tests can swap in a mock per-domain without touching the other sub-server. The root `app` is what gets `streamable-http`-served by uvicorn.

Day-2 namespaces (`forecast.*`, `analytics.*`) are added by dropping a new file under `app/servers/` and one extra `app.mount(...)` call in `app/main.py` — no client-visible rename of existing tools.

## Positive consequences

- **Self-describing names** — the LLM sees `schema.describe_table` and immediately understands "this is the schema-discovery family". With ~30 tools in a future Day-2 catalog this saves the LLM (and reviewers) from playing prefix-detective with `t_schema_describe` or `mcp_schema_describe`.
- **Single process, single transport** — one `streamable-http` endpoint, one auth layer, one observability hook. Per-domain isolation lives in module boundaries, not network boundaries.
- **Independent test surfaces** — each `schema_app` / `query_app` accepts its own SQL client via `set_sql_client(...)`; the 199 unit tests in [apps/mcp/tests/](../../apps/mcp/tests/) mock per sub-server with no cross-contamination.
- **Open/closed for Day-2** — `forecast.*` and `analytics.*` slot in as new files, zero edits to existing tools. The auto-generated tool catalog at [docs/mcp-tool-catalog.md](../../docs/mcp-tool-catalog.md) renders new families automatically.
- **No magic** — `mount()` is documented FastMCP behaviour; no custom routing layer to maintain. The mount is a one-liner per sub-server.

## Negative consequences

- **FastMCP coupling** — switching to a different MCP framework later (e.g. a fork or a lower-level SDK) means re-doing the mount/prefix mechanism. Acceptable: FastMCP is the de-facto Python MCP implementation today, and the cost is one file (`app/main.py`).
- **Two distinct injection points for the SQL client** — `schema_module.set_sql_client(...)` and `query_module.set_sql_client(...)` must both be wired from the lifespan handler. Made explicit (and harder to forget) by the test fixtures in [apps/mcp/tests/conftest.py](../../apps/mcp/tests/conftest.py) and [apps/mcp/tests/integration/conftest.py](../../apps/mcp/tests/integration/conftest.py).
- **Discoverability for clients that flatten names** — a client that ignores the dotted namespace and just lists "tool names" sees `schema.list_tables` as one string, not a hierarchy. Acceptable — all serious MCP clients we target (the Agent Framework MCP tool factory, Inspector, Claude Desktop) understand the convention.

## Alternatives considered

### A. Flat namespace at root (`list_tables`, `execute`, ...)

Five tools at the top level. Discarded for Day-2 reasons: with `forecast_list_indicators` and `analytics_compare_periods` we end up reinventing prefix-by-underscore by hand, and there is no machine-readable grouping for the LLM or for the auto-generated tool catalog.

### B. Two separate processes (one MCP per domain)

Two `streamable-http` servers behind two routes. Discarded because:
- We pay 2× the container cost and 2× the cold-start tax for zero functional benefit at our scale.
- The APIM `mcp-api-{dev,prod}` definition would need to either union the two upstreams or expose them separately to clients — both options are worse than a single endpoint.
- The HMAC chain (ADR-0005) is configured once per service; doubling it doubles the maintenance.

### C. Custom dispatcher (single endpoint, manual prefix routing)

Roll our own dispatch table mapping `tool.name → handler`. Discarded because `mount()` already does this correctly, with the right type-checking and error surfaces. Building it ourselves is undifferentiated work that has to be maintained against future FastMCP releases.

## Implementation status

- [x] Root `FastMCP` instance + `mount` calls — Phase 2 #16 ([apps/mcp/app/main.py](../../apps/mcp/app/main.py))
- [x] `app/servers/schema.py` + `set_sql_client(...)` — Phase 2 #18
- [x] `app/servers/query.py` + `set_sql_client(...)` — Phase 2 #19, #20
- [x] Auto-generated tool catalog reflects the namespace — Phase 2 #24 ([docs/mcp-tool-catalog.md](../../docs/mcp-tool-catalog.md))
- [x] 199 unit tests + 12 integration tests pass against the mounted layout — Phase 2 #23

## References

- [PLAN.md §8 — MCP design](../../PLAN.md#8-mcp-design)
- [apps/mcp/README.md](../../apps/mcp/README.md)
- [FastMCP `mount` documentation](https://github.com/jlowin/fastmcp)
