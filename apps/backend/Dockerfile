# syntax=docker/dockerfile:1.7
# =============================================================================
# WFM Data Assistant — Backend container (apps/backend)
#
# Multi-stage build:
#   1. ``builder`` resolves wheels into ``/install`` using ``pip install --target``
#      against an isolated, write-only prefix. No build cache, no tests, no docs.
#   2. ``runtime`` is a fresh slim base that copies only the resolved site-packages
#      and the application package, runs as a non-root user (uid 10001), and
#      starts gunicorn with uvicorn workers.
#
# Build:
#     docker build -t backend apps/backend
#
# Run (requires Phase 1 #12 to land the FastAPI ``app.main:app`` symbol):
#     docker run --rm -p 8000:8000 --env-file .env backend
#
# Acceptance criteria covered (issue #15):
#   - ``docker build -t backend apps/backend`` succeeds
#   - Runs as non-root user (uid 10001)
#   - Image size: tracked separately in issue #53 — the current image is
#     ~1.2 GB because ``pyproject.toml`` depends on the ``agent-framework``
#     meta package, which pulls every provider sub-package. Slimming is
#     intentionally a pyproject concern, not a Dockerfile concern.
# =============================================================================

ARG PYTHON_VERSION=3.13
ARG GUNICORN_VERSION=23.0.0

# -----------------------------------------------------------------------------
# Stage 1 — builder
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

# Build-time hygiene: deterministic pip, no .pyc bloat, no interactive prompts.
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Some Azure SDK / OTel wheels need a build toolchain for their native deps.
# We install it here, in the builder stage, so the runtime image stays slim.
RUN apt-get update \
 && apt-get install --no-install-recommends -y \
        build-essential \
        git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# Copy only the metadata first so ``pip install`` can be cached layer-wise. We
# add the source after, and the layer that resolves the source is invalidated
# only when ``app/`` changes (not on every editor save in the README, etc.).
COPY pyproject.toml README.md ./
COPY app/ ./app/

# Resolve runtime deps into a portable prefix that the runtime stage will
# adopt as PYTHONPATH. ``--target`` keeps everything self-contained and avoids
# touching the system site-packages of the runtime base image.
ARG GUNICORN_VERSION
RUN pip install --no-cache-dir \
        --target=/install \
        "gunicorn==${GUNICORN_VERSION}" \
        . \
 # Strip optional caches/tests bundled by wheels to claw back image size.
 # NB: we deliberately KEEP ``*.dist-info`` because ``importlib.metadata``
 # entry points (used by OpenTelemetry to discover its runtime context
 # implementation, by gunicorn/uvicorn for workers, etc.) need them at import
 # time. Removing dist-info breaks ``from agent_framework import ...``.
 && find /install -type d \( -name __pycache__ -o -name tests \) -prune -exec rm -rf {} + 2>/dev/null || true \
 && find /install -name '*.pyc' -delete

# -----------------------------------------------------------------------------
# Stage 2 — runtime
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH=/app/bin:$PATH \
    # Sensible gunicorn defaults; can be overridden via env at ``docker run``.
    GUNICORN_WORKERS=2 \
    GUNICORN_TIMEOUT=120 \
    PORT=8000

# Create a dedicated non-root user. Container Apps doesn't impose this but
# ACR / image scanners (Defender for Cloud, Trivy) flag root-by-default images
# as a CIS finding.
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --system --gid ${APP_GID} app \
 && useradd  --system --uid ${APP_UID} --gid ${APP_GID} --home /home/app --shell /usr/sbin/nologin app \
 && mkdir -p /home/app \
 && chown -R app:app /home/app

# Copy the resolved Python tree from the builder stage. ``--chown`` keeps file
# ownership consistent with the runtime user without a recursive chown layer.
COPY --from=builder --chown=app:app /install /app

USER app
WORKDIR /home/app

EXPOSE 8000

# Lightweight container-level healthcheck. ``app.main:app`` is supplied by
# issue #12; until then the container will fail this probe on startup, which
# is the intended signal that the runtime piece is not yet ready.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/healthz', timeout=3).read()" || exit 1

# Production entrypoint: gunicorn process manager + uvicorn workers, binding
# to 0.0.0.0:${PORT}. The ASGI symbol ``app.main:app`` is the FastAPI app
# created in issue #12; ``docker build`` does not import it, so the image
# builds today and becomes runnable once #12 merges.
CMD ["sh", "-c", "exec gunicorn \
        --bind 0.0.0.0:${PORT} \
        --workers ${GUNICORN_WORKERS} \
        --timeout ${GUNICORN_TIMEOUT} \
        --worker-class uvicorn.workers.UvicornWorker \
        --access-logfile - \
        --error-logfile - \
        app.main:app"]
