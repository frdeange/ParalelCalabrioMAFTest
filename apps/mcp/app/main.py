"""ASGI entrypoint for the MCP server.

Builds the root :class:`fastmcp.FastMCP` instance, mounts the
``schema`` and ``query`` sub-servers under their respective
namespaces, and exposes a Streamable HTTP ASGI ``app`` for
``uvicorn app.main:app``.

Design notes
------------

* **Mounting**: PLAN.md §8 and the issue title call the kwarg
  ``prefix=``, but FastMCP 3.x renamed it to ``namespace=`` and emits a
  ``DeprecationWarning`` if the old name is used. The behaviour is
  identical — tools end up exposed as ``<namespace>_<tool_name>`` — so
  we adopt the current name to keep the warning out of CI.
* **Stateless Streamable HTTP**: required by PLAN.md §6.3 ("Does NOT
  cache across requests"). Each request gets a brand-new transport;
  there is no server-side session bookkeeping to scale horizontally.
* **No HMAC verification yet**: APIM-signed request verification lands
  in a later phase-2 issue together with the SqlDatabaseClient (#17)
  and the validator (#19). The scaffold deliberately leaves the door
  open by exposing ``app`` as a Starlette ASGI app — adding a
  middleware is then a one-liner.
"""

from __future__ import annotations

from fastmcp import FastMCP

from .servers import query_server, schema_server
from .settings import get_settings

settings = get_settings()

#: Root MCP server. Holds no tools of its own; everything is reached
#: through the mounted sub-servers.
mcp: FastMCP = FastMCP(name="calabrio-mcp")
mcp.mount(schema_server, namespace="schema")
mcp.mount(query_server, namespace="query")

#: Starlette ASGI application served by uvicorn.
#:
#: ``http_app`` returns a fully-wired Starlette app (with the FastMCP
#: lifespan attached) — that's the object ``uvicorn app.main:app`` will
#: import. Keeping the FastMCP instance (`mcp`) and the ASGI app (`app`)
#: as separate module-level names lets tests use the in-memory
#: ``fastmcp.Client(mcp)`` transport without spinning HTTP.
app = mcp.http_app(
    path=settings.path,
    transport="streamable-http",
    stateless_http=settings.stateless,
)


__all__ = ["app", "mcp"]
