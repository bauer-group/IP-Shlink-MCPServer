#!/usr/bin/env bash
#
# setup-azure-app.sh - Provision or maintain the Microsoft Entra app registration
#                      for Shlink MCP. Linux/macOS counterpart to setup-azure-app.ps1.
#
# Modes:
#   create          New app registration with redirect URIs, OIDC Graph permissions,
#                   custom API scope (Expose-an-API), v2 access tokens, client secret,
#                   and admin consent. Refuses to overwrite an existing app of the
#                   same name.
#
#   rotate-secret   Generates a fresh client secret for an existing app and prints it.
#                   Old secrets stay valid (rolling rotation) - delete them manually
#                   once production has switched over.
#
# Usage:
#   ./scripts/setup-azure-app.sh --tenant-id <guid> --hostname mcp.example.com
#   ./scripts/setup-azure-app.sh --mode rotate-secret --tenant-id <guid> --client-id <guid>
#
# Options:
#   --mode <create|rotate-secret>    Default: create
#   --tenant-id <guid>               Required
#   --hostname <fqdn>                Required for create (e.g. mcp.go.bauer-group.com)
#   --client-id <guid>               Required for rotate-secret
#   --display-name <name>            Defaults to "MCP-Server (<hostname>) - URL Shortener"
#   --local-port <port>              Local dev redirect port. Default: 8000
#   --scope-name <name>              Custom API scope. Default: access_as_user
#   --secret-years <1|2>             Secret validity. Default: 2 (Azure max)
#   --include-graph-user-read        Add Graph User.Read (only if MCP calls Graph)
#   --no-admin-consent               Skip the consent step (grant manually in Portal)
#   --help                           Show this help and exit
#
# Requirements:
#   az CLI:  curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash   (Debian/Ubuntu)
#            brew install azure-cli                                    (macOS)
#   jq:      apt install jq | brew install jq
#
# Signed-in user needs Application Administrator role OR ownership +
# Application.ReadWrite.OwnedBy. Admin consent additionally needs one of:
# Global Admin, Privileged Role Administrator, Cloud Application Administrator.

set -euo pipefail

# --- Microsoft Graph well-known IDs (stable across tenants) -----------------
GRAPH_APP_ID='00000003-0000-0000-c000-000000000000'
SCOPE_OPENID='37f7f235-527c-4136-accd-4a02d197296e'
SCOPE_PROFILE='14dad69e-099b-42c9-810b-d002981feec1'
SCOPE_EMAIL='64a6cdd6-aab1-4aaf-94b8-3cc8405e90d0'
SCOPE_OFFLINE='7427e0e9-2fba-42fe-b0c0-848c9e6a8182'
SCOPE_USER_READ='e1fe6dd8-ba31-4d61-89e7-88639da4683d'

# --- ANSI colors (only if stdout is a tty) ----------------------------------
if [[ -t 1 ]]; then
    C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
    C_CYAN=$'\033[36m'; C_GRAY=$'\033[90m'; C_RESET=$'\033[0m'
else
    C_RED=''; C_GREEN=''; C_YELLOW=''; C_CYAN=''; C_GRAY=''; C_RESET=''
fi

log_step()  { echo "${C_CYAN}$*${C_RESET}"; }
log_info()  { echo "${C_GRAY}  -> $*${C_RESET}"; }
log_warn()  { echo "${C_YELLOW}$*${C_RESET}"; }
log_ok()    { echo "${C_GREEN}$*${C_RESET}"; }
die()       { echo "${C_RED}ERROR: $*${C_RESET}" >&2; exit 1; }

usage() {
    sed -n '/^# Usage:/,/^$/p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

# --- Argument parsing -------------------------------------------------------
MODE='create'
TENANT_ID=''
HOSTNAME_ARG=''
CLIENT_ID=''
DISPLAY_NAME=''
LOCAL_PORT=8000
SCOPE_NAME='access_as_user'
SECRET_YEARS=2
INCLUDE_USER_READ=false
NO_ADMIN_CONSENT=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)                     MODE="$2"; shift 2 ;;
        --tenant-id)                TENANT_ID="$2"; shift 2 ;;
        --hostname)                 HOSTNAME_ARG="$2"; shift 2 ;;
        --client-id)                CLIENT_ID="$2"; shift 2 ;;
        --display-name)             DISPLAY_NAME="$2"; shift 2 ;;
        --local-port)               LOCAL_PORT="$2"; shift 2 ;;
        --scope-name)               SCOPE_NAME="$2"; shift 2 ;;
        --secret-years)             SECRET_YEARS="$2"; shift 2 ;;
        --include-graph-user-read)  INCLUDE_USER_READ=true; shift ;;
        --no-admin-consent)         NO_ADMIN_CONSENT=true; shift ;;
        --help|-h)                  usage ;;
        *)                          die "Unknown argument: $1 (try --help)" ;;
    esac
done

# --- Validation -------------------------------------------------------------
[[ -n "$TENANT_ID" ]] || die "--tenant-id is required"
[[ "$TENANT_ID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] \
    || die "--tenant-id is not a valid GUID"
[[ "$MODE" == "create" || "$MODE" == "rotate-secret" ]] \
    || die "--mode must be 'create' or 'rotate-secret'"
[[ "$SECRET_YEARS" =~ ^[12]$ ]] || die "--secret-years must be 1 or 2 (Azure max)"

command -v az >/dev/null 2>&1 || die "Azure CLI (az) not found. Install it first."
command -v jq >/dev/null 2>&1 || die "jq not found. Install: apt install jq | brew install jq"

# --- Login --------------------------------------------------------------------
current_tenant=$(az account show --query tenantId -o tsv 2>/dev/null || echo "")
if [[ "$current_tenant" != "$TENANT_ID" ]]; then
    log_step "Logging into tenant $TENANT_ID..."
    az login --tenant "$TENANT_ID" --output none
fi
signed_in=$(az account show --query 'user.name' -o tsv)
log_ok "Signed in as $signed_in (tenant: $TENANT_ID)"

# --- Helpers ------------------------------------------------------------------

# Build the list of Graph permission GUIDs as a bash array, honoring
# INCLUDE_USER_READ. Used in both the initial create call and the manifest PATCH.
collect_graph_perm_ids() {
    local -n out=$1
    out=("$SCOPE_OPENID" "$SCOPE_PROFILE" "$SCOPE_EMAIL" "$SCOPE_OFFLINE")
    if [[ "$INCLUDE_USER_READ" == "true" ]]; then
        out+=("$SCOPE_USER_READ")
    fi
}

# Convert a bash array of GUIDs into a jq-consumable JSON array of
# {id, type:"Scope"} objects.
guids_to_resource_access_json() {
    local guid
    {
        echo '['
        local first=true
        for guid in "$@"; do
            $first || echo ','
            first=false
            printf '{"id":"%s","type":"Scope"}' "$guid"
        done
        echo ']'
    }
}

# --- create mode --------------------------------------------------------------
create_mode() {
    [[ -n "$HOSTNAME_ARG" ]] || die "--hostname is required for create mode (e.g. mcp.go.bauer-group.com)"
    [[ -z "$DISPLAY_NAME" ]] && DISPLAY_NAME="MCP-Server ($HOSTNAME_ARG) - URL Shortener"

    # Refuse to clobber existing app of the same display name
    local existing
    existing=$(az ad app list --display-name "$DISPLAY_NAME" --query '[].appId' -o tsv)
    if [[ -n "$existing" ]]; then
        die "App '$DISPLAY_NAME' already exists (appId: $existing). Delete it via Portal or run with --mode rotate-secret."
    fi

    local graph_perm_ids
    collect_graph_perm_ids graph_perm_ids
    local graph_access_json
    graph_access_json=$(guids_to_resource_access_json "${graph_perm_ids[@]}")

    log_step ""
    log_step "Step 1/6: Creating app registration..."

    # Initial requiredResourceAccess covers Graph only - own API gets added in
    # step 3 because we need the appId first. The full array gets re-set then.
    local initial_required_access
    initial_required_access=$(jq -n \
        --arg graphAppId "$GRAPH_APP_ID" \
        --argjson perms "$graph_access_json" \
        '[{resourceAppId: $graphAppId, resourceAccess: $perms}]')

    local app_json
    app_json=$(az ad app create \
        --display-name "$DISPLAY_NAME" \
        --sign-in-audience AzureADMyOrg \
        --web-redirect-uris "http://localhost:$LOCAL_PORT/auth/callback" "https://$HOSTNAME_ARG/auth/callback" \
        --required-resource-accesses "$initial_required_access" \
        --output json)
    local app_id obj_id
    app_id=$(echo "$app_json" | jq -r '.appId')
    obj_id=$(echo "$app_json" | jq -r '.id')
    log_info "appId: $app_id"

    log_step ""
    log_step "Step 2/6: Setting Application ID URI to api://$app_id..."
    az ad app update --id "$app_id" --identifier-uris "api://$app_id" --output none

    log_step ""
    log_step "Step 3/6: Adding custom scope '$SCOPE_NAME' + v2 access tokens + own API in requiredResourceAccess..."
    local scope_id
    scope_id=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || uuidgen | tr 'A-Z' 'a-z')

    local required_resource_access
    required_resource_access=$(jq -n \
        --arg graphAppId "$GRAPH_APP_ID" \
        --argjson graphPerms "$graph_access_json" \
        --arg ownAppId "$app_id" \
        --arg scopeId "$scope_id" \
        '[
          {resourceAppId: $graphAppId, resourceAccess: $graphPerms},
          {resourceAppId: $ownAppId,   resourceAccess: [{id: $scopeId, type: "Scope"}]}
        ]')

    local patch_body
    patch_body=$(jq -n \
        --arg scope_id "$scope_id" \
        --arg scope_name "$SCOPE_NAME" \
        --argjson required "$required_resource_access" \
        '{
          api: {
            requestedAccessTokenVersion: 2,
            oauth2PermissionScopes: [{
              id: $scope_id,
              value: $scope_name,
              type: "User",
              isEnabled: true,
              adminConsentDisplayName: "Access Shlink MCP as User",
              adminConsentDescription: "Allows the app to call the Shlink MCP server on behalf of the signed-in user.",
              userConsentDisplayName: "Access Shlink MCP",
              userConsentDescription: "Allows the app to call Shlink MCP on your behalf."
            }]
          },
          requiredResourceAccess: $required
        }')

    # Use a tempfile to keep the JSON body verbatim (avoids shell-escaping hell).
    local tmp_body
    tmp_body=$(mktemp)
    trap 'rm -f "$tmp_body"' EXIT
    echo "$patch_body" >"$tmp_body"

    az rest --method PATCH \
        --uri "https://graph.microsoft.com/v1.0/applications/$obj_id" \
        --headers "Content-Type=application/json" \
        --body "@$tmp_body" \
        --output none

    log_step ""
    log_step "Step 4/6: Creating service principal (needed for admin consent)..."
    # Idempotent - swallow "already exists" errors.
    az ad sp create --id "$app_id" --output none 2>/dev/null || true

    log_step ""
    log_step "Step 5/6: Generating client secret (valid $SECRET_YEARS year(s))..."
    local secret_name
    secret_name="auto-$(date +%Y-%m-%d)"
    local secret_json
    secret_json=$(az ad app credential reset \
        --id "$app_id" \
        --display-name "$secret_name" \
        --years "$SECRET_YEARS" \
        --append \
        --output json)
    local secret_value secret_expiry
    secret_value=$(echo "$secret_json" | jq -r '.password')
    secret_expiry=$(echo "$secret_json" | jq -r '.endDateTime')

    if [[ "$NO_ADMIN_CONSENT" == "true" ]]; then
        log_step ""
        log_warn "Step 6/6: Skipping admin consent (--no-admin-consent set)."
        log_warn "  Grant manually: Portal -> API Permissions -> 'Grant admin consent for <tenant>'"
    else
        log_step ""
        log_step "Step 6/6: Granting tenant-wide admin consent..."
        sleep 5  # Allow SP to replicate across Azure regions
        az ad app permission admin-consent --id "$app_id" --output none
    fi

    print_env_output "$app_id" "$secret_value" "$secret_expiry" "create"
}

# --- rotate-secret mode -------------------------------------------------------
rotate_secret_mode() {
    [[ -n "$CLIENT_ID" ]] || die "--client-id is required for rotate-secret mode"

    local app_json
    app_json=$(az ad app show --id "$CLIENT_ID" --output json 2>/dev/null) \
        || die "App registration with clientId '$CLIENT_ID' not found in current tenant"
    local display_name
    display_name=$(echo "$app_json" | jq -r '.displayName')
    log_step "Rotating secret for: $display_name ($CLIENT_ID)"

    echo ""
    echo "${C_GRAY}Existing credentials:${C_RESET}"
    echo "$app_json" | jq -r '.passwordCredentials[] |
        "  - \(.displayName) [keyId=\(.keyId)] (expires \(.endDateTime))"' \
        | sed "s/^/${C_GRAY}/;s/\$/${C_RESET}/"

    log_step ""
    log_step "Generating new secret (valid $SECRET_YEARS year(s))..."
    local secret_name
    secret_name="auto-$(date +%Y-%m-%d-%H%M)"
    local secret_json
    secret_json=$(az ad app credential reset \
        --id "$CLIENT_ID" \
        --display-name "$secret_name" \
        --years "$SECRET_YEARS" \
        --append \
        --output json)
    local secret_value secret_expiry
    secret_value=$(echo "$secret_json" | jq -r '.password')
    secret_expiry=$(echo "$secret_json" | jq -r '.endDateTime')

    print_env_output "$CLIENT_ID" "$secret_value" "$secret_expiry" "rotate-secret"
}

# --- Output -------------------------------------------------------------------
print_env_output() {
    local app_id="$1" secret_value="$2" secret_expiry="$3" mode="$4"

    echo ""
    log_ok "============================================================"
    log_ok " Done. Paste into your .env (secret is shown ONCE)"
    log_ok "============================================================"
    cat <<EOF

ENTRA_CLIENT_ID=$app_id
ENTRA_CLIENT_SECRET=$secret_value
ENTRA_TENANT_ID=$TENANT_ID
# Secret expires: $secret_expiry

EOF

    if [[ "$mode" == "rotate-secret" ]]; then
        log_warn "Old secrets remain valid until their own expiry. Remove them via:"
        echo "${C_GRAY}  az ad app credential delete --id $app_id --key-id <keyId>${C_RESET}"
    fi
}

# --- Main ---------------------------------------------------------------------
case "$MODE" in
    create)         create_mode ;;
    rotate-secret)  rotate_secret_mode ;;
esac
