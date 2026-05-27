# ParalelCalabrioMAF v2

> Conversational assistant over Calabrio WFM data. 3-service architecture on Azure Container Apps fronted by APIM.

📖 **Anchor document**: [PLAN.md](./PLAN.md ) — every architectural decision lives there.

---

## Components

| Folder | Role | Stack |
|--------|------|-------|
| [apps/backend/](./apps/backend/ ) | MAF workflow orchestration + AG-UI endpoint | FastAPI + `agent_framework.ag_ui` |
| [apps/frontend/](./apps/frontend/ ) | Chat UI with Entra ID login | Next.js 15 + CopilotKit + MSAL |
| [apps/mcp/](./apps/mcp/ ) | Azure SQL access via MCP tools | FastMCP 3.x + sqlglot |
| [infra/](./infra/ ) | Bicep + APIM policies + azd | Bicep + APIM |
| [database/](./database/ ) | Schema + seed + extended properties | T-SQL |
| [docs/](./docs/ ) | Architecture, ADRs, runbooks | Markdown |
| [tests-e2e/](./tests-e2e/ ) | Playwright cross-component | TypeScript |

---

## Quickstart

> **Phase 0 (current)**: only the skeleton is in place. The components do not run yet.
> The active reference runtime is [main_local_multiturn.py](./main_local_multiturn.py ) (local REPL) until Phase 1 lands.

### Local REPL (legacy exploration runtime)

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in your values in .env
python main_local_multiturn.py
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

We are in **Phase 0 — Scaffold**. See [PLAN.md §13](./PLAN.md#13-project-phases) for the full roadmap.
