# ADR-0005 — HMAC signing on the APIM ↔ backend / APIM ↔ MCP hops

**Status**: Accepted (verify side); Proposed (sign side, Phase 4)
**Date**: 2026-05-29
**Decider**: Project owner
**Related**: ADR-0001 (overall architecture), ADR-0003 (BU resolution at APIM), [PLAN.md §7](../../PLAN.md#7-authentication-flow-and-bu-resolution), [apps/backend/app/security/hmac.py](../../apps/backend/app/security/hmac.py), [apps/backend/app/deps/identity.py](../../apps/backend/app/deps/identity.py)

---

## Context

The Calabrio production topology is *frontend → APIM → backend → APIM → MCP*. APIM is the only public surface; backend and MCP are private Container Apps. Two facts make a pure-TLS-and-JWT story insufficient:

1. **APIM enriches the request with derived headers after JWT validation.** `x-user-oid`, `x-user-email`, `x-user-name`, `x-bu-id` are computed (the last by the BU resolver — ADR-0003) and forwarded to backend/MCP. The JWT does not, and cannot, attest to those derived values.
2. **The private Container App endpoints can leak.** Misconfigured network rules, a stale ingress, or a forgotten `--ingress external` flag is enough to make the backend or MCP reachable without APIM. If they trust their request headers unconditionally, anyone can impersonate any user.

We therefore need a way for the backend and MCP to **prove the request came through APIM** (so the four identity headers can be trusted) without re-running JWT validation on the inside (which would couple every internal service to Entra ID and double the latency).

## Decision

APIM **signs**, backend and MCP **verify**, an HMAC-SHA256 over a fixed canonical string built from the four identity headers. The shared secret lives in Key Vault and is exposed to APIM (via Named Value KV reference) and to the consumer Container Apps (via env var bound to the same KV secret).

### Canonical payload (locked wire contract)

```
x-user-oid + "\n" + x-user-email + "\n" + x-user-name + "\n" + x-bu-id
```

* **Order is fixed.** Both producer and consumer must serialise headers in this order — the ordering is implemented as a module-level tuple in [apps/backend/app/security/hmac.py](../../apps/backend/app/security/hmac.py).
* **Separator is `\n`.** Cannot occur inside Entra-issued GUIDs / RFC-5322 strings so we do not need an escaping rule.
* **Body is NOT signed.** AG-UI bodies are large, user-controlled JSON; signing them would either force APIM to buffer the request (perf hit + memory pressure) or open canonicalisation bugs. We sign **identity**, not content — the threat model is "trust the headers", not "trust the payload".
* The signature is the URL-safe Base64 of `HMAC_SHA256(secret, payload)`, padding stripped. Round-trips through HTTP headers without quoting; length is implicit (256 bits → 43 chars).

### Roles

| Component | Role | Phase |
|---|---|---|
| APIM `hmac-sign.xml` fragment | Computes and injects `x-apim-signature` after BU resolution | Phase 4 (planned) |
| Backend `verify_signature(...)` | Validates `x-apim-signature` over the canonical payload before any business logic runs | Phase 1 — implemented in [apps/backend/app/security/hmac.py](../../apps/backend/app/security/hmac.py), wired in [apps/backend/app/deps/identity.py](../../apps/backend/app/deps/identity.py) |
| MCP `verify_signature(...)` | Same as backend, on the MCP side | Phase 2 prep / Phase 4 wire-up |

Both verify implementations use `hmac.compare_digest` so the comparison is constant-time and cannot leak the secret via timing.

### Dev mode

Local dev runs the backend without APIM in front. `HMAC_DISABLED=1` (env flag in [apps/backend/app/settings.py](../../apps/backend/app/settings.py)) bypasses verification and the dev runner injects fake identity headers. Production settings refuse to load with the flag set.

### Key rotation

The shared secret lives in Key Vault. Rotation is a two-step:
1. Issue a new secret version, update the KV reference on the consumer side first (backend + MCP accept two valid secrets briefly — the verify path tries the rotated secret then the previous version).
2. Update the APIM Named Value to the new version, then strip the previous-version acceptance.

The dual-acceptance window keeps the rotation zero-downtime. The verify implementation in [apps/backend/app/security/hmac.py](../../apps/backend/app/security/hmac.py) is shaped to make this trivial when Phase 4 lands the rotation runbook.

## Positive consequences

- **Defense in depth.** Even if the backend or MCP ingress accidentally goes public, requests without a valid `x-apim-signature` are rejected at the identity dependency before reaching any business logic.
- **Tamper-evident identity headers.** A client cannot tweak `x-bu-id` to spy on another tenant — the signature is computed over it and verification fails.
- **No JWT validation on the inside.** Internal services do not need Entra ID network egress, do not need to know the tenant configuration, do not pay the JWKS-fetch latency. APIM is the only thing that talks to Entra ID.
- **Cheap to verify.** SHA-256 of ~150 bytes is microseconds on modern x86; no measurable per-request impact.
- **Testable on both sides.** `compute_signature(...)` is exported so the APIM fragment can be unit-tested against the exact bytes the backend will verify — no "two implementations of the same thing" risk.

## Negative consequences

- **Shared secret in two places.** Anyone with KV read on the secret can forge a request. Mitigated by minimal KV RBAC (only the backend MI, the MCP MI, and the APIM MI can read; ops humans cannot). Rotation runbook is part of the Phase 4 deliverable.
- **Body is unsigned.** A man-in-the-middle (which would need TLS termination) could swap the body. Acceptable: TLS is the layer that protects the body, and APIM's mTLS-or-IP-allowlist to the backend ingress eliminates the MitM in practice.
- **Identity headers are not encrypted.** Anyone with traffic capture sees the user's OID and email. Same threat surface as the JWT they replace; mitigated by TLS end-to-end.
- **Adds one dependency to identity parsing.** A bug in `verify_signature` becomes an outage. Mitigated by unit tests in [apps/backend/tests/test_hmac.py](../../apps/backend/tests/test_hmac.py) and the explicit `HMAC_DISABLED=1` escape hatch for emergencies (gated by a code-level assertion that prevents accidental prod use).

## Alternatives considered

### A. mTLS between APIM and the Container Apps

APIM presents a client certificate; backend/MCP validate the chain. Discarded for two reasons:
1. The Container App ingress does not natively validate client certs; we would need a sidecar (nginx / Envoy) or a layer-7 verification in the app itself — more moving parts.
2. mTLS proves the *connection* originated from APIM but says nothing about the *headers* APIM injected. A bug in APIM that overwrites `x-bu-id` is not caught by mTLS. HMAC signing closes that loophole.

mTLS is **complementary** and can be layered on top later if we ever want connection-level proof too. It does not replace this ADR.

### B. Re-validate the JWT inside the backend

Strip the four derived headers, re-validate the JWT, recompute everything internally. Discarded:
- Doubles the latency per request (JWKS fetch + signature verify on every hop).
- Internal services now need Entra ID network egress, breaking the "private VNet, no Internet" property of Container Apps.
- Derived headers (notably `x-bu-id` from the 4-layer BU resolver — ADR-0003) cannot be derived from the JWT alone in layers 2/3/4. The internal service would still need to trust APIM for those, defeating the purpose.

### C. Signed JWT issued by APIM for downstream calls

APIM mints a fresh JWT (different signer) per hop that carries the four headers as claims. Discarded:
- Re-implements HMAC signing with extra ceremony (RS256 keys, expiry handling, kid rotation) for zero additional security.
- The "JWT shape" tempts callers to inspect arbitrary claims; we want a deliberately small, fixed header set.

### D. No signing — trust the network

VNet integration, private DNS, deny-all on public ingress. Discarded: a single misconfiguration on a forgotten dev deployment is enough to break it, and we have no way to *detect* it from inside the backend. Defense in depth is cheap here.

## Implementation status

- [x] Wire contract specified — [apps/backend/app/security/hmac.py](../../apps/backend/app/security/hmac.py)
- [x] Verify side (backend) — Phase 1 #13 ([apps/backend/app/deps/identity.py](../../apps/backend/app/deps/identity.py))
- [x] `HMAC_DISABLED=1` dev escape hatch — Phase 1 ([apps/backend/app/settings.py](../../apps/backend/app/settings.py))
- [x] Unit tests on the verify side — Phase 1 ([apps/backend/tests/test_hmac.py](../../apps/backend/tests/test_hmac.py))
- [ ] Verify side (MCP) — Phase 4 wire-up
- [ ] Sign side (APIM `hmac-sign.xml` fragment) — Phase 4
- [ ] KV secret + Named Value bindings — Phase 4
- [ ] Key rotation runbook — Phase 4

## References

- [PLAN.md §7 — Authentication flow](../../PLAN.md#7-authentication-flow-and-bu-resolution)
- [apps/backend/app/security/hmac.py](../../apps/backend/app/security/hmac.py) — canonical payload + verify
- ADR-0003 — BU resolution at APIM (the derived header this signature protects)
