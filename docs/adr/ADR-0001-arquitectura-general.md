# ADR-0001 — Arquitectura general v2

**Estado**: Aceptado
**Fecha**: 2026-05-27
**Decisor**: Owner del proyecto

---

## Contexto

El proyecto v1 (en repo separado `CalabrioMAFVersion`, ya archivado) usaba:
- Backend Foundry hosted agent (single binary).
- MCP propio acoplado al backend.
- Frontend Angular.
- APIM monolítico (1 API por entorno).
- BU resolución parcial vía claim, sin fallback.

Tras la iteración local-multiturn ([main_local_multiturn.py](../../main_local_multiturn.py )) validamos un workflow MAF de 3 steps que funciona end-to-end con Cosmos history. Toca decidir cómo evolucionar a una arquitectura productiva.

## Decisión

Adoptamos arquitectura **3-service monorepo** sobre **Azure Container Apps**, con **APIM por delante** de backend y MCP:

1. **Frontend**: Next.js 15 + CopilotKit + MSAL → 1 Container App.
2. **Backend**: FastAPI + `agent_framework.ag_ui` + Cosmos history → 1 Container App.
3. **MCP**: FastMCP 3.x con `mount(prefix=...)` → 1 Container App.
4. **APIM**: 4 APIs (`chat-api-dev`, `chat-api-prod`, `mcp-api-dev`, `mcp-api-prod`) + 4 policy fragments reusables.

Tabla completa de decisiones en [PLAN.md §3](../../PLAN.md#3-decisiones-arquitectónicas-locked).

## Consecuencias positivas

- **Escalado independiente**: el frontend puede escalar en concurrencia de usuarios sin afectar el MCP.
- **Deploys aislados**: cambiar el MCP no fuerza redeploy del backend.
- **Revisión de PRs atómica**: monorepo permite que un PR toque contrato BE↔MCP coherentemente.
- **Stack moderno**: Next.js 15 + CopilotKit ofrece integración nativa con AG-UI sin código glue.
- **APIM como única superficie pública**: defensa en profundidad, observabilidad central, rate limiting.

## Consecuencias negativas

- **Latencia adicional**: APIM añade ~30-80ms por hop. Aceptable para chat (humano no nota < 200ms).
- **3 imágenes Docker** que mantener vs 1 monolito.
- **Multi-cloud-init en CI**: cada componente con su pipeline.
- **APIM Standard SKU**: coste ~$700/mes (developer SKU como alternativa para POC, ~$50/mes pero sin SLA).

## Alternativas consideradas

### A) Foundry Hosted Agent (heredado de v1)
- Pros: 1 binario, gestión simple.
- Contras: lock-in con runtime Foundry; testing local complicado; no permite frontend con AG-UI nativo; debugging opaco. **Descartado**.

### B) Monolito FastAPI (backend + MCP en mismo proceso)
- Pros: menos infra, menos red.
- Contras: viola separation of concerns; reuso de MCP por otros clientes (futuro) imposible; escalado acoplado. **Descartado**.

### C) Backend en App Service + MCP en Container Apps (mix)
- Pros: App Service tiene slot swapping.
- Contras: stack inconsistente; YAGNI (slot swap se simula con Container Apps revisions). **Descartado**.

### D) Frontend en Static Web App (en vez de Container App)
- Pros: barato, CDN global.
- Contras: limita SSR/RSC de Next.js 15 (que es donde Next.js brilla); además SWA con backend separado replica complejidad. **Posiblemente reconsiderar en v3** si la app sigue siendo casi-estática.

## Implementación

Ver [PLAN.md §13 Phases](../../PLAN.md#13-fases-del-proyecto).

## Referencias

- [PLAN.md](../../PLAN.md ) — este es el documento ancla.
- [Microsoft Agent Framework — AG-UI](https://github.com/microsoft/agent-framework/tree/main/python/packages/ag_ui)
- [Azure Container Apps networking](https://learn.microsoft.com/en-us/azure/container-apps/networking)
