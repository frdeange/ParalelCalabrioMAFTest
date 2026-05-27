"""Typed runtime configuration for the WFM backend.

All variables are sourced from the process environment, falling back to a
local ``.env`` file for development (see ``.env.example`` for the full
inventory and PLAN.md §14 for the canonical list).

Required variables raise :class:`pydantic.ValidationError` at construction
time — the service fails fast at boot rather than producing confusing
errors later when an Azure SDK is invoked without credentials.

Usage
-----
::

    from app.settings import settings  # lazy singleton
    settings.foundry_project_endpoint

For dependency-injected access in FastAPI handlers::

    from fastapi import Depends
    from app.settings import Settings, get_settings

    @app.get("/healthz")
    def healthz(s: Settings = Depends(get_settings)) -> dict[str, str]:
        return {"service": s.otel_service_name}
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend runtime configuration.

    Field names are snake_case (Python convention) and each is bound to its
    UPPER_SNAKE environment variable via ``alias=``. The mapping is the
    single source of truth referenced by ``.env.example`` and PLAN.md §14.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    # ------------------------------------------------------------------
    # Foundry / model
    # ------------------------------------------------------------------
    foundry_project_endpoint: str = Field(
        ...,
        alias="FOUNDRY_PROJECT_ENDPOINT",
        description=(
            "Azure AI Foundry project endpoint URL "
            "(https://<project>.<region>.ai.azure.com/api/projects/<id>)."
        ),
    )
    foundry_deployment_name: str = Field(
        "gpt-5.2",
        alias="FOUNDRY_DEPLOYMENT_NAME",
        description="Foundry chat model deployment used by the workflow agents.",
    )
    azure_ai_model_deployment_name: str = Field(
        "gpt-5.2",
        alias="AZURE_AI_MODEL_DEPLOYMENT_NAME",
        description="Alias kept for SDK clients that read it directly.",
    )

    # ------------------------------------------------------------------
    # MCP (tool server)
    # ------------------------------------------------------------------
    mcp_server_url: str = Field(
        ...,
        alias="MCP_SERVER_URL",
        description=(
            "Public MCP entry point — typically the APIM mcp-api facade "
            "(e.g. https://<apim>/mcp-api-dev/mcp/)."
        ),
    )

    # ------------------------------------------------------------------
    # Cosmos DB (conversation history)
    # ------------------------------------------------------------------
    azure_cosmos_endpoint: str = Field(
        ...,
        alias="AZURE_COSMOS_ENDPOINT",
        description="Cosmos DB account endpoint URL.",
    )
    azure_cosmos_database_name: str = Field(
        "agent-framework",
        alias="AZURE_COSMOS_DATABASE_NAME",
        description="Database name where the chat-history container lives.",
    )
    azure_cosmos_container_name: str = Field(
        "chat-history",
        alias="AZURE_COSMOS_CONTAINER_NAME",
        description="Container name (partition key /session_id).",
    )

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------
    applicationinsights_connection_string: str = Field(
        ...,
        alias="APPLICATIONINSIGHTS_CONNECTION_STRING",
        description="App Insights connection string for the OTel exporter.",
    )
    enable_instrumentation: bool = Field(
        True,
        alias="ENABLE_INSTRUMENTATION",
        description="Master switch for agent-framework instrumentation.",
    )
    enable_sensitive_data: bool = Field(
        True,
        alias="ENABLE_SENSITIVE_DATA",
        description="Emit prompts/responses in traces (disable in production).",
    )
    otel_service_name: str = Field(
        "wfm-backend",
        alias="OTEL_SERVICE_NAME",
        description="Service name attached to every OTel span.",
    )

    # ------------------------------------------------------------------
    # Business Unit
    # ------------------------------------------------------------------
    bu_id_default: int = Field(
        1,
        alias="BU_ID_DEFAULT",
        description=(
            "Fallback Business Unit ID when APIM cannot resolve it from "
            "JWT claim, domain map or debug header (PLAN.md §4 D8)."
        ),
    )

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    hmac_shared_secret: SecretStr = Field(
        ...,
        alias="HMAC_SHARED_SECRET",
        description=(
            "HMAC secret shared with APIM for request signing — "
            "KeyVault reference in production (PLAN.md §11)."
        ),
    )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    log_level: str = Field(
        "INFO",
        alias="LOG_LEVEL",
        description="Root logger level (DEBUG / INFO / WARNING / ERROR).",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached :class:`Settings` singleton.

    Construction is deferred to first access so that importing this module
    does not require the full environment to be present (handy for tooling
    and unit tests that only need to import individual fields/types).
    """
    return Settings()


def __getattr__(name: str) -> Settings:
    """Lazy module-level access to the singleton.

    Allows ``from app.settings import settings`` to work without forcing
    construction at import time. The first attribute read materialises the
    singleton; subsequent reads return the cached instance.
    """
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ``settings`` is exposed lazily via ``__getattr__`` above, so ruff/F822
# cannot statically see it as a module attribute. Silence that one warning.
__all__ = ["Settings", "get_settings", "settings"]  # noqa: F822
