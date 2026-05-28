"""MCP client factory for the backend.

Centralises the construction of :class:`MCPStreamableHTTPTool` instances
so the rest of the codebase never deals with URLs, prefixes or HMAC
headers directly.

Why a factory module
--------------------
The MAF workflow needs at least two distinct MCP "tool slots":

* one carrying the catalog / schema discovery tools used by the SQL
  builder agent (``listTables``, ``getSchema``), and
* one carrying the read-only execution tool used by the query executor
  agent (``executeQuery``).

Both slots talk to the **same MCP server** but expose a different
``allowed_tools`` whitelist. Building them through this factory ensures
both share the same:

* base URL (``settings.mcp_server_url``),
* display name + tool prefix (``settings.mcp_tool_name`` /
  ``settings.mcp_tool_prefix``), so traces and tool names are
  consistent, and
* outbound header pipeline — e.g. the HMAC signature the backend will
  add when talking through APIM in production (see ``app/security/hmac.py``
  once #13 lands).

See PLAN.md §6.3 ("MCP design") and decision D10.
"""

from app.mcp.factory import build_mcp_tool

__all__ = ["build_mcp_tool"]
