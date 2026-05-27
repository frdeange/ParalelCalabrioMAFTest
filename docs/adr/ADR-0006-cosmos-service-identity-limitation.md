# ADR-0006: Cosmos DB × Foundry Hosted Agent `ServiceIdentity` RBAC limitation

- **Status**: Accepted (documents a platform-level constraint)
- **Date**: 2026-05-26 (originally discovered) — formalized 2026-05-27
- **Deciders**: @frdeange
- **Related**: ADR-0001 (overall architecture), `apps/backend` (consumer of `CosmosHistoryProvider`)

---

## Context

In the v1 codebase (`OLD/foundry_hosted/main_hosted.py`) we tried to persist
multi-turn conversation history in Cosmos DB while running inside a **Foundry
Hosted Agent** container. The local equivalent (`OLD/main_local_multiturn.py`,
later promoted as the starting point for v2) works perfectly because
`DefaultAzureCredential` resolves to the developer's `az login` user identity,
which holds `Cosmos DB Built-in Data Contributor`.

Inside the hosted-agent container the credential chain resolves instead to the
**agent's runtime principal** — a brand-new Entra subtype called
`ServiceIdentity` (`microsoft.graph.agentIdentity`).

When we attempt to grant that principal the Cosmos data-plane role, every
documented path (CLI, raw ARM, both stable and preview API versions) returns
`HTTP 202 {"status": "Enqueued"}` but the assignment **never lands**:
subsequent `GET` returns `404`, and the agent request fails with
`Forbidden / readMetadata blocked`.

Investigation confirmed the root cause is a **platform-level RBAC compatibility
gap**: Cosmos DB data-plane RBAC was built before Entra introduced the
`agentIdentity` subtype, and its internal principal resolver only accepts
`User`, `Group`, `ServicePrincipal`, `ManagedIdentity`. The `--assignee-principal-type`
override available in **ARM RBAC** has no equivalent in the **Cosmos data-plane RBAC**
control surface (`az cosmosdb sql role assignment`).

This is not a configuration mistake — it requires a fix from the Cosmos DB
and/or Foundry product teams.

## Decision

For **v2**, we do **NOT** rely on Cosmos DB being accessible from a Foundry
Hosted Agent runtime identity. Instead:

1. **Persistence path**: `apps/backend` runs on **Azure Container Apps with a
   user-assigned Managed Identity** (a regular `ManagedIdentity`, fully
   supported by Cosmos data-plane RBAC). This avoids the `ServiceIdentity`
   path entirely.
2. **Foundry usage**: Foundry is consumed as a **model endpoint only** (chat
   completions for the workflow Executors), not as a hosting plane for the
   agent runtime. The agent runtime lives in our ACA service.
3. **History provider**: `CosmosHistoryProvider` from `agent-framework-azure-cosmos`
   is kept as-is — it works against a `ManagedIdentity` principal.

If, in the future, we ever want to revisit Foundry Hosted Agents for any
component, the documented alternatives below apply.

## Alternatives considered

### A. Azure Blob Storage instead of Cosmos for hosted-agent history

Blob Storage uses **ARM RBAC**, which accepts
`--assignee-principal-type ServicePrincipal` as an override and therefore
works with `ServiceIdentity` principals. Requires a `BlobHistoryProvider`
(~80 LOC) replacing `CosmosHistoryProvider`, plus a Storage Account and the
relevant role assignments. Viable but not pursued.

### B. Foundry native workflow checkpoints (`FileCheckpointStorage`)

The MAF SDK's `ResponsesHostServer._handle_inner_workflow` exposes
`ctx.get_state` / `ctx.set_state` on every Executor; Foundry serializes that
into a per-session checkpoint stored on the platform filesystem (keyed by
`conversation_id` / `previous_response_id`). This was validated end-to-end on
Foundry Hosted Agent v9 (`OLD/foundry_hosted/main_hosted_native.py`). Works
without any external storage, but:

- Each `previous_response_id` chain creates a separate checkpoint directory,
  so the Foundry Traces UI shows turns as independent rows unless the client
  also creates a `conv_*` object server-side.
- It is per-conversation only — no cross-conversation memory, no BU indexing.

### C. Wait for Microsoft to add `ServiceIdentity` to Cosmos data-plane RBAC

Not a planning option for this project. We do not block on third-party fixes.

## Consequences

### Positive

- `apps/backend` keeps a single, well-supported persistence backend (Cosmos)
  with full ARM RBAC compatibility via Managed Identity.
- No need to introduce Blob Storage just for history.
- The architecture is decoupled from any future Foundry-platform RBAC changes.

### Negative / accepted trade-offs

- We do not benefit from any future Foundry Hosted Agent autoscaling / pricing
  improvements. We pay ACA hosting cost instead.
- If we ever need to move a component onto Foundry Hosted Agents, we must
  re-evaluate Alternative A or B.

### Operational checklist

- ACA service uses a **user-assigned ManagedIdentity** (not SystemAssigned, to
  keep RBAC bindings stable across container revisions).
- Cosmos role assignment uses the Managed Identity's `principalId`:
  `az cosmosdb sql role assignment create --principal-id <mi-principal-id> --role-definition-id 00000000-0000-0000-0000-000000000002 --scope <db-scope>`.
- No `ServiceIdentity` principal is ever expected as a Cosmos consumer.

## Evidence / references

Full investigation trail (HTTP traces, Graph API responses, every bypass
attempted and why it failed) is preserved in this ADR rather than the previous
location to keep the project history self-contained.

| Artifact | What it documented | Status in v2 |
|---|---|---|
| `agentIdentity` principal id `b5849610-…` (v1 hosted agent) | Concrete example of the rejected principal | N/A — v2 uses ACA MI |
| Cosmos `readMetadata` 403 with `x-ms-substatus: 5301` | Symptom on the data plane | Won't reproduce in v2 |
| `az cosmosdb sql role assignment create` returning `Enqueued` + later `404` | Silent rejection at control plane | Won't reproduce in v2 |
| `--assignee-principal-type ServicePrincipal` override available only in ARM RBAC | Why Blob would work but Cosmos does not | Documented as Alternative A |
| `ResponsesHostServer._handle_inner_workflow` / `ctx.get_state` / `ctx.set_state` | Foundry-native checkpoint path | Documented as Alternative B |

The original v1 archive (`OLD/`) has been removed from the repository now that
this ADR carries the lesson forward. The v1 long-form README that contained
this material is preserved by the maintainer outside the repo.
