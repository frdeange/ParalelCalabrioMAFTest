# ParalelCalabrioMAF v2

> Conversational assistant over Calabrio WFM data. 3-service architecture on Azure Container Apps fronted by APIM.

📖 **Anchor document**: [PLAN.md](./PLAN.md ) — every architectural decision lives there.

📚 **Deep technical docs**:
- [Architecture Deep Dive](./docs/architecture.md)
- [Agent and Service Flows](./docs/agent-and-service-flows.md)
- [MCP Tool Catalog](./docs/mcp-tool-catalog.md)
- [ADRs](./docs/adr/)

---

## Components

| Folder | Role | Stack |
|--------|------|-------|
| [apps/backend/](./apps/backend/ ) | MAF workflow orchestration + AG-UI endpoint | FastAPI + `agent_framework.ag_ui` |
| [apps/frontend/](./apps/frontend/ ) | Chat UI with Entra ID login | Next.js + MSAL |
| [apps/mcp/](./apps/mcp/ ) | Azure SQL access via MCP tools | FastMCP 3.x + sqlglot |
| [infra/](./infra/ ) | Bicep + APIM policies + azd | Bicep + APIM |
| [database/](./database/ ) | Schema + seed + extended properties | T-SQL |
| [docs/](./docs/ ) | Architecture, ADRs, runbooks | Markdown |
| [tests-e2e/](./tests-e2e/ ) | Playwright cross-component | TypeScript |

---

## Quickstart

Use one of these entry points depending on what you want to validate.

### Local REPL (legacy exploration runtime)

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in your values in .env
python main_local_multiturn.py
```

### Backend service

```bash
cd apps/backend
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

### MCP service

```bash
cd apps/mcp
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --port 8001
```

### Frontend service

```bash
cd apps/frontend
npm install
npm run dev
```

### Next steps per component

When each phase completes, see:
- `apps/backend/README.md` — Phase 1
- `apps/mcp/README.md` — Phase 2
- `apps/frontend/README.md` — Phase 3
- `infra/README.md` — Phase 5

---

## DevOps

- Branching: GitFlow-lite (`main` ← `develop` ← `feature/*`). Details in [docs/devops-setup.md](./docs/devops-setup.md ).
- Issues: use the templates in `.github/ISSUE_TEMPLATE/`.
- PRs: template auto-loaded, conventional commits required.

---

## Status

Roadmap authority remains [PLAN.md §13](./PLAN.md#13-project-phases).

Current implementation snapshot by component:

- Backend: Phase 1 scope implemented (`apps/backend/README.md`).
- MCP: Phase 2 core tooling implemented (`apps/mcp/README.md`).
- Frontend: active auth + chat implementation (`apps/frontend/README.md`).
- Infra/APIM end-to-end hardening and full deployment automation remain phase-driven and tracked in PLAN/ADRs.
