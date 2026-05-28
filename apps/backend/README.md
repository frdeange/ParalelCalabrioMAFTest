# Backend — apps/backend

> MAF workflow orchestration + AG-UI endpoint. Container App #1.

📖 See [PLAN.md §6.1](../../PLAN.md#61-backend-appsbackend) for responsibilities and endpoint.

## Status

**Phase 1 — feature-complete.** Package scaffold, typed settings, the
three-step MAF workflow, the MCP tool factory, the history provider
abstraction, the FastAPI app + AG-UI endpoint, the HMAC identity
dependency, the multi-stage Dockerfile and the pytest+coverage CI
gate are all in place (see milestone [Phase 1 — Backend](https://github.com/frdeange/ParalelCalabrioMAFTest/milestone/2)).

## Structure

```
app/
├── __init__.py        ✅
├── settings.py        ✅ pydantic-settings (PLAN.md §14)
├── main.py            ✅ FastAPI app factory + /healthz (#12)
├── lifespan.py        ✅ production lifespan (telemetry, MCP, AG-UI mount) (#12)
├── deps/
│   └── identity.py    ✅ verify x-user-* + HMAC → Caller (#13)
├── history/
│   ├── protocol.py    ✅ HistoryProvider Protocol (#10)
│   ├── memory.py      ✅ InMemoryHistoryProvider (dev/tests)
│   ├── cosmos.py      ✅ CosmosHistoryProvider wrapper (prod)
│   └── factory.py     ✅ provider selection by settings
├── mcp/
│   └── factory.py     ✅ MCPStreamableHTTPTool factory (#11)
├── security/
│   └── hmac.py        ✅ canonical payload + HMAC compute/verify (#13)
└── workflow/          ✅ 3-step MAF workflow (#8)
    ├── build.py       ✅ SequentialBuilder assembly
    ├── intent.py      ✅ IntentStep
    ├── sql_builder.py ✅ SqlBuilderStep
    ├── query_executor.py ✅ QueryExecutorStep
    ├── schemas.py     ✅ IntentBundle / SqlBundle / IntentResult / SqlPlan
    ├── prompts.py     ✅ prompt templates
    └── _helpers.py    ✅ templating, usage tracking, history windowing
tests/                 ✅ 108 tests, 94% coverage (#14)
pyproject.toml         ✅
.env.example           ✅
Dockerfile             ✅ (#15)
.dockerignore          ✅ (#15)
```

## Quickstart (local)

```bash
cd apps/backend
pip install -e ".[dev]"
cp .env.example .env          # fill in the blanks
pytest                        # 108 tests — settings, workflow, MCP, history, HMAC, identity, AG-UI
ruff check .                  # lint
uvicorn app.main:app --reload --port 8000
```

`uvicorn app.main:app` requires the env vars listed in [`.env.example`](.env.example).
The production lifespan opens a real `DefaultAzureCredential` and two
MCP sessions on startup, so for fully isolated runs use the testsuite
(`pytest`) which mocks those out.

## Docker

The backend ships as a multi-stage container image (`python:3.13-slim-bookworm`)
designed for **Azure Container Apps** and Docker Desktop. The runtime stage
runs as a non-root user (uid 10001) and uses gunicorn with uvicorn workers.

### Build

From the **repository root**:

```bash
docker build -t backend apps/backend
```

The build context is scoped to `apps/backend/` so only the package source,
`pyproject.toml`, `README.md` and a few build hints are sent to the daemon
(see [`.dockerignore`](.dockerignore)). The resulting image weighs **~253 MB**
(meets the `< 250 MB` aspiration of #15 within noise), down from the
~1.2 GB the `agent-framework` meta package produced — see #53 for the
curated sub-package list and rationale.

### Run

```bash
docker run --rm -p 8000:8000 \
    --env-file .env \
    backend
```

The container listens on `0.0.0.0:8000` and exposes the FastAPI app via
`gunicorn --worker-class uvicorn.workers.UvicornWorker app.main:app`.
It expects every variable listed in [PLAN.md §14](../../PLAN.md#14-environment-variables-inventory)
(Foundry endpoint, MCP URL, Cosmos endpoint, App Insights connection
string, HMAC shared secret); the lifespan crashes fast on startup if a
required one is missing.

### Tunables (env vars consumed by the entrypoint)

| Variable | Default | Purpose |
|----------|---------|---------|
| `PORT` | `8000` | Bind port for gunicorn. |
| `GUNICORN_WORKERS` | `2` | Number of worker processes. Tune to ~`(2 × cpu) + 1` in ACA. |
| `GUNICORN_TIMEOUT` | `120` | Worker timeout (seconds). |

Plus every application setting listed in [PLAN.md §14](../../PLAN.md#14-environment-variables-inventory)
and reproduced in [`.env.example`](.env.example).

### Image hygiene

* Base image: `python:3.13-slim-bookworm` (CVE patched ~weekly upstream).
* Two-stage build: build tools (`build-essential`, `git`) live only in the
  builder stage and are dropped from the runtime layer.
* Dependencies resolved with `pip install --target=/install` to keep the
  runtime tree self-contained and reproducible.
* `__pycache__`, test packages and `.dist-info` metadata stripped from the
  vendored tree to claw back ~20 MB.
* Runs as `app:app` with uid/gid `10001` — satisfies CIS Docker Benchmark
  4.1 and Defender for Cloud's "container should not run as root" rule.

### Local smoke test

```bash
docker run --rm backend python -c "from app.workflow import build_workflow; print('workflow OK')"
```

This bypasses the FastAPI entrypoint and exercises only the package
import path — handy to verify the image is sane without supplying the
full env var set required by the real lifespan.

## Environment variables

See [PLAN.md §14 Backend](../../PLAN.md#14-environment-variables-inventory)
and the local [`.env.example`](.env.example) for descriptions of every
variable.

## Settings access

```python
from app.settings import settings           # lazy singleton

settings.foundry_project_endpoint
settings.bu_id_default                       # int (coerced)
settings.hmac_shared_secret.get_secret_value()  # SecretStr
```

FastAPI dependency form (preferred in handlers, easier to override in tests):

```python
from fastapi import Depends
from app.settings import Settings, get_settings

@app.get("/healthz")
def healthz(s: Settings = Depends(get_settings)) -> dict[str, str]:
    return {"service": s.otel_service_name}
```
