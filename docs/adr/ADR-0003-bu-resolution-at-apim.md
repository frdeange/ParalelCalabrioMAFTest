# ADR-0003 — BU resolution at APIM (4-layer fallback chain)

**Status**: Proposed (locked design, awaiting Phase 4 implementation)
**Date**: 2026-05-29 (proposal); to be revised to *Accepted* once the APIM policy fragment lands
**Decider**: Project owner
**Related**: ADR-0001 (overall architecture), ADR-0005 (HMAC APIM ↔ backend), [PLAN.md §7](../../PLAN.md#7-authentication-flow-and-bu-resolution), [apps/backend/app/deps/identity.py](../../apps/backend/app/deps/identity.py)

---

## Context

The Calabrio backend is multi-tenant by **business unit** (BU). Every authenticated request must carry a `bu_id`, and every SQL query the MCP issues must be scoped by `WHERE bu_id = @bu_id` (enforced server-side by the MCP — see [apps/mcp/app/servers/query.py](../../apps/mcp/app/servers/query.py)). The question this ADR answers is **who computes `bu_id` for a given request**.

Real-world constraints:

1. Not every user has a `bu_id` claim in their Entra ID token. Older Calabrio tenants federate without the custom claim populated.
2. Some integrations (POC environments, automated tests, on-call demos) need to drive a request without any token at all.
3. Once a request is past the public edge, the backend and MCP must be able to **trust** the resolved `bu_id` without re-running the resolution themselves — otherwise the resolution logic exists in 3 places and drifts.
4. The resolved value must be tamper-evident over the APIM → backend → MCP hops (see ADR-0005).

## Decision

**BU resolution lives in APIM**, implemented as a reusable policy fragment `bu-resolution.xml` consumed by `chat-api-{dev,prod}` and `mcp-api-{dev,prod}`. The fragment runs **after** JWT validation (so claims are available) and **before** HMAC signing (so the signed-header set includes the resolved `x-bu-id`).

The fragment tries four sources in order and stops on the first hit:

```
1. JWT claim   `extension_bu_id`  → if present, trust it
2. Domain map  Named Value `domain-to-bu-map` (JSON) →
                 lookup email-domain → bu_id
3. Header      `x-debug-bu`        → POC / dev only,
                 gated by an extra subscription key, never
                 forwarded on by APIM (the layer-1/2 result wins
                 in prod APIs because layer 3 is disabled there)
4. Default     Named Value `BU_ID_DEFAULT` → final fallback
```

Outcome contract: by the time a request leaves APIM, **`x-bu-id` is always present and is signed** as part of the canonical header list (see ADR-0005). Backend and MCP `identity` dependencies treat the header as authoritative after HMAC verify — they do **not** re-resolve the BU.

Layer 3 (`x-debug-bu`) is the explicit POC affordance: dev-only APIs accept it, prod APIs reject it (the policy fragment branches on `context.Api.Name`). It exists so we can demo the full flow before Entra ID claims are wired up, and so the e2e test suite can pin a BU without spinning up an Entra app.

## Positive consequences

- **Single resolver, single owner** — the routing logic lives in one APIM fragment, version-controlled in [infra/apim-policies/fragments/](../../infra/apim-policies/fragments/). Backend and MCP carry zero BU-resolution code; they only consume and trust the header.
- **Tamper-evident** — the resolved BU is signed alongside the other identity headers (ADR-0005). A client cannot smuggle a different `x-bu-id` past APIM without invalidating the HMAC.
- **Composable** — adding a new resolution source (e.g. a Graph lookup) is one edit to one fragment, no service redeploys.
- **POC-friendly** — `x-debug-bu` lets us run the full chain (FE → APIM → BE → MCP → DB) before any Entra ID custom claim is provisioned.
- **Observable** — APIM diagnostic logs record which layer fired (1/2/3/4) per request, so we can see how often the fallback path is actually used in prod.

## Negative consequences

- **APIM coupling** — the backend cannot be safely exposed *without* APIM in front. Local dev runs the backend with `HMAC_DISABLED=1` (see [apps/backend/app/settings.py](../../apps/backend/app/settings.py)) and a hardcoded `x-bu-id`; this is a known dev affordance, gated by an env flag the prod settings refuse to load.
- **Domain map is a Named Value** — adding a new tenant means editing the named value (or its KV-backed source) and redeploying APIM. Acceptable: the cadence is low (≤ once per quarter historically) and the change is reviewable. If frequency increases we move it to a Cosmos lookup.
- **Layer-3 is a foot-gun if mis-deployed** — the policy must branch on `context.Api.Name` correctly. Mitigated by an integration test in [tests-e2e/](../../tests-e2e/) that asserts `x-debug-bu` on the prod API name returns 401.

## Alternatives considered

### A. Resolve BU in the backend (read JWT claim + domain map + default)

Discarded for three reasons:
1. The MCP would also need the same logic (it talks to APIM independently from the backend), so we end up with two copies of the resolver.
2. The backend cannot easily reject malformed `x-debug-bu` requests without re-implementing the per-API gating that APIM does naturally.
3. We lose the "headers are signed" guarantee — if the backend computes BU after HMAC verify, the BU is not under the signature.

### B. Resolve BU in the frontend (compute and pass `x-bu-id` from the SPA)

Discarded outright. The frontend is untrusted by design; any header it sets is a hint, never an authority. Trusting a frontend-supplied `bu_id` would let any signed-in user query any tenant's data.

### C. APIM resolves, backend re-verifies

A compromise where APIM injects `x-bu-id` and the backend re-derives it from the JWT to make sure they match. Discarded because:
- It defeats the "single owner" property and re-introduces the multi-tenant resolver in two places.
- The HMAC-signed header chain (ADR-0005) already provides the tamper-evidence guarantee that the re-verify was trying to add.
- It does nothing about layer-2 (domain map) and layer-4 (default) requests, where the JWT does not carry the answer and the backend would still have to trust APIM.

## Open questions (to revisit before flipping to Accepted)

- **Multi-BU users**: a single Entra account could legitimately belong to several BUs. Today we ship single-BU only; the resolver returns one value. If multi-BU lands we need either a `x-bu-id-list` header + per-request selection at the FE, or a session-scoped selection persisted in the AG-UI state.
- **JWT claim name**: `extension_bu_id` is the Calabrio convention; if Entra ID External tenants use a different shape we update the JSONPath in the fragment.
- **Named value vs KV reference**: `domain-to-bu-map` is currently planned as a plain Named Value. If it grows past a few KB or starts holding secrets, move to a Key Vault reference.

## Implementation plan (Phase 4)

- [ ] [infra/apim-policies/fragments/bu-resolution.xml](../../infra/apim-policies/fragments/) — the 4-layer fragment
- [ ] Wire the fragment into `chat-api-dev`, `chat-api-prod`, `mcp-api-dev`, `mcp-api-prod`
- [ ] Named value `domain-to-bu-map` (JSON) + `BU_ID_DEFAULT`
- [ ] E2E test: dev API accepts `x-debug-bu`, prod API returns 401
- [ ] E2E test: JWT claim wins over `x-debug-bu` even on dev API
- [ ] Diagnostic logging: emit `bu.resolved.layer = 1|2|3|4` on every request

## References

- [PLAN.md §7 — Authentication flow and BU resolution](../../PLAN.md#7-authentication-flow-and-bu-resolution)
- [apps/backend/app/deps/identity.py](../../apps/backend/app/deps/identity.py) — the consumer side, already implemented in Phase 1
- ADR-0005 — HMAC APIM ↔ backend (the signature that protects the resolved header)
