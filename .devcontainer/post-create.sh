#!/usr/bin/env bash
# Dev container bootstrap.
#
# Runs once on container creation. Idempotent: re-running is safe (apt/pip
# detect already-installed packages and no-op).
#
# Order matters: install OS-level dependencies first (msodbcsql18 needs
# the Microsoft apt repository), then language tooling, then optional
# Azure extensions.
set -euo pipefail

# ---------------------------------------------------------------------------
# Microsoft apt repository + msodbcsql18 + unixodbc-dev
# ---------------------------------------------------------------------------
# Required by ``apps/mcp`` (pyodbc → SqlDatabaseClient, issue #17 onwards).
# Every Phase 2 issue from #17 to #22 touches the database via pyodbc and
# would otherwise fail at ``import pyodbc`` with "libodbc.so.2 not found".
if ! dpkg -s msodbcsql18 >/dev/null 2>&1; then
    echo "[post-create] Installing msodbcsql18 + unixodbc-dev..."
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | sudo gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg
    curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list \
        | sudo tee /etc/apt/sources.list.d/mssql-release.list >/dev/null
    sudo apt-get update -qq
    sudo ACCEPT_EULA=Y apt-get install -y -qq msodbcsql18 unixodbc-dev
else
    echo "[post-create] msodbcsql18 already installed, skipping."
fi

# ---------------------------------------------------------------------------
# Python tooling
# ---------------------------------------------------------------------------
python -m pip install --upgrade pip
pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Azure Developer CLI extensions
# ---------------------------------------------------------------------------
azd ext install azure.ai.agents || true
