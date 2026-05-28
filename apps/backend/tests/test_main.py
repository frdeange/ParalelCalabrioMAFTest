"""Tests for the FastAPI application factory and ``/healthz``.

These tests deliberately bypass the production lifespan
(:func:`app.lifespan.lifespan`) by injecting a no-op ``contextmanager``
into :func:`app.main.create_app`. The Azure-touching wiring is covered
in :mod:`tests.test_lifespan`; this module only exercises the static
API surface so it can run in any environment.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import create_app


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan that performs no I/O — used to isolate ``/healthz``."""
    yield


@pytest.fixture()
def client() -> TestClient:
    """A test client backed by an app whose lifespan does nothing."""
    app = create_app(lifespan=_noop_lifespan)
    with TestClient(app) as c:
        yield c


def test_healthz_returns_ok(client: TestClient) -> None:
    """``GET /healthz`` returns 200 with the documented payload shape."""
    response = client.get("/healthz")
    assert response.status_code == 200

    payload = response.json()
    # Use a set comparison so the order of keys is irrelevant; the
    # health endpoint contract is the *set* of keys, not their layout.
    assert set(payload.keys()) == {"status", "service", "version", "git_sha"}
    assert payload["status"] == "ok"
    assert isinstance(payload["service"], str) and payload["service"]
    assert isinstance(payload["version"], str) and payload["version"]
    assert isinstance(payload["git_sha"], str) and payload["git_sha"]


def test_healthz_reports_git_sha_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``GIT_SHA`` is read at import time, but we can still exercise the
    fall-through path by reloading the module-level constant."""
    monkeypatch.setattr(main_module, "_GIT_SHA", "abc1234")
    app = create_app(lifespan=_noop_lifespan)
    with TestClient(app) as client_:
        payload = client_.get("/healthz").json()
    assert payload["git_sha"] == "abc1234"


def test_module_level_app_exists() -> None:
    """``app.main.app`` is a FastAPI instance for production ASGI servers."""
    assert isinstance(main_module.app, FastAPI)


def test_openapi_contains_healthz(client: TestClient) -> None:
    """The health endpoint is documented in the OpenAPI schema so the
    SRE tooling that auto-discovers probes can find it."""
    schema = client.get("/openapi.json").json()
    assert "/healthz" in schema["paths"]
    assert "get" in schema["paths"]["/healthz"]
