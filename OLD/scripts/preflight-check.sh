#!/usr/bin/env bash
# preflight-check.sh — Verify all prerequisites for `azd deploy` of the
# Foundry Hosted Agent are in place BEFORE you try to deploy.
#
# Exits 0 if the project is ready to deploy, non-zero with a clear message
# if anything is missing.
#
# What this checks
# ----------------
# 1. CLI tools: az, azd, docker, curl, jq (or python3)
# 2. Active Azure login + active subscription
# 3. azd env exists and exposes the variables Foundry needs
# 4. Foundry account + project exist; system-assigned MIs are enabled
# 5. ACR exists and both Foundry MIs have AcrPull
# 6. Both Foundry MIs have **Foundry User** at the ACCOUNT scope
#    (this is the requirement most often missed — without it the hosted
#    agent gets 401 from `/storage/history/item_ids` and every /responses
#    call returns 500 PermissionDenied)
# 7. Cosmos DB account exists and the Foundry project MI has the
#    Cosmos DB Built-in Data Contributor role (data-plane RBAC).
#    Without this the hosted agent's persistence + recall_conversation
#    tool 403 against Cosmos.
# 8. MCP endpoint is reachable
#
# Usage
# -----
#   ./scripts/preflight-check.sh
#
# Exit codes
#   0 — ready to deploy
#   1 — missing CLI or login
#   2 — missing azd env vars
#   3 — Foundry/ACR/Cosmos resources not found / MI disabled
#   4 — RBAC missing (script will print the exact `az ...` commands
#       you need to run; Cosmos uses `az cosmosdb sql role assignment
#       create`, the rest use `az role assignment create`)
#   5 — MCP unreachable

set -uo pipefail

#--- pretty printing --------------------------------------------------------
RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
ok()   { printf "  ${GREEN}✓${NC} %s\n" "$1"; }
warn() { printf "  ${YELLOW}!${NC} %s\n" "$1"; }
err()  { printf "  ${RED}✗${NC} %s\n" "$1"; }
hdr()  { printf "\n${BOLD}${BLUE}== %s ==${NC}\n" "$1"; }
note() { printf "    %s\n" "$1"; }

ERRORS=0
RBAC_FIXES=()

# Resolve repo root so the script works regardless of cwd.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

#--- 1. CLI tools ----------------------------------------------------------
hdr "1. CLI tools"
need_cli() {
  if command -v "$1" >/dev/null 2>&1; then
    ok "$1 available ($(command -v "$1"))"
  else
    err "$1 NOT installed"
    ERRORS=$((ERRORS+1))
  fi
}
need_cli az
need_cli azd
need_cli docker
need_cli curl

# We use python3 for JSON parsing (jq is optional).
if command -v jq >/dev/null 2>&1; then
  JSON_TOOL="jq"
  ok "jq available"
elif command -v python3 >/dev/null 2>&1; then
  JSON_TOOL="python3"
  ok "python3 available (used in place of jq)"
else
  err "Need either 'jq' or 'python3' for JSON parsing"
  ERRORS=$((ERRORS+1))
fi

# Check Foundry azd extension
if azd extension list 2>/dev/null | grep -q "azure.ai.agents"; then
  ok "azd extension 'azure.ai.agents' installed"
else
  warn "azd extension 'azure.ai.agents' not installed — run: azd extension install azure.ai.agents"
fi

[[ $ERRORS -gt 0 ]] && { echo; echo "${RED}Install the missing tools and re-run.${NC}"; exit 1; }

#--- 2. Azure login & subscription -----------------------------------------
hdr "2. Azure login & subscription"
if ! az account show >/dev/null 2>&1; then
  err "Not logged into az. Run: az login"
  exit 1
fi
SUB_ID=$(az account show --query id -o tsv)
SUB_NAME=$(az account show --query name -o tsv)
USER_UPN=$(az account show --query user.name -o tsv)
ok "Logged in as: ${USER_UPN}"
ok "Active subscription: ${SUB_NAME} (${SUB_ID})"

if ! azd auth login --check-status >/dev/null 2>&1; then
  warn "azd not authenticated. Run: azd auth login"
fi

#--- 3. azd env variables --------------------------------------------------
hdr "3. azd env variables"
if ! AZD_ENV_NAME=$(azd env get-value AZURE_ENV_NAME 2>/dev/null); then
  err "No active azd env. Create one: azd env new <name>"
  exit 2
fi
ok "Active azd env: ${AZD_ENV_NAME}"

REQUIRED_VARS=(
  AZURE_SUBSCRIPTION_ID
  AZURE_TENANT_ID
  AZURE_LOCATION
  AZURE_RESOURCE_GROUP
  AZURE_AI_PROJECT_ID
  FOUNDRY_PROJECT_ENDPOINT
  AZURE_AI_MODEL_DEPLOYMENT_NAME
  AZURE_CONTAINER_REGISTRY_ENDPOINT
  BU_ID
  MCP_SERVER_URL
  AZURE_COSMOS_ENDPOINT
  AZURE_COSMOS_DATABASE_NAME
  AZURE_COSMOS_CONTAINER_NAME
)

MISSING_VARS=()
for var in "${REQUIRED_VARS[@]}"; do
  val=$(azd env get-value "$var" 2>/dev/null || true)
  if [[ -z "$val" || "$val" == "null" ]]; then
    err "azd env var '${var}' is missing"
    MISSING_VARS+=("$var")
    ERRORS=$((ERRORS+1))
  else
    # Truncate long values
    display="$val"
    [[ ${#display} -gt 80 ]] && display="${display:0:77}..."
    ok "${var} = ${display}"
  fi
done

if [[ ${#MISSING_VARS[@]} -gt 0 ]]; then
  echo
  echo "${YELLOW}Set the missing values with:${NC}"
  for v in "${MISSING_VARS[@]}"; do
    echo "  azd env set $v <value>"
  done
  exit 2
fi

# Extract IDs we need next
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
PROJECT_ID=$(azd env get-value AZURE_AI_PROJECT_ID)
ACR_ENDPOINT=$(azd env get-value AZURE_CONTAINER_REGISTRY_ENDPOINT)
ACR_NAME="${ACR_ENDPOINT%%.azurecr.io}"
# Foundry account = parent of the project id
FOUNDRY_ACCOUNT_ID=$(echo "$PROJECT_ID" | sed -E 's|/projects/[^/]+$||')
FOUNDRY_ACCOUNT_NAME=$(basename "$FOUNDRY_ACCOUNT_ID")

#--- 4. Foundry account + project + MIs ------------------------------------
hdr "4. Foundry account + project + Managed Identities"

ACCOUNT_JSON=$(az cognitiveservices account show \
  -n "$FOUNDRY_ACCOUNT_NAME" -g "$RG" -o json 2>/dev/null) || true
if [[ -z "${ACCOUNT_JSON:-}" ]]; then
  err "Foundry account '${FOUNDRY_ACCOUNT_NAME}' not found in RG '${RG}'"
  exit 3
fi
ok "Foundry account: ${FOUNDRY_ACCOUNT_NAME}"

ACCOUNT_MI_PID=$(echo "$ACCOUNT_JSON" | python3 -c 'import sys,json;d=json.load(sys.stdin);print((d.get("identity") or {}).get("principalId",""))')
if [[ -z "$ACCOUNT_MI_PID" ]]; then
  err "Foundry account does NOT have a system-assigned managed identity enabled"
  note "Enable it in the Azure portal: Account → Identity → System assigned → On"
  ERRORS=$((ERRORS+1))
else
  ok "Account MI principalId: ${ACCOUNT_MI_PID}"
fi

PROJECT_JSON=$(az resource show --ids "$PROJECT_ID" --api-version 2025-06-01 -o json 2>/dev/null) || true
if [[ -z "${PROJECT_JSON:-}" ]]; then
  err "Foundry project not found: ${PROJECT_ID}"
  exit 3
fi
PROJECT_MI_PID=$(echo "$PROJECT_JSON" | python3 -c 'import sys,json;d=json.load(sys.stdin);print((d.get("identity") or {}).get("principalId",""))')
if [[ -z "$PROJECT_MI_PID" ]]; then
  err "Foundry project does NOT have a system-assigned managed identity enabled"
  note "Enable it in Foundry portal: Project → Resource Management → Identity → System assigned → On"
  ERRORS=$((ERRORS+1))
else
  ok "Project MI principalId: ${PROJECT_MI_PID}"
fi

#--- 5. ACR + AcrPull ------------------------------------------------------
hdr "5. Container Registry (ACR)"
ACR_ID=$(az acr show -n "$ACR_NAME" --query id -o tsv 2>/dev/null) || true
if [[ -z "$ACR_ID" ]]; then
  err "ACR '${ACR_NAME}' not found"
  exit 3
fi
ok "ACR found: ${ACR_NAME}"

# AcrPull role ID is well-known
ACR_PULL_ROLE="7f951dda-4ed3-4680-a7ca-43fe172d538d"
# Foundry User role ID
FOUNDRY_USER_ROLE="53ca6127-db72-4b80-b1b0-d745d6d5456d"

check_assignment() {
  local pid="$1" role_id="$2" scope="$3" label="$4"
  if [[ -z "$pid" ]]; then return; fi
  local count
  count=$(az role assignment list \
            --assignee "$pid" \
            --role "$role_id" \
            --scope "$scope" \
            --query "length([])" -o tsv 2>/dev/null || echo "0")
  if [[ "$count" -ge 1 ]]; then
    ok "${label}"
  else
    err "${label} — MISSING"
    RBAC_FIXES+=("az role assignment create --assignee-object-id $pid --assignee-principal-type ServicePrincipal --role $role_id --scope \"$scope\"")
    ERRORS=$((ERRORS+1))
  fi
}

hdr "6. ACR pull permissions"
check_assignment "$ACCOUNT_MI_PID" "$ACR_PULL_ROLE" "$ACR_ID" "AcrPull → Foundry account MI"
check_assignment "$PROJECT_MI_PID" "$ACR_PULL_ROLE" "$ACR_ID" "AcrPull → Foundry project MI"

#--- 7. Foundry User on account ($ CRITICAL) -------------------------------
hdr "7. Foundry User on account (CRITICAL — fixes storage 401)"
check_assignment "$ACCOUNT_MI_PID" "$FOUNDRY_USER_ROLE" "$FOUNDRY_ACCOUNT_ID" "Foundry User → Foundry account MI (account scope)"
check_assignment "$PROJECT_MI_PID" "$FOUNDRY_USER_ROLE" "$FOUNDRY_ACCOUNT_ID" "Foundry User → Foundry project MI (account scope)"

#--- 8. Cosmos DB account + data-plane RBAC --------------------------------
hdr "8. Cosmos DB (conversation history backend)"
COSMOS_ENDPOINT=$(azd env get-value AZURE_COSMOS_ENDPOINT)
COSMOS_DB=$(azd env get-value AZURE_COSMOS_DATABASE_NAME)
COSMOS_CONT=$(azd env get-value AZURE_COSMOS_CONTAINER_NAME)
# Endpoint shape: https://<account>.documents.azure.com:443/
COSMOS_ACCOUNT=$(echo "$COSMOS_ENDPOINT" | sed -E 's|^https://([^.]+)\..*$|\1|')

COSMOS_ID=$(az cosmosdb show -n "$COSMOS_ACCOUNT" -g "$RG" --query id -o tsv 2>/dev/null || true)
if [[ -z "$COSMOS_ID" ]]; then
  err "Cosmos account '${COSMOS_ACCOUNT}' not found in RG '${RG}'"
  ERRORS=$((ERRORS+1))
else
  ok "Cosmos account:       ${COSMOS_ACCOUNT}"

  # Verify database + container exist
  if az cosmosdb sql database show -a "$COSMOS_ACCOUNT" -g "$RG" -n "$COSMOS_DB" >/dev/null 2>&1; then
    ok "Cosmos database:      ${COSMOS_DB}"
  else
    err "Cosmos database '${COSMOS_DB}' not found in account '${COSMOS_ACCOUNT}'"
    ERRORS=$((ERRORS+1))
  fi
  if az cosmosdb sql container show -a "$COSMOS_ACCOUNT" -g "$RG" -d "$COSMOS_DB" -n "$COSMOS_CONT" >/dev/null 2>&1; then
    ok "Cosmos container:     ${COSMOS_CONT}"
  else
    err "Cosmos container '${COSMOS_CONT}' not found in database '${COSMOS_DB}'"
    ERRORS=$((ERRORS+1))
  fi

  # Data-plane RBAC: Cosmos DB Built-in Data Contributor (well-known ID).
  # NOTE: this is NOT Azure RBAC — it uses the data-plane role API
  # `az cosmosdb sql role assignment`. The role id below is the well-known
  # GUID and is identical across every Cosmos NoSQL account.
  COSMOS_CONTRIB_ROLE="00000000-0000-0000-0000-000000000002"

  check_cosmos_assignment() {
    local pid="$1" label="$2"
    if [[ -z "$pid" ]]; then return; fi
    local count
    count=$(az cosmosdb sql role assignment list \
              --account-name "$COSMOS_ACCOUNT" \
              --resource-group "$RG" \
              --query "[?principalId=='$pid' && (roleDefinitionId=='/subscriptions/${SUB_ID}/resourceGroups/${RG}/providers/Microsoft.DocumentDB/databaseAccounts/${COSMOS_ACCOUNT}/sqlRoleDefinitions/${COSMOS_CONTRIB_ROLE}' || ends_with(roleDefinitionId,'${COSMOS_CONTRIB_ROLE}'))] | length(@)" \
              -o tsv 2>/dev/null || echo "0")
    if [[ "$count" -ge 1 ]]; then
      ok "${label}"
    else
      err "${label} — MISSING"
      RBAC_FIXES+=("az cosmosdb sql role assignment create --account-name $COSMOS_ACCOUNT --resource-group $RG --scope \"/\" --principal-id $pid --role-definition-id $COSMOS_CONTRIB_ROLE")
      ERRORS=$((ERRORS+1))
    fi
  }

  # Foundry project MI is what `DefaultAzureCredential()` resolves to inside
  # the Hosted Agent container, so it MUST have data-plane access. The
  # account MI is not strictly required (hosting layer never touches Cosmos
  # itself) but we check it for symmetry with the Foundry User pattern.
  check_cosmos_assignment "$PROJECT_MI_PID" "Cosmos Data Contributor → Foundry project MI (account scope)"
fi

#--- 9. MCP reachable ------------------------------------------------------
hdr "9. MCP server reachability"
MCP_URL=$(azd env get-value MCP_SERVER_URL 2>/dev/null || echo "")
if [[ -z "$MCP_URL" ]]; then
  err "MCP_SERVER_URL not set"
  ERRORS=$((ERRORS+1))
else
  http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$MCP_URL" || echo "000")
  if [[ "$http_code" =~ ^(200|400|405|406)$ ]]; then
    ok "MCP endpoint reachable (HTTP ${http_code}): ${MCP_URL}"
  else
    err "MCP endpoint unreachable or returned HTTP ${http_code}: ${MCP_URL}"
    note "If this is a devtunnel, make sure the tunnel + the local MCP server are running."
    note "If the MCP server talks to Azure SQL, ensure the SQL firewall allows your dev host."
    ERRORS=$((ERRORS+1))
  fi
fi

#--- Final report ----------------------------------------------------------
echo
if [[ ${#RBAC_FIXES[@]} -gt 0 ]]; then
  hdr "Missing RBAC — run these to fix"
  printf '%s\n\n' "${RBAC_FIXES[@]}"
  exit 4
fi

if [[ $ERRORS -gt 0 ]]; then
  echo "${RED}Preflight failed (${ERRORS} error(s)).${NC}"
  exit 5
fi

echo "${GREEN}${BOLD}✓ All preflight checks passed — ready for: azd deploy${NC}"
exit 0
