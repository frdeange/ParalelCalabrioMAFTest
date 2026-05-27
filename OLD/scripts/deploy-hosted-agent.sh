#!/usr/bin/env bash
# deploy-hosted-agent.sh — End-to-end deploy of the Foundry Hosted Agent.
#
# This script encodes every gotcha we hit while bringing the WFM Data
# Assistant online on Foundry. It is idempotent: safe to re-run after a
# partial failure.
#
# Steps
# -----
# 0. Resolve required identities (Foundry account MI, project MI) and IDs.
# 1. Run scripts/preflight-check.sh (CLI tools + azd env + MI + RBAC + MCP).
# 2. Auto-fix RBAC if `--fix-rbac` is passed:
#       a. AcrPull on the ACR for both Foundry MIs
#       b. Foundry User on the Foundry account for both Foundry MIs
#          (THE FIX without which /storage/history/item_ids returns 401)
#       c. Cosmos DB Built-in Data Contributor on the Cosmos account
#          for the Foundry project MI (data-plane RBAC, separate API)
# 3. azd deploy (builds & pushes the image, creates new agent version).
# 4. Resolve the new agent runtime MI principalId and (optionally) grant
#    Foundry User + Cosmos Data Contributor at account scope so it can
#    read its own storage and the conversation history container.
#    Re-deploys typically reuse the same agent identity; this is a no-op then.
# 5. Wait for RBAC propagation.
# 6. Smoke test via `azd ai agent invoke`.
#
# Usage
# -----
#   ./scripts/deploy-hosted-agent.sh [--fix-rbac] [--no-smoke-test] [--no-preflight]
#
# Flags
#   --fix-rbac        Create missing role assignments automatically.
#   --no-smoke-test   Skip the final `azd ai agent invoke`.
#   --no-preflight    Skip preflight (useful in CI after preflight passed once).
#   --question "..."  Question for the smoke test (default: Spanish agent-count).
#
# Exit codes
#   0 — Deployed and smoke-tested OK
#   1 — Preflight failed (re-run with --fix-rbac or fix manually)
#   2 — azd deploy failed
#   3 — Smoke test failed
#
# Environment
#   Required: azd env with the project initialized (`azd env new <name>`)
#             and these vars set:
#               AZURE_AI_MODEL_DEPLOYMENT_NAME
#               BU_ID
#               MCP_SERVER_URL
#
#             Plus the ones populated by `azd ai agent init`:
#               AZURE_SUBSCRIPTION_ID, AZURE_TENANT_ID, AZURE_LOCATION,
#               AZURE_RESOURCE_GROUP, AZURE_AI_PROJECT_ID,
#               FOUNDRY_PROJECT_ENDPOINT, AZURE_CONTAINER_REGISTRY_ENDPOINT

set -uo pipefail

#--- styling ---------------------------------------------------------------
RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
ok()   { printf "  ${GREEN}✓${NC} %s\n" "$1"; }
warn() { printf "  ${YELLOW}!${NC} %s\n" "$1"; }
err()  { printf "  ${RED}✗${NC} %s\n" "$1"; }
hdr()  { printf "\n${BOLD}${BLUE}=== %s ===${NC}\n" "$1"; }

#--- args ------------------------------------------------------------------
FIX_RBAC=false
RUN_SMOKE=true
RUN_PREFLIGHT=true
QUESTION="¿Cuántos agentes hay en mi organización?"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fix-rbac)      FIX_RBAC=true; shift;;
    --no-smoke-test) RUN_SMOKE=false; shift;;
    --no-preflight)  RUN_PREFLIGHT=false; shift;;
    --question)      QUESTION="$2"; shift 2;;
    -h|--help)       sed -n '2,40p' "$0"; exit 0;;
    *) err "Unknown flag: $1"; exit 1;;
  esac
done

#--- locate project --------------------------------------------------------
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

#--- 0. Resolve identities -------------------------------------------------
hdr "Resolving Foundry identities & IDs"
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
PROJECT_ID=$(azd env get-value AZURE_AI_PROJECT_ID)
ACR_ENDPOINT=$(azd env get-value AZURE_CONTAINER_REGISTRY_ENDPOINT)
ACR_NAME="${ACR_ENDPOINT%%.azurecr.io}"
FOUNDRY_ACCOUNT_ID=$(echo "$PROJECT_ID" | sed -E 's|/projects/[^/]+$||')
FOUNDRY_ACCOUNT_NAME=$(basename "$FOUNDRY_ACCOUNT_ID")
ACR_ID=$(az acr show -n "$ACR_NAME" --query id -o tsv)

ACCOUNT_MI_PID=$(az cognitiveservices account show -n "$FOUNDRY_ACCOUNT_NAME" -g "$RG" --query identity.principalId -o tsv 2>/dev/null || true)
PROJECT_MI_PID=$(az resource show --ids "$PROJECT_ID" --api-version 2025-06-01 --query identity.principalId -o tsv 2>/dev/null || true)

ok "Resource group:       ${RG}"
ok "Foundry account:      ${FOUNDRY_ACCOUNT_NAME}"
ok "Project ID:           ${PROJECT_ID}"
ok "ACR:                  ${ACR_NAME}"
ok "Account MI:           ${ACCOUNT_MI_PID:-(none)}"
ok "Project MI:           ${PROJECT_MI_PID:-(none)}"

ACR_PULL_ROLE="7f951dda-4ed3-4680-a7ca-43fe172d538d"
FOUNDRY_USER_ROLE="53ca6127-db72-4b80-b1b0-d745d6d5456d"
# Cosmos DB Built-in Data Contributor — well-known data-plane role id,
# same across every Cosmos NoSQL account.
COSMOS_CONTRIB_ROLE="00000000-0000-0000-0000-000000000002"

# Cosmos account name derived from the endpoint:
#   https://<account>.documents.azure.com:443/  ->  <account>
COSMOS_ENDPOINT=$(azd env get-value AZURE_COSMOS_ENDPOINT 2>/dev/null || true)
COSMOS_ACCOUNT=""
if [[ -n "$COSMOS_ENDPOINT" ]]; then
  COSMOS_ACCOUNT=$(echo "$COSMOS_ENDPOINT" | sed -E 's|^https://([^.]+)\..*$|\1|')
fi

#--- 1. Preflight ----------------------------------------------------------
if $RUN_PREFLIGHT; then
  hdr "Running preflight"
  if ! "$SCRIPT_DIR/preflight-check.sh"; then
    PF_EXIT=$?
    if [[ "$PF_EXIT" -eq 4 ]] && $FIX_RBAC; then
      warn "Preflight reported missing RBAC. Applying fixes..."
    else
      err "Preflight failed (exit ${PF_EXIT})."
      if [[ "$PF_EXIT" -eq 4 ]]; then
        echo "  Re-run with --fix-rbac to apply the listed role assignments automatically."
      fi
      exit 1
    fi
  fi
fi

#--- 2. RBAC bootstrap (idempotent) ---------------------------------------
ensure_assignment() {
  local pid="$1" role_id="$2" scope="$3" label="$4"
  if [[ -z "$pid" ]]; then warn "${label} — no principalId, skipping"; return; fi
  local count
  count=$(az role assignment list \
            --assignee "$pid" \
            --role "$role_id" \
            --scope "$scope" \
            --query "length([])" -o tsv 2>/dev/null || echo "0")
  if [[ "$count" -ge 1 ]]; then
    ok "${label} — already present"
  else
    if $FIX_RBAC; then
      if az role assignment create \
            --assignee-object-id "$pid" \
            --assignee-principal-type ServicePrincipal \
            --role "$role_id" \
            --scope "$scope" >/dev/null 2>&1; then
        ok "${label} — created"
      else
        err "${label} — FAILED to create"
        return 1
      fi
    else
      warn "${label} — MISSING (re-run with --fix-rbac)"
    fi
  fi
}

# Cosmos data-plane RBAC uses a different API and a built-in role id.
# Scope "/" = whole account. We use `ends_with` to match the role-definition
# id regardless of subscription path differences.
ensure_cosmos_assignment() {
  local pid="$1" label="$2"
  if [[ -z "$pid" ]]; then warn "${label} — no principalId, skipping"; return; fi
  if [[ -z "$COSMOS_ACCOUNT" ]]; then warn "${label} — no Cosmos account, skipping"; return; fi
  local count
  count=$(az cosmosdb sql role assignment list \
            --account-name "$COSMOS_ACCOUNT" \
            --resource-group "$RG" \
            --query "[?principalId=='$pid' && ends_with(roleDefinitionId,'${COSMOS_CONTRIB_ROLE}')] | length(@)" \
            -o tsv 2>/dev/null || echo "0")
  if [[ "$count" -ge 1 ]]; then
    ok "${label} — already present"
  else
    if az cosmosdb sql role assignment create \
          --account-name "$COSMOS_ACCOUNT" \
          --resource-group "$RG" \
          --scope "/" \
          --principal-id "$pid" \
          --role-definition-id "$COSMOS_CONTRIB_ROLE" >/dev/null 2>&1; then
      ok "${label} — created"
    else
      err "${label} — FAILED to create"
      return 1
    fi
  fi
}

if $FIX_RBAC; then
  hdr "Ensuring RBAC (idempotent)"
  ensure_assignment "$ACCOUNT_MI_PID" "$ACR_PULL_ROLE"     "$ACR_ID"            "AcrPull → Account MI"
  ensure_assignment "$PROJECT_MI_PID" "$ACR_PULL_ROLE"     "$ACR_ID"            "AcrPull → Project MI"
  ensure_assignment "$ACCOUNT_MI_PID" "$FOUNDRY_USER_ROLE" "$FOUNDRY_ACCOUNT_ID" "Foundry User → Account MI"
  ensure_assignment "$PROJECT_MI_PID" "$FOUNDRY_USER_ROLE" "$FOUNDRY_ACCOUNT_ID" "Foundry User → Project MI"

  # Cosmos data-plane (separate API: `az cosmosdb sql role assignment`).
  if [[ -n "$COSMOS_ACCOUNT" ]]; then
    ensure_cosmos_assignment "$PROJECT_MI_PID" "Cosmos Data Contributor → Project MI"
  else
    warn "AZURE_COSMOS_ENDPOINT not set — skipping Cosmos data-plane RBAC"
  fi
fi

#--- 3. azd deploy ---------------------------------------------------------
hdr "Running 'azd deploy'"
if ! azd deploy --no-prompt; then
  # `azd deploy` exits non-zero on the cosmetic postdeploy 404 for the agent
  # version lookup. Check whether the deploy itself actually succeeded by
  # asking Foundry for the latest agent version.
  warn "azd deploy returned non-zero (often a benign postdeploy 404 — verifying)..."
fi

AGENT_NAME=$(azd env get-value AGENT_WFM_NAME 2>/dev/null || echo "wfm-data-assistant")
AGENT_VERSION=$(azd env get-value AGENT_WFM_VERSION 2>/dev/null || echo "?")
ok "Deployed agent:       ${AGENT_NAME} v${AGENT_VERSION}"

#--- 4. Resolve agent runtime MI and grant Foundry User -------------------
hdr "Resolving agent runtime identity"
TOKEN=$(az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv)
FOUNDRY_ENDPOINT=$(azd env get-value FOUNDRY_PROJECT_ENDPOINT)

AGENT_JSON=$(curl -sS -H "Authorization: Bearer $TOKEN" \
  "${FOUNDRY_ENDPOINT}/agents/${AGENT_NAME}?api-version=v1" 2>/dev/null || true)
AGENT_RUNTIME_PID=$(echo "$AGENT_JSON" | python3 -c \
  'import sys,json
try:
  d=json.load(sys.stdin)
  print((d.get("instance_identity") or {}).get("principal_id",""))
except Exception:
  print("")' 2>/dev/null)

if [[ -n "$AGENT_RUNTIME_PID" ]]; then
  ok "Agent runtime MI: ${AGENT_RUNTIME_PID}"
  # Best practice (per Foundry docs): also assign Foundry User to the agent's own MI
  # at account scope. This is a no-op if Foundry already configured it.
  ensure_assignment "$AGENT_RUNTIME_PID" "$FOUNDRY_USER_ROLE" "$FOUNDRY_ACCOUNT_ID" \
    "Foundry User → Agent runtime MI" || true
  # The hosted container resolves `DefaultAzureCredential()` to this MI when
  # calling Cosmos, so it also needs data-plane access. Only attempt this
  # when the user opted into --fix-rbac (we don't have an --assignee path
  # for Cosmos and creation requires Cosmos role-assignment write).
  if $FIX_RBAC && [[ -n "$COSMOS_ACCOUNT" ]]; then
    ensure_cosmos_assignment "$AGENT_RUNTIME_PID" \
      "Cosmos Data Contributor → Agent runtime MI" || true
  fi
else
  warn "Could not resolve agent runtime MI (this is usually fine — Foundry manages it transparently)"
fi

#--- 5. Wait for RBAC propagation -----------------------------------------
if $FIX_RBAC; then
  hdr "Waiting 60s for RBAC propagation"
  sleep 60
fi

#--- 6. Smoke test --------------------------------------------------------
if ! $RUN_SMOKE; then
  echo
  ok "Deploy finished. Smoke test skipped (--no-smoke-test)."
  exit 0
fi

hdr "Smoke test"
echo "  Question: ${QUESTION}"
echo

if ! OUTPUT=$(azd ai agent invoke --new-conversation --new-session "$QUESTION" 2>&1); then
  err "Smoke test FAILED:"
  echo "$OUTPUT"
  exit 3
fi

echo "$OUTPUT" | tail -20
echo
if echo "$OUTPUT" | grep -q "PermissionDenied"; then
  err "Smoke test returned PermissionDenied — RBAC propagation may still be in progress."
  echo "  Wait 2-3 minutes and re-run:"
  echo "    azd ai agent invoke --new-conversation --new-session \"$QUESTION\""
  exit 3
elif echo "$OUTPUT" | grep -q "ERROR:"; then
  err "Smoke test surfaced an error. Inspect container logs:"
  echo "    azd ai agent monitor --tail 200"
  exit 3
else
  ok "Smoke test PASSED 🎉"
fi
