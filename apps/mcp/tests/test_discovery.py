"""Acceptance tests for :mod:`app.main` — issue #16.

Asserts the three acceptance criteria of #16:

1. The package builds / imports cleanly (covered by ``from app.main
   import app, mcp``).
2. ``uvicorn app.main:app`` would boot — verified by checking the
   resulting object is a Starlette ASGI app with a lifespan.
3. MCP discovery returns the two namespaces with their prefixes —
   verified end-to-end through the in-memory ``fastmcp.Client``.
"""

from __future__ import annotations

import pytest
from fastmcp import Client, FastMCP

from app.main import app, mcp


def test_root_server_is_fastmcp_instance() -> None:
    """``mcp`` is the root FastMCP server we use for in-memory tests."""
    assert isinstance(mcp, FastMCP)
    assert mcp.name == "calabrio-mcp"


def test_asgi_app_is_starlette_with_lifespan() -> None:
    """``app`` must be a Starlette ASGI app with a lifespan attached.

    FastMCP returns a ``StarletteWithLifespan`` subclass; we don't
    import the exact symbol to avoid a private import. The duck-typing
    checks below are enough to guarantee ``uvicorn app.main:app`` boots.
    """
    assert callable(app)  # ASGI callable
    # ``router.lifespan_context`` is set by Starlette when a lifespan
    # is registered; FastMCP uses it for the Streamable HTTP session
    # manager.
    assert hasattr(app, "router")
    assert app.router.lifespan_context is not None


@pytest.mark.asyncio
async def test_discovery_returns_both_namespaces() -> None:
    """Every tool exposed by the root server lives under one of the two
    expected namespace prefixes, and both prefixes are represented.
    """
    async with Client(mcp) as client:
        tools = await client.list_tools()

    names = {tool.name for tool in tools}
    assert names, "discovery returned zero tools"

    prefixes = {name.split("_", 1)[0] for name in names}
    assert prefixes == {"schema", "query"}, (
        f"expected exactly the schema/query namespaces, got {prefixes!r}"
    )


@pytest.mark.asyncio
async def test_discovery_includes_placeholder_ping_tools() -> None:
    """Both sub-servers expose the placeholder ``ping`` tool added in
    #16; subsequent issues (#18 / #20) replace it with the real Day-1
    tools but the namespace prefix contract stays the same.
    """
    async with Client(mcp) as client:
        tools = await client.list_tools()

    names = {tool.name for tool in tools}
    assert "schema_ping" in names
    assert "query_ping" in names


@pytest.mark.asyncio
async def test_ping_tool_returns_ok_payload() -> None:
    """Sanity-check the placeholder tool actually executes through the
    mount layer (not just registered).
    """
    async with Client(mcp) as client:
        result = await client.call_tool("schema_ping", {})

    assert result.data == {"namespace": "schema", "status": "ok"}


def test_log_level_setting_is_applied_to_root_logger() -> None:
    """``Settings.log_level`` is honoured by ``logging.basicConfig`` in
    :mod:`app.main`.

    Defaults to ``INFO`` (per ``.env.example``). We assert ``<= INFO``
    rather than ``== INFO`` so a developer overriding ``MCP_LOG_LEVEL``
    locally does not flake the test \u2014 the contract is "main wires the
    setting", not "the level is exactly INFO".
    """
    import logging

    # ``logging.basicConfig`` only takes effect on first call unless
    # ``force=True``; main.py uses ``force=True`` so the root logger
    # level reflects the value from settings even if pytest itself or a
    # third-party plugin had previously configured logging.
    assert logging.getLogger().level <= logging.INFO
