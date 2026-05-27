# tests-e2e — Cross-component end-to-end tests

> Playwright tests that exercise the full flow: Frontend → APIM → Backend → MCP → SQL.

📖 See [PLAN.md §11](../PLAN.md#11-testing).

## Status

**Phase 0** — skeleton. Implementation in **Phase 6**.

## Planned scenarios (5 critical)

1. `auth-flow.spec.ts` — MSAL login redirect OK
2. `chat-happy-path.spec.ts` — data question → answer with correct rows
3. `chat-conversational.spec.ts` — conversational question (no SQL) → direct answer
4. `bu-isolation.spec.ts` — a user in BU 1 cannot see data from BU 2
5. `error-paths.spec.ts` — invalid query → user-friendly message

## Run (once it exists)

```bash
cd tests-e2e
pnpm install
pnpm exec playwright install --with-deps chromium
E2E_BASE_URL=https://<dev-frontend>.azurecontainerapps.io pnpm exec playwright test
```
