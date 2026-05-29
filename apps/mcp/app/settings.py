"""Typed runtime configuration for the MCP server.

Knobs are added per Phase 2 issue. Each field documents its purpose and
the issue that introduced it so the manifest stays traceable.
"""

from __future__ import annotations

import re

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Mirrors ``app.clients.sql._UNSAFE_ODBC_CHARS``. Defined here too so
# misconfiguration fails the process *boot*, not the first query: e.g.
# ``MCP_AZURE_SQL_SERVER="x.db;UID=evil;PWD=..."`` must never be
# accepted as a Settings value, because the value is later interpolated
# verbatim into the ODBC connection string.
_UNSAFE_ODBC_CHARS = re.compile(r"[;={}\"'\x00\r\n\t]")


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

    # Pin the User-Assigned Managed Identity used to acquire SQL access
    # tokens. Critical in Azure when the container has more than one
    # UAMI attached — without this pin, MSAL picks one arbitrarily and
    # the SQL grant may not match. Leave empty to use the
    # System-Assigned MI or a single UAMI. Ported from
    # ``CalabrioMAFVersion/src/mcp_wfm/app/config.py``.
    azure_sql_managed_identity_client_id: str = ""

    # Runtime environment marker, used by
    # :class:`SqlDatabaseClient` to lock the credential chain down to
    # *Managed Identity only* in Azure and *az CLI only* in local. The
    # default chain would also try ``EnvironmentCredential`` (which
    # honours ``AZURE_CLIENT_SECRET`` — a password by another name) and
    # interactive browser flows; we explicitly forbid both.
    environment: str = "local"

    # ------------------------------------------------------------------
    # query.execute row caps (issue #20)
    # ------------------------------------------------------------------
    # ``query.execute`` defaults to returning at most
    # ``query_max_rows_default`` rows; callers may override per call up
    # to ``query_max_rows_cap``. Out-of-range ``max_rows`` raises rather
    # than silently clamping so the agent does not get back a smaller
    # result than the one it explicitly requested and treat it as
    # complete.
    #
    # The 200/1000 defaults mirror PLAN.md §6.3 ("Day-1 tools, page
    # 200 rows by default, cap at 1k"). Tuning is per-deployment via
    # ``MCP_QUERY_MAX_ROWS_DEFAULT`` / ``MCP_QUERY_MAX_ROWS_CAP``.
    query_max_rows_default: int = 200
    query_max_rows_cap: int = 1_000

    @field_validator("azure_sql_server", "azure_sql_database")
    @classmethod
    def _reject_odbc_delimiters(cls, value: str | None) -> str | None:
        """Block ODBC-injection vectors at the config boundary.

        ``server`` and ``database`` are interpolated verbatim into the
        ODBC connection string built by
        :class:`SqlDatabaseClient`. If either value contained a ``;``
        or ``=``, a misconfiguration could smuggle an extra clause
        (``UID=`` / ``PWD=`` / ``Authentication=``) and break the
        Entra-only invariant fixed in issue #17. Reject the whole
        configuration here rather than relying on the downstream
        sanitizer to clean it up.
        """
        if value is None:
            return None
        match = _UNSAFE_ODBC_CHARS.search(value)
        if match is not None:
            raise ValueError(
                f"contains forbidden character {match.group()!r}; hostnames "
                "and database names must not contain any of: ; = { } \" ' or "
                "control chars."
            )
        return value

    @field_validator("query_max_rows_default", "query_max_rows_cap")
    @classmethod
    def _reject_non_positive_row_caps(cls, value: int) -> int:
        """Both row caps must be strictly positive ints.

        A zero or negative default would make every ``query.execute``
        call fail at the ``SqlDatabaseClient.execute`` layer with a
        less actionable error. Reject at boot instead.
        """
        if value < 1:
            raise ValueError(f"must be >= 1, got {value}")
        return value

    @model_validator(mode="after")
    def _validate_row_cap_ordering(self) -> Settings:
        """``query_max_rows_default`` must not exceed ``query_max_rows_cap``.

        Otherwise a caller passing ``max_rows=None`` would request
        more rows than the hard cap permits and the tool would reject
        its own default — a confusing failure mode that should never
        ship to production.
        """
        if self.query_max_rows_default > self.query_max_rows_cap:
            raise ValueError(
                f"MCP_QUERY_MAX_ROWS_DEFAULT ({self.query_max_rows_default}) must "
                f"not exceed MCP_QUERY_MAX_ROWS_CAP ({self.query_max_rows_cap})."
            )
        return self


def get_settings() -> Settings:
    """Return a fresh :class:`Settings` instance.

    Kept as a function (rather than a module-level singleton) so tests
    can monkey-patch the environment before calling it. Each call reads
    the environment again — cheap enough for our request volume.
    """
    return Settings()


__all__ = ["Settings", "get_settings"]
