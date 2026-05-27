# Backend — apps/backend

> Orquestación MAF workflow + endpoint AG-UI. Container App #1.

📖 Ver [PLAN.md §6.1](../../PLAN.md#61-backend-appsbackend) para responsabilidades y endpoint.

## Estado

**Phase 0** — solo esqueleto. Implementación real en **Phase 1** (refactor de [main_local_multiturn.py](../../main_local_multiturn.py )).

## Estructura prevista

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

## Run (cuando exista)

```bash
cd apps/backend
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

## Variables de entorno

Ver [PLAN.md §14 Backend](../../PLAN.md#14-inventario-de-variables-de-entorno).
