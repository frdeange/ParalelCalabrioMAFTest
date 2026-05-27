# Frontend — apps/frontend

> Chat UI: Next.js 15 + CopilotKit + MSAL (Entra ID). Container App #2.

📖 See [PLAN.md §6.2](../../PLAN.md#62-frontend-appsfrontend).

## Status

**Phase 0** — empty skeleton. Implementation in **Phase 3**.

## Planned structure

```
app/                       # Next.js App Router
├── layout.tsx
├── page.tsx               # landing/login
├── chat/page.tsx
└── api/copilotkit/route.ts
components/
├── chat/
├── auth/MsalProvider.tsx
└── ui/                    # shadcn
lib/
├── msal-config.ts
└── api-client.ts
tests/                     # vitest unit
e2e/                       # playwright
Dockerfile
package.json
.env.local.example
```

## Run (once it exists)

```bash
cd apps/frontend
pnpm install
cp .env.local.example .env.local
pnpm dev
```

## Environment variables

See [PLAN.md §14 Frontend](../../PLAN.md#14-environment-variables-inventory).
