# ParalelCalabrioMAF v2

> Asistente conversacional sobre datos Calabrio WFM. Arquitectura de 3 servicios sobre Azure Container Apps con APIM por delante.

📖 **Documento ancla**: [PLAN.md](./PLAN.md ) — toda decisión arquitectónica vive ahí.

---

## Componentes

| Carpeta | Rol | Stack |
|---------|-----|-------|
| [apps/backend/](./apps/backend/ ) | Orquestación MAF workflow + endpoint AG-UI | FastAPI + `agent_framework.ag_ui` |
| [apps/frontend/](./apps/frontend/ ) | UI de chat con login Entra ID | Next.js 15 + CopilotKit + MSAL |
| [apps/mcp/](./apps/mcp/ ) | Acceso a Azure SQL via tools MCP | FastMCP 3.x + sqlglot |
| [infra/](./infra/ ) | Bicep + APIM policies + azd | Bicep + APIM |
| [database/](./database/ ) | Schema + seed + extended properties | T-SQL |
| [docs/](./docs/ ) | Architecture, ADRs, runbooks | Markdown |
| [tests-e2e/](./tests-e2e/ ) | Playwright cross-component | TypeScript |
| [OLD/](./OLD/ ) | Archivado, no editar | — |

---

## Quickstart

> **Phase 0 (actual)**: solo está montado el esqueleto. Los componentes aún no funcionan.
> El runtime activo de referencia es [main_local_multiturn.py](./main_local_multiturn.py ) (REPL local) hasta que termine Phase 1.

### Local REPL (legacy de la fase de exploración)

```bash
pip install -r requirements.txt
cp .env.example .env
# editar .env con tus valores
python main_local_multiturn.py
```

### Próximos pasos por componente

Cuando completemos cada fase, ver:
- `apps/backend/README.md` — Phase 1
- `apps/mcp/README.md` — Phase 2
- `apps/frontend/README.md` — Phase 3
- `infra/README.md` — Phase 5

---

## DevOps

- Branching: GitFlow-lite (`main` ← `develop` ← `feature/*`). Detalle en [docs/devops-setup.md](./docs/devops-setup.md ).
- Issues: usar templates en `.github/ISSUE_TEMPLATE/`.
- PRs: template auto-cargado, conventional commits obligatorios.

---

## Estado

Trabajamos en **Phase 0 — Scaffold**. Ver [PLAN.md §13](./PLAN.md#13-fases-del-proyecto) para roadmap completo.
