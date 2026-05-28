"""Typed runtime configuration for the MCP server.

Only the knobs needed for the **scaffold** (issue #16) are exposed.
Subsequent issues (#17 SqlDatabaseClient, #18-#20 tools, #19 validator)
extend this module with database / KV / HMAC settings.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables / ``.env``.

    All names are upper-case in the environment and lower-case here
    (pydantic-settings handles the mapping). Values are immutable after
    construction.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MCP_",
        extra="ignore",
        frozen=True,
    )

    # Path the Streamable HTTP transport listens on. Backend's
    # ``MCPStreamableHTTPTool`` consumers concatenate this with the base
    # URL. Default mirrors the spec example so a fresh checkout boots
    # without any environment customisation.
    path: str = "/mcp/"

    # Stateless Streamable HTTP per PLAN.md §6.3 ("Does NOT cache across
    # requests"). Tests flip this off when they need to assert session
    # continuity, but production always runs stateless.
    stateless: bool = True

    # Log level forwarded to uvicorn / FastMCP.
    log_level: str = "INFO"


def get_settings() -> Settings:
    """Return a fresh :class:`Settings` instance.

    Kept as a function (rather than a module-level singleton) so tests
    can monkey-patch the environment before calling it. Each call reads
    the environment again — cheap enough for our request volume.
    """
    return Settings()


__all__ = ["Settings", "get_settings"]
