"""Mounted FastMCP sub-servers.

Each module here exports a :class:`fastmcp.FastMCP` instance with the
tools belonging to its namespace. The root server in :mod:`app.main`
mounts them with the corresponding ``namespace=`` so client-facing
tool names come out as ``<namespace>_<tool_name>``.
"""

from __future__ import annotations

from .query import query_server
from .schema import schema_server

__all__ = ["query_server", "schema_server"]
