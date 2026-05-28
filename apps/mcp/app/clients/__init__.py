"""HTTP-style clients consumed by MCP tools.

Each module here owns *one* external dependency (SQL, Key Vault, …) and
exposes a thin, well-typed wrapper that the FastMCP tools in
``app/servers/`` can call without worrying about authentication,
retries, or driver quirks.
"""

from app.clients.sql import SqlDatabaseClient

__all__ = ["SqlDatabaseClient"]
