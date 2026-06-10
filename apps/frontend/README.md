# Frontend - apps/frontend

Next.js web client for ParalelCalabrioMAF v2.

This service is responsible for:

- Entra ID authentication (MSAL redirect flow).
- Route protection and session continuity.
- Real-time chat UX over AG-UI SSE.
- Optional POC Business Unit override header emission.

## 1. Runtime Role in the Platform

The frontend sits at the user edge and speaks to the backend AG-UI endpoint (`/agui`) through APIM. It does not execute SQL and does not orchestrate model workflows.

High-level responsibilities:

1. Login and token lifecycle.
2. Chat message capture and rendering.
3. AG-UI event stream handling.
4. User-friendly progress and error states.

## 2. Main User Flows

### 2.1 Login

1. User lands on `/login`.
2. User signs in with Microsoft.
3. MSAL redirects back to configured redirect URI.
4. Authenticated users are routed to `/chat`.

### 2.2 Chat Turn

1. User submits a message.
2. Frontend acquires API token silently.
3. Frontend sends full message history + stable `thread_id` to `/agui`.
4. Frontend parses SSE frames and updates UI incrementally.
5. On completion, loading/progress states are cleared.

## 3. AG-UI Event Handling

The frontend consumes `data: { ... }` SSE frames and reacts to these event types:

- `STEP_STARTED`: map workflow executor id to a friendly progress label.
- `TEXT_MESSAGE_CONTENT`: append streamed token text.
- `RUN_ERROR`: show a recoverable error message.
- `RUN_FINISHED`: stop loading state.

Other lifecycle events are ignored when they do not carry UI payload.

## 4. Business Unit (POC) Behavior

When POC override is enabled, frontend can send `x-debug-bu` with the selected BU value. APIM policy controls whether this is accepted.

Important: this is an environment-governed behavior and must not be treated as trusted identity by services behind APIM.

## 5. Project Structure (Frontend)

```
app/
	layout.tsx            # App shell and providers
	page.tsx              # Root redirect logic
	login/page.tsx        # Login experience
	chat/page.tsx         # Chat workspace
components/
	Chat.tsx              # Main chat UI + streaming rendering
	BuSelector.tsx        # BU selector (POC behavior)
	auth/
		AuthGuard.tsx       # Route guard behavior
		MsalProvider.tsx    # MSAL provider wiring
lib/
	agui/client.ts        # SSE protocol client and event mapping
	use-auth.ts           # Auth helper hook
	msal-config.ts        # MSAL and scopes config
	bu.ts                 # BU selection utilities
```

## 6. Local Development

Install and run:

```bash
cd apps/frontend
npm install
npm run dev
```

Default local URL: `http://localhost:3000`

## 7. Environment Variables

Key variables (see `.env.local.example` in this folder):

- `NEXT_PUBLIC_AZURE_CLIENT_ID`
- `NEXT_PUBLIC_AZURE_TENANT_ID`
- `NEXT_PUBLIC_REDIRECT_URI`
- `NEXT_PUBLIC_API_SCOPE`
- `NEXT_PUBLIC_BACKEND_API_URL`

The MSAL configuration fails fast in browser runtime when required auth variables are missing.

## 8. Scripts

```bash
npm run dev
npm run build
npm run start
npm run lint
npm run test
npm run test:watch
npm run test:coverage
npm run e2e
npm run e2e:ui
npm run e2e:report
```

## 9. Testing

### 9.1 Unit Tests

- Framework: Vitest + React Testing Library.
- Test location: `__tests__/` mirroring `components/` and `lib/`.
- Coverage gate is configured in `vitest.config.ts`.

Run unit suite:

```bash
npm run test
```

Coverage run:

```bash
npm run test:coverage
```

### 9.2 End-to-End Tests

Playwright scenarios are under `e2e/`.

Run e2e suite:

```bash
npm run e2e
```

## 10. Operational Notes

- The frontend is stateless; conversation continuity is server-side and keyed by `thread_id`.
- Authentication and API token scope must match APIM/backend expectations.
- Frontend should never embed backend trust logic; trust contracts are enforced at APIM and backend identity dependencies.

## 11. Related Documentation

- Project architecture deep dive: `docs/architecture.md`
- Runtime sequence diagrams: `docs/agent-and-service-flows.md`
- Global plan and phased roadmap: `PLAN.md`
- Backend details: `apps/backend/README.md`
- MCP details: `apps/mcp/README.md`
