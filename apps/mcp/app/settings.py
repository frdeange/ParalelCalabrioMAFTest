"""Typed runtime configuration for the MCP server.

Knobs are added per Phase 2 issue. Each field documents its purpose and
the issue that introduced it so the manifest stays traceable.
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

    # ------------------------------------------------------------------
    # Streamable HTTP transport (issue #16 scaffold)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Azure SQL connection (issue #17 SqlDatabaseClient)
    # ------------------------------------------------------------------
    # Authentication is **Entra only** via ``DefaultAzureCredential``
    # (see ``app/clients/sql.py``). SQL passwords are forbidden by
    # policy; there is no username / KV-secret fallback. Key Vault is
    # consumed only for the HMAC shared secret (#47).
    #
    # Optional at the Settings layer so the FastMCP scaffold and unit
    # tests for unrelated modules keep working without these vars set.
    # ``SqlDatabaseClient.__init__`` validates that both are present
    # before opening any connection.
    azure_sql_server: str | None = None
    azure_sql_database: str | None = None


def get_settings() -> Settings:
    """Return a fresh :class:`Settings` instance.

    Kept as a function (rather than a module-level singleton) so tests
    can monkey-patch the environment before calling it. Each call reads
    the environment again — cheap enough for our request volume.
    """
    return Settings()


__all__ = ["Settings", "get_settings"]
