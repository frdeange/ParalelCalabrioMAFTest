# ADR-0001 — Overall architecture v2

**Status**: Accepted
**Date**: 2026-05-27
**Decider**: Project owner

---

## Context

The v1 project (in a separate repo `CalabrioMAFVersion`, now archived) used:
- Foundry hosted agent backend (single binary).
- An MCP coupled to the backend.
- An Angular frontend.
- A monolithic APIM (1 API per environment).
- Partial BU resolution via claim, with no fallback.

After the local-multiturn iteration ([main_local_multiturn.py](../../main_local_multiturn.py )) we validated a 3-step MAF workflow that runs end-to-end with Cosmos history. Time to decide how to evolve to a production-ready architecture.

## Decision

We adopt a **3-service monorepo** architecture on **Azure Container Apps**, with **APIM in front** of backend and MCP:

1. **Frontend**: Next.js 15 + CopilotKit + MSAL → 1 Container App.
2. **Backend**: FastAPI + `agent_framework.ag_ui` + Cosmos history → 1 Container App.
3. **MCP**: FastMCP 3.x with `mount(prefix=...)` → 1 Container App.
4. **APIM**: 4 APIs (`chat-api-dev`, `chat-api-prod`, `mcp-api-dev`, `mcp-api-prod`) + 4 reusable policy fragments.

Full decision table in [PLAN.md §3](../../PLAN.md#3-locked-architectural-decisions).

## Positive consequences

- **Independent scaling**: the frontend can scale on user concurrency without affecting the MCP.
- **Isolated deploys**: changing the MCP does not force a backend redeploy.
- **Atomic PR review**: monorepo lets a PR change a BE↔MCP contract coherently.
- **Modern stack**: Next.js 15 + CopilotKit offers native AG-UI integration with no glue code.
- **APIM as the only public surface**: defense in depth, central observability, rate limiting.

## Negative consequences

- **Extra latency**: APIM adds ~30-80ms per hop. Acceptable for chat (humans don't notice < 200ms).
- **3 Docker images** to maintain vs 1 monolith.
- **Multi-cloud-init in CI**: each component with its own pipeline.
- **APIM Standard SKU**: ~$700/month (developer SKU as alternative for POC, ~$50/month but no SLA).

## Alternatives considered

### A) Foundry Hosted Agent (inherited from v1)
- Pros: 1 binary, simple ops.
- Cons: lock-in with Foundry runtime; local testing painful; no native AG-UI frontend; opaque debugging. **Discarded**.

### B) FastAPI monolith (backend + MCP in same process)
- Pros: less infra, less network.
- Cons: violates separation of concerns; future MCP reuse by other clients impossible; coupled scaling. **Discarded**.

### C) Backend in App Service + MCP in Container Apps (mix)
- Pros: App Service has slot swapping.
- Cons: inconsistent stack; YAGNI (slot swap can be simulated with Container Apps revisions). **Discarded**.

### D) Frontend in Static Web App (instead of Container App)
- Pros: cheap, global CDN.
- Cons: limits Next.js 15 SSR/RSC (where Next.js shines); SWA + separate backend replicates complexity. **Possibly revisit in v3** if the app stays mostly static.

## Implementation

See [PLAN.md §13 Phases](../../PLAN.md#13-project-phases).

## References

- [PLAN.md](../../PLAN.md ) — anchor document.
- [Microsoft Agent Framework — AG-UI](https://github.com/microsoft/agent-framework/tree/main/python/packages/ag_ui)
- [Azure Container Apps networking](https://learn.microsoft.com/en-us/azure/container-apps/networking)
