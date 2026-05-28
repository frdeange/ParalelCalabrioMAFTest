"""FastAPI application factory + module-level ASGI entrypoint.

The factory :func:`create_app` is the single place that constructs
the FastAPI instance. It exists so the test suite can swap in a fake
lifespan (one that does not touch Azure) without monkey-patching the
real production wiring.

Production servers (gunicorn / uvicorn) import the module-level
``app`` symbol — e.g. ``gunicorn app.main:app``. That call uses the
default :func:`app.lifespan.lifespan`, which performs the full Azure
bootstrap and registers the AG-UI endpoint.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from importlib import metadata
from typing import Any

from fastapi import FastAPI

from app.lifespan import lifespan as default_lifespan
from app.settings import get_settings

logger = logging.getLogger(__name__)


def _resolve_version() -> str:
    """Return the installed ``wfm-backend`` distribution version.

    Falls back to ``"0.0.0"`` when running from a checkout without
    ``pip install -e`` (e.g. some test environments). The fallback is
    deliberately distinguishable from a real version so health-check
    consumers can tell editable-mode tests apart from production.
    """
    try:
        return metadata.version("wfm-backend")
    except metadata.PackageNotFoundError:
        return "0.0.0"


# Resolved once at import time. ``GIT_SHA`` is injected by the Docker
# build (``ARG GIT_SHA`` once the Dockerfile starts wiring it in #15
# follow-up). In dev shells it stays ``"dev"`` so the health endpoint
# remains useful without a build pipeline.
_VERSION = _resolve_version()
_GIT_SHA = os.environ.get("GIT_SHA", "dev")


def create_app(
    *,
    lifespan: Callable[[FastAPI], Any] | None = None,
) -> FastAPI:
    """Build a FastAPI application.

    Parameters
    ----------
    lifespan:
        Optional async-context-manager factory. Defaults to
        :func:`app.lifespan.lifespan` which performs the full Azure
        bootstrap. Tests inject a stub that registers a fake
        ``/agui`` handler against a mock workflow.
    """
    settings = get_settings()

    app = FastAPI(
        title="WFM Backend",
        version=_VERSION,
        description=(
            "Workforce-Management chat assistant backend. "
            "Exposes the MAF workflow as an AG-UI SSE endpoint and "
            "a health probe."
        ),
        lifespan=lifespan or default_lifespan,
    )

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        """Liveness + identification probe.

        Returns the package version (from the ``wfm-backend``
        distribution metadata) and the git SHA (from the ``GIT_SHA``
        environment variable set by the Docker build). Used by
        Container Apps health probes, by the deployment pipeline to
        verify the rolled-out image and by support to confirm which
        revision is serving a given request.
        """
        return {
            "status": "ok",
            "service": settings.otel_service_name,
            "version": _VERSION,
            "git_sha": _GIT_SHA,
        }

    return app


# Module-level ASGI app — referenced by ``gunicorn app.main:app``.
app = create_app()


__all__ = ["app", "create_app"]
