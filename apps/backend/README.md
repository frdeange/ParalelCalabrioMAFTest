# Backend — apps/backend

> MAF workflow orchestration + AG-UI endpoint. Container App #1.

📖 See [PLAN.md §6.1](../../PLAN.md#61-backend-appsbackend) for responsibilities and endpoint.

## Status

**Phase 1 — in progress.** Package scaffold and typed settings are live; the
FastAPI app, MAF workflow and the remaining modules land in subsequent
Phase 1 issues (see milestone [Phase 1 — Backend](https://github.com/frdeange/ParalelCalabrioMAFTest/milestone/2)).

## Structure

```
app/
├── __init__.py    ✅ done
├── settings.py    ✅ done — pydantic-settings (PLAN.md §14)
├── main.py        ⏳ FastAPI app + ag-ui endpoint
├── workflow.py    ⏳ 3-step MAF workflow
├── identity.py    ⏳ FastAPI dep: parse x-user-* + verify HMAC
├── history.py     ⏳ CosmosHistoryProvider wrapper
└── tools.py       ⏳ MCPStreamableHTTPTool factory
tests/
├── conftest.py    ✅ shared fixtures (required_env)
└── test_settings.py ✅
pyproject.toml     ✅
.env.example       ✅
Dockerfile         ⏳
```

## Quickstart

```bash
cd apps/backend
pip install -e ".[dev]"
cp .env.example .env          # fill in the blanks
pytest                        # 11 settings tests pass
ruff check .                  # lint
```

Once the FastAPI app lands:

```bash
uvicorn app.main:app --reload --port 8000
```

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
