"""``schema`` namespace sub-server.

Hosts the schema-introspection tools listed in PLAN.md §6.3 Day-1:

* ``list_tables``        — lands in #18
* ``search_tables``      — lands in #18
* ``describe_table``     — lands in #18
* ``get_distinct_values`` — lands in #18

This module currently only exposes a no-op ``ping`` tool so MCP
discovery returns the namespace even before the real tools are wired —
that is what the acceptance criteria of #16 require. ``ping`` doubles
as a cheap liveness probe for ACA / curl-style smoke tests, and will be
kept (or replaced by a richer ``health``) once #18 ships.
"""

from __future__ import annotations

from fastmcp import FastMCP

schema_server: FastMCP = FastMCP(name="calabrio-mcp-schema")


@schema_server.tool
def ping() -> dict[str, str]:
    """Return a static liveness payload.

    Placeholder tool so the ``schema`` namespace is observable through
    MCP discovery before the Day-1 introspection tools land in #18.
    """
    return {"namespace": "schema", "status": "ok"}


__all__ = ["schema_server"]
