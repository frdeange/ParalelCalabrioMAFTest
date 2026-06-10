# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /src

COPY pyproject.toml README.md ./
COPY app/ ./app/

RUN pip install --no-cache-dir --target=/install . \
 && find /install -type d \( -name __pycache__ -o -name tests \) -prune -exec rm -rf {} + \
 && find /install -name "*.pyc" -delete

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH=/app/bin:$PATH \
    PORT=8001

RUN apt-get update \
 && apt-get install --no-install-recommends -y \
      ca-certificates \
      curl \
      gnupg \
      unixodbc \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
      | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
      > /etc/apt/sources.list.d/microsoft-prod.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install --no-install-recommends -y msodbcsql18 \
 && apt-get purge -y --auto-remove curl gnupg \
 && ACCEPT_EULA=Y apt-get install --no-install-recommends -y libgssapi-krb5-2 \
 && rm -rf /var/lib/apt/lists/*

ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --system --gid ${APP_GID} mcp \
 && useradd --system --uid ${APP_UID} --gid ${APP_GID} --home /home/mcp --shell /usr/sbin/nologin mcp \
 && mkdir -p /home/mcp \
 && chown -R mcp:mcp /home/mcp

COPY --from=builder --chown=mcp:mcp /install /app

USER mcp
WORKDIR /home/mcp

EXPOSE 8001

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
