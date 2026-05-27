# Backend — apps/backend

> MAF workflow orchestration + AG-UI endpoint. Container App #1.

📖 See [PLAN.md §6.1](../../PLAN.md#61-backend-appsbackend) for responsibilities and endpoint.

## Status

**Phase 0** — skeleton only. Actual implementation in **Phase 1** (refactor of [main_local_multiturn.py](../../main_local_multiturn.py )).

## Planned structure

```
app/
├── main.py        # FastAPI app + ag-ui endpoint
├── workflow.py    # 3-step MAF workflow
├── identity.py    # FastAPI dep: parse x-user-* + verify HMAC
├── history.py     # CosmosHistoryProvider wrapper
├── tools.py       # MCPStreamableHTTPTool factory
└── settings.py    # pydantic-settings
tests/
Dockerfile
pyproject.toml
.env.example
```

## Run (once it exists)

```bash
cd apps/backend
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

## Environment variables

See [PLAN.md §14 Backend](../../PLAN.md#14-environment-variables-inventory).
