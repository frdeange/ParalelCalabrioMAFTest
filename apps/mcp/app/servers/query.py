"""``query`` namespace sub-server.

Hosts the query-execution tool listed in PLAN.md §6.3 Day-1:

* ``execute`` — lands in #20 (depends on the validator from #19 and
  the ``SqlDatabaseClient`` from #17).

This module currently only exposes a no-op ``ping`` tool so MCP
discovery returns the namespace even before the real tool is wired —
see :mod:`app.servers.schema` for the rationale.
"""

from __future__ import annotations

from fastmcp import FastMCP

query_server: FastMCP = FastMCP(name="calabrio-mcp-query")


@query_server.tool
def ping() -> dict[str, str]:
    """Return a static liveness payload.

    Placeholder tool so the ``query`` namespace is observable through
    MCP discovery before ``execute`` lands in #20.
    """
    return {"namespace": "query", "status": "ok"}


__all__ = ["query_server"]
