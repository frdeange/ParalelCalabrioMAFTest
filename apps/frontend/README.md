# Frontend — apps/frontend

> UI chat Next.js 15 + CopilotKit + MSAL (Entra ID). Container App #2.

📖 Ver [PLAN.md §6.2](../../PLAN.md#62-frontend-appsfrontend).

## Estado

**Phase 0** — esqueleto vacío. Implementación en **Phase 3**.

## Estructura prevista

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

## Run (cuando exista)

```bash
cd apps/frontend
pnpm install
cp .env.local.example .env.local
pnpm dev
```

## Variables de entorno

Ver [PLAN.md §14 Frontend](../../PLAN.md#14-inventario-de-variables-de-entorno).
