"""Tests for :mod:`app.lifespan` helpers.

The full production lifespan is not exercised here — it requires
Azure credentials, a reachable MCP server and a Foundry endpoint.
What we *can* test cheaply is the telemetry initialisation, which is
the only branch with a meaningful business rule (skip when disabled).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.lifespan import _init_telemetry


def _settings(**overrides: object) -> SimpleNamespace:
    """Build a minimal settings-like object with only the fields
    :func:`_init_telemetry` actually reads."""
    base = {
        "enable_instrumentation": True,
        "enable_sensitive_data": False,
        "applicationinsights_connection_string": "InstrumentationKey=00000000-0000-0000-0000-000000000000;IngestionEndpoint=https://x.in/",
        "otel_service_name": "wfm-backend-test",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_init_telemetry_skips_when_disabled() -> None:
    """When ``ENABLE_INSTRUMENTATION=false`` the function must not
    call :func:`configure_azure_monitor` — otherwise local dev runs
    without an App Insights resource would crash on startup."""
    settings = _settings(enable_instrumentation=False)
    with (
        patch("app.lifespan.configure_azure_monitor") as cfg,
        patch("app.lifespan.enable_instrumentation") as enable,
    ):
        _init_telemetry(settings)  # type: ignore[arg-type]

    cfg.assert_not_called()
    enable.assert_not_called()


def test_init_telemetry_configures_when_enabled() -> None:
    """When enabled the function forwards every flag to the
    underlying primitives — including the sensitive-data toggle."""
    settings = _settings(enable_instrumentation=True, enable_sensitive_data=True)

    with (
        patch("app.lifespan.configure_azure_monitor") as cfg,
        patch("app.lifespan.enable_instrumentation") as enable,
        patch("app.lifespan.create_resource") as create_resource,
    ):
        create_resource.return_value = "fake-resource"
        _init_telemetry(settings)  # type: ignore[arg-type]

    cfg.assert_called_once()
    kwargs = cfg.call_args.kwargs
    assert (
        kwargs["connection_string"]
        == settings.applicationinsights_connection_string
    )
    assert kwargs["resource"] == "fake-resource"
    assert kwargs["enable_live_metrics"] is True

    create_resource.assert_called_once_with(service_name=settings.otel_service_name)
    enable.assert_called_once_with(enable_sensitive_data=True)
