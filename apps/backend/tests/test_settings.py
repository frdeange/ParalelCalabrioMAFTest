"""Unit tests for :mod:`app.settings`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.settings import Settings, get_settings, settings


def test_settings_singleton_is_typed() -> None:
    """``from app.settings import settings`` returns a fully-typed object."""
    assert isinstance(settings, Settings)
    assert settings.foundry_project_endpoint.startswith("https://")
    assert settings.hmac_shared_secret.get_secret_value()  # SecretStr revealed


def test_defaults_apply() -> None:
    """Optional vars fall back to the documented defaults from PLAN.md §14."""
    assert settings.foundry_deployment_name == "gpt-5.2"
    assert settings.azure_ai_model_deployment_name == "gpt-5.2"
    assert settings.azure_cosmos_database_name == "agent-framework"
    assert settings.azure_cosmos_container_name == "chat-history"
    assert settings.enable_instrumentation is True
    assert settings.enable_sensitive_data is True
    assert settings.otel_service_name == "wfm-backend"
    assert settings.bu_id_default == 1
    assert settings.log_level == "INFO"


def test_bu_id_default_coerces_to_int(monkeypatch: pytest.MonkeyPatch) -> None:
    """``BU_ID_DEFAULT`` is parsed as ``int`` even when supplied as a string."""
    monkeypatch.setenv("BU_ID_DEFAULT", "42")
    s = Settings(_env_file=None)
    assert s.bu_id_default == 42
    assert isinstance(s.bu_id_default, int)


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
def test_missing_required_var_raises(
    monkeypatch: pytest.MonkeyPatch, missing_var: str
) -> None:
    """Every required var must be present; missing any one fails fast."""
    monkeypatch.delenv(missing_var, raising=False)
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)
    assert missing_var.lower() in str(excinfo.value).lower()


def test_get_settings_returns_singleton() -> None:
    """``get_settings()`` exposes the module-level instance (for FastAPI Depends)."""
    assert get_settings() is settings
