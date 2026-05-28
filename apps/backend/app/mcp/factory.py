"""Factory for :class:`MCPStreamableHTTPTool` instances.

Public API:

.. code-block:: python

    from app.mcp import build_mcp_tool

    # Slot for the SQL builder agent (catalog + schema discovery).
    schema_tool = build_mcp_tool(
        settings,
        allowed_tools=["listTables", "getSchema"],
    )

    # Slot for the query executor agent (read-only execution).
    exec_tool = build_mcp_tool(
        settings,
        allowed_tools=["executeQuery"],
    )

Both slots share ``settings.mcp_server_url``, ``settings.mcp_tool_name``
and ``settings.mcp_tool_prefix``; the only difference is the whitelist.

Outbound headers
----------------
``header_provider`` is an optional callable invoked by the MAF MCP client
on every outbound HTTP request. The callable receives a snapshot of the
current request context (``dict[str, Any]``) and returns the headers to
add to that single request. This is where #13 will wire the HMAC
signature the backend computes before talking through APIM — it is kept
optional here so the factory itself stays settings-only and the security
glue can land in its own PR without churning this module.

Approval mode
-------------
We always pin ``approval_mode="never_require"``: the MCP server is
trusted infrastructure inside our VNet, and the chat workflow has no
human-in-the-loop UI for tool approval. Per PLAN.md §11 the security
boundary is APIM (authn + HMAC) plus the MCP server's own SQL allowlist
(see #19), not an interactive approval step.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

from agent_framework import MCPStreamableHTTPTool

from app.settings import Settings

# Type alias for the optional outbound header provider.
HeaderProvider = Callable[[dict[str, Any]], dict[str, str]]


def build_mcp_tool(
    settings: Settings,
    *,
    allowed_tools: Collection[str] | None = None,
    name: str | None = None,
    tool_name_prefix: str | None = None,
    header_provider: HeaderProvider | None = None,
) -> MCPStreamableHTTPTool:
    """Construct a configured :class:`MCPStreamableHTTPTool`.

    Parameters
    ----------
    settings:
        Backend configuration singleton. ``mcp_server_url``,
        ``mcp_tool_name`` and ``mcp_tool_prefix`` are read from here.
    allowed_tools:
        Optional whitelist of tool names exposed on this slot. ``None``
        means "every tool the MCP server advertises" — only appropriate
        for ad-hoc scripts; production callers should always pass an
        explicit list so a new tool added on the MCP side doesn't
        silently become available to every agent.
    name:
        Optional override for the MCP slot display name. Defaults to
        ``settings.mcp_tool_name`` (typically ``"wfm-data"``). Most call
        sites should leave this alone; it exists for tests and for
        future scenarios where the backend talks to more than one MCP
        server.
    tool_name_prefix:
        Optional override for the per-tool name prefix the MAF client
        adds before exposing tools to agents. Defaults to
        ``settings.mcp_tool_prefix``. Setting it to an empty string
        keeps the upstream tool names as-is (``listTables``,
        ``getSchema``, ``executeQuery``).
    header_provider:
        Optional callable invoked by the MCP client on every outbound
        request to compute the headers to attach. The callable receives
        a context dictionary and must return a ``dict[str, str]``. This
        is the integration point for HMAC signing of requests bound for
        APIM — see #13.

    Returns
    -------
    A ready-to-use :class:`MCPStreamableHTTPTool`. The caller is
    responsible for entering it as an async context manager (the MCP
    client opens a streamable HTTP session on entry).
    """
    effective_prefix = (
        tool_name_prefix if tool_name_prefix is not None else settings.mcp_tool_prefix
    )

    return MCPStreamableHTTPTool(
        name=name or settings.mcp_tool_name,
        url=settings.mcp_server_url,
        tool_name_prefix=effective_prefix or None,
        allowed_tools=list(allowed_tools) if allowed_tools is not None else None,
        approval_mode="never_require",
        header_provider=header_provider,
    )


__all__ = ["HeaderProvider", "build_mcp_tool"]
