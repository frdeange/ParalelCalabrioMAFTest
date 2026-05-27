# tests-e2e — Cross-component end-to-end tests

> Playwright tests que ejercitan el flujo completo: Frontend → APIM → Backend → MCP → SQL.

📖 Ver [PLAN.md §11](../PLAN.md#11-testing).

## Estado

**Phase 0** — esqueleto. Implementación en **Phase 6**.

## Escenarios planificados (5 críticos)

1. `auth-flow.spec.ts` — MSAL login redirect OK
2. `chat-happy-path.spec.ts` — pregunta de datos → respuesta con filas correctas
3. `chat-conversational.spec.ts` — pregunta conversacional (no SQL) → respuesta directa
4. `bu-isolation.spec.ts` — usuario de BU 1 no puede ver datos de BU 2
5. `error-paths.spec.ts` — query inválida → mensaje user-friendly

## Run (cuando exista)

```bash
cd tests-e2e
pnpm install
pnpm exec playwright install --with-deps chromium
E2E_BASE_URL=https://<dev-frontend>.azurecontainerapps.io pnpm exec playwright test
```
