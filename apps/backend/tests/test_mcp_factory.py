"""Tests for :mod:`app.mcp.factory`.

We do not boot a real MCP server here — that lives behind ``apps/mcp``
and has its own integration suite (#23). These tests verify the
plumbing of the factory: that settings flow through to the underlying
:class:`MCPStreamableHTTPTool`, that ``allowed_tools`` round-trips, and
that the optional ``header_provider`` hook is honoured.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.mcp import build_mcp_tool
from app.mcp.factory import HeaderProvider
from app.settings import Settings


def _settings(**overrides: Any) -> Settings:
    """Build a ``Settings`` instance with the conftest defaults."""
    base = Settings()
    return base.model_copy(update=overrides)


def test_build_mcp_tool_uses_settings_defaults() -> None:
    settings = _settings(
        mcp_server_url="https://test.local/mcp-api-dev/mcp/",
        mcp_tool_name="wfm-data",
        mcp_tool_prefix="",
    )

    tool = build_mcp_tool(settings, allowed_tools=["listTables", "getSchema"])

    assert tool.name == "wfm-data"
    assert tool.url == "https://test.local/mcp-api-dev/mcp/"
    # Empty prefix collapses to ``None`` so MAF does not prepend "_".
    assert tool.tool_name_prefix is None
    assert tool.allowed_tools == ["listTables", "getSchema"]


def test_build_mcp_tool_applies_prefix_from_settings() -> None:
    # MAF stores the prefix without the trailing underscore and re-adds
    # it when composing the per-tool name, so ``"wfm_"`` round-trips as
    # ``"wfm"`` on the public attribute.
    settings = _settings(mcp_tool_prefix="wfm_")

    tool = build_mcp_tool(settings, allowed_tools=["listTables"])

    assert tool.tool_name_prefix == "wfm"


def test_build_mcp_tool_explicit_name_overrides_settings() -> None:
    settings = _settings(mcp_tool_name="wfm-data")

    tool = build_mcp_tool(
        settings, allowed_tools=["executeQuery"], name="wfm-data-exec"
    )

    assert tool.name == "wfm-data-exec"


def test_build_mcp_tool_explicit_prefix_overrides_settings() -> None:
    settings = _settings(mcp_tool_prefix="ignored_")

    tool = build_mcp_tool(
        settings,
        allowed_tools=["listTables"],
        tool_name_prefix="other_",
    )

    # Same trailing-underscore normalisation as above.
    assert tool.tool_name_prefix == "other"


def test_build_mcp_tool_allowed_tools_round_trips() -> None:
    settings = _settings()

    # ``frozenset`` is a Collection but not a list — the factory must
    # normalise to list so MAF can serialise it.
    tool = build_mcp_tool(settings, allowed_tools=frozenset(["executeQuery"]))

    assert tool.allowed_tools == ["executeQuery"]


def test_build_mcp_tool_allowed_tools_none_keeps_everything() -> None:
    settings = _settings()

    tool = build_mcp_tool(settings, allowed_tools=None)

    assert tool.allowed_tools is None


def test_build_mcp_tool_propagates_header_provider() -> None:
    settings = _settings()

    captured: dict[str, Any] = {}

    def provider(ctx: dict[str, Any]) -> dict[str, str]:
        captured["ctx"] = ctx
        return {"x-apim-signature": "deadbeef"}

    tool = build_mcp_tool(
        settings, allowed_tools=["listTables"], header_provider=provider
    )

    # MAF stores the callable on a private attribute (``_header_provider``)
    # — there is no public accessor at the time of writing. Asserting on
    # the private attribute is intentional: it locks the contract so a
    # silent rename in MAF would surface in our CI rather than at runtime
    # against APIM.
    assert tool._header_provider is provider
    # Smoke-call the provider to confirm it remains usable through the
    # round-trip; the real MCP client invokes it once per request.
    headers = tool._header_provider({"method": "POST"})
    assert headers == {"x-apim-signature": "deadbeef"}
    assert captured["ctx"] == {"method": "POST"}


def test_header_provider_protocol_is_callable() -> None:
    """The exported :data:`HeaderProvider` alias must be a callable type."""

    provider: HeaderProvider = lambda _ctx: {"x-test": "1"}  # noqa: E731
    assert isinstance(provider, Callable)


def test_build_mcp_tool_pins_approval_mode_never_require() -> None:
    """Trusted-infra design: MCP calls never trigger interactive approval."""
    settings = _settings()

    tool = build_mcp_tool(settings, allowed_tools=["listTables"])

    # ``approval_mode`` lives on the underlying tool definition; surface it
    # via the public attribute MAF exposes.
    assert tool.approval_mode == "never_require"


@pytest.mark.parametrize("prefix", ["", None])
def test_build_mcp_tool_empty_prefix_normalised_to_none(prefix: str | None) -> None:
    settings = _settings(mcp_tool_prefix=prefix or "")

    tool = build_mcp_tool(
        settings, allowed_tools=["listTables"], tool_name_prefix=prefix
    )

    assert tool.tool_name_prefix is None
