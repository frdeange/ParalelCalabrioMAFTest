"""Unit tests for :mod:`app.settings`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.settings import Settings, get_settings


def test_settings_loads_with_required_env(required_env: dict[str, str]) -> None:
    """All required vars set → ``Settings()`` builds and exposes them."""
    s = Settings(_env_file=None)

    assert s.foundry_project_endpoint == required_env["FOUNDRY_PROJECT_ENDPOINT"]
    assert s.mcp_server_url == required_env["MCP_SERVER_URL"]
    assert s.azure_cosmos_endpoint == required_env["AZURE_COSMOS_ENDPOINT"]
    assert (
        s.applicationinsights_connection_string
        == required_env["APPLICATIONINSIGHTS_CONNECTION_STRING"]
    )
    assert s.hmac_shared_secret.get_secret_value() == required_env["HMAC_SHARED_SECRET"]


def test_settings_defaults_apply(required_env: dict[str, str]) -> None:
    """Optional vars fall back to the documented defaults from PLAN.md §14."""
    s = Settings(_env_file=None)

    assert s.foundry_deployment_name == "gpt-5.2"
    assert s.azure_ai_model_deployment_name == "gpt-5.2"
    assert s.azure_cosmos_database_name == "agent-framework"
    assert s.azure_cosmos_container_name == "chat-history"
    assert s.enable_instrumentation is True
    assert s.enable_sensitive_data is True
    assert s.otel_service_name == "wfm-backend"
    assert s.bu_id_default == 1
    assert s.log_level == "INFO"


@pytest.mark.parametrize(
    "missing_var",
    [
        "FOUNDRY_PROJECT_ENDPOINT",
        "MCP_SERVER_URL",
        "AZURE_COSMOS_ENDPOINT",
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "HMAC_SHARED_SECRET",
    ],
)
def test_missing_required_var_raises_validation_error(
    required_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    missing_var: str,
) -> None:
    """Every required var must be present; missing any one fails fast."""
    monkeypatch.delenv(missing_var, raising=False)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    # The offending field appears in the error report — operators can fix
    # their .env in one shot rather than guessing.
    assert missing_var.lower() in str(excinfo.value).lower()


def test_bu_id_default_coerces_to_int(
    required_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``BU_ID_DEFAULT`` is parsed as ``int`` even when provided as a string."""
    monkeypatch.setenv("BU_ID_DEFAULT", "42")
    s = Settings(_env_file=None)
    assert s.bu_id_default == 42
    assert isinstance(s.bu_id_default, int)


def test_get_settings_is_cached(required_env: dict[str, str]) -> None:
    """``get_settings`` returns the same instance on repeated calls."""
    get_settings.cache_clear()
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_lazy_module_level_settings(required_env: dict[str, str]) -> None:
    """``from app.settings import settings`` returns the cached singleton."""
    get_settings.cache_clear()
    from app import settings as settings_module

    s = settings_module.settings  # triggers ``__getattr__``
    assert isinstance(s, Settings)
    assert s is get_settings()


def test_unknown_module_attribute_raises() -> None:
    """``__getattr__`` only handles ``settings``; everything else errors."""
    from app import settings as settings_module

    with pytest.raises(AttributeError):
        _ = settings_module.nonexistent  # type: ignore[attr-defined]
