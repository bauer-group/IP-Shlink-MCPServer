#Requires -Version 7.0
<#
.SYNOPSIS
    Provision or maintain the Microsoft Entra app registration for Shlink MCP.

.DESCRIPTION
    Replaces the manual Azure-Portal click-through with a one-shot script. Two modes:

      create          Creates a new app registration with everything wired:
                      redirect URIs (localhost + public host), Graph OIDC permissions,
                      a custom API scope under "Expose an API", v2 access tokens,
                      a client secret, and tenant-wide admin consent.

      rotate-secret   Generates a fresh client secret for an existing app registration.
                      The old secret is NOT removed automatically - both stay valid
                      until the old one expires or you delete it via Azure Portal.
                      That's intentional: lets you flip the new secret into prod and
                      verify before pulling the rug on the old one.

    Output is .env-ready - paste the printed block into your .env file.

.PARAMETER Mode
    create | rotate-secret. Defaults to create.

.PARAMETER TenantId
    Azure AD tenant GUID. Required.

.PARAMETER Hostname
    Public hostname for the production redirect URI (e.g. mcp.go.bauer-group.com).
    Required for create mode.

.PARAMETER ClientId
    Existing app registration's Application (client) ID. Required for rotate-secret.

.PARAMETER DisplayName
    App registration display name. Defaults to "MCP-Server (<hostname>) - URL Shortener".

.PARAMETER LocalPort
    Local development port for the dev redirect URI. Defaults to 8000.

.PARAMETER ScopeName
    Custom API scope name exposed under "Expose an API". Defaults to access_as_user.

.PARAMETER SecretYears
    Client secret validity in years. Azure max is 2. Defaults to 2.

.PARAMETER IncludeGraphUserRead
    Switch. When set, also requests Microsoft Graph User.Read delegated permission.
    Only needed if MCP tools call Graph on behalf of the user. Default off.

.PARAMETER NoAdminConsent
    Switch. Skip the admin-consent step. Useful when running as a non-admin user
    who lacks Application.ReadWrite.All - consent has to be granted via Portal then.

.EXAMPLE
    .\scripts\setup-azure-app.ps1 -TenantId a5456ef1-... -Hostname mcp.go.bauer-group.com

.EXAMPLE
    .\scripts\setup-azure-app.ps1 -Mode rotate-secret -TenantId a5456ef1-... -ClientId 5bb036cc-...

.NOTES
    Requires: az CLI (winget install Microsoft.AzureCLI).
    The signed-in user needs Application Administrator role OR ownership of the app
    plus Application.ReadWrite.OwnedBy. Admin consent additionally needs Global Admin
    OR Privileged Role Administrator OR Cloud Application Administrator.
#>

[CmdletBinding()]
param(
    [ValidateSet('create', 'rotate-secret')]
    [string] $Mode = 'create',

    [Parameter(Mandatory)]
    [ValidatePattern('^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]
    [string] $TenantId,

    [string] $Hostname,
    [string] $ClientId,
    [string] $DisplayName,
    [ValidateRange(1, 65535)]
    [int]    $LocalPort = 8000,
    [string] $ScopeName = 'access_as_user',
    [ValidateRange(1, 2)]
    [int]    $SecretYears = 2,
    [switch] $IncludeGraphUserRead,
    [switch] $NoAdminConsent
)

$ErrorActionPreference = 'Stop'

# --- Microsoft Graph well-known IDs (stable across all tenants) --------------
# Source: https://learn.microsoft.com/en-us/graph/permissions-reference
$GraphAppId        = '00000003-0000-0000-c000-000000000000'
$GraphScopes = @{
    openid         = '37f7f235-527c-4136-accd-4a02d197296e'
    profile        = '14dad69e-099b-42c9-810b-d002981feec1'
    email          = '64a6cdd6-aab1-4aaf-94b8-3cc8405e90d0'
    offline_access = '7427e0e9-2fba-42fe-b0c0-848c9e6a8182'
    'User.Read'    = 'e1fe6dd8-ba31-4d61-89e7-88639da4683d'
}

# --- Pre-flight checks -------------------------------------------------------

function Test-AzCli {
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        throw "Azure CLI (az) not found. Install: winget install Microsoft.AzureCLI"
    }
}

function Connect-AzTenant {
    param([string] $TenantId)

    $account = az account show --output json 2>$null | ConvertFrom-Json
    if (-not $account -or $account.tenantId -ne $TenantId) {
        Write-Host "Logging into tenant $TenantId..." -ForegroundColor Cyan
        az login --tenant $TenantId --output none
        $account = az account show --output json | ConvertFrom-Json
    }
    Write-Host "Signed in as $($account.user.name) (tenant: $($account.tenantId))" -ForegroundColor Green
}

# --- Create mode -------------------------------------------------------------

function New-AzureMcpApp {
    param(
        [string] $DisplayName,
        [string] $Hostname,
        [int]    $LocalPort,
        [string] $ScopeName,
        [int]    $SecretYears,
        [bool]   $IncludeUserRead,
        [bool]   $NoAdminConsent
    )

    # Refuse to clobber an existing app with the same name - too many edge cases
    # in "update vs create" merges; safer to make the operator delete or rename.
    $existing = az ad app list --display-name $DisplayName --output json | ConvertFrom-Json
    if ($existing.Count -gt 0) {
        $ids = ($existing | ForEach-Object { $_.appId }) -join ', '
        throw "App with display name '$DisplayName' already exists (appId: $ids). Delete it via Portal or run with rotate-secret mode."
    }

    Write-Host "`nStep 1/6: Creating app registration..." -ForegroundColor Cyan
    $graphPermissions = @($GraphScopes.openid, $GraphScopes.profile, $GraphScopes.email, $GraphScopes.offline_access)
    if ($IncludeUserRead) { $graphPermissions += $GraphScopes.'User.Read' }

    # Build initial requiredResourceAccess (Graph only - own API added in step 3
    # once we know appId). Write to temp file so az consumes verbatim JSON
    # instead of getting mangled by shell-escaping rules.
    $initialAccess = @(@{
        resourceAppId  = $GraphAppId
        resourceAccess = @($graphPermissions | ForEach-Object { @{ id = $_; type = 'Scope' } })
    }) | ConvertTo-Json -Depth 10 -Compress
    $tmpInitial = New-TemporaryFile
    Set-Content -Path $tmpInitial -Value $initialAccess -Encoding utf8
    try {
        $app = az ad app create `
            --display-name $DisplayName `
            --sign-in-audience AzureADMyOrg `
            --web-redirect-uris "http://localhost:$LocalPort/auth/azure/callback" "https://$Hostname/auth/azure/callback" `
            --required-resource-accesses "@$tmpInitial" `
            --output json | ConvertFrom-Json
    } finally {
        Remove-Item $tmpInitial -Force -ErrorAction SilentlyContinue
    }
    $appId  = $app.appId
    $objId  = $app.id
    Write-Host "  -> appId: $appId" -ForegroundColor DarkGray

    Write-Host "`nStep 2/6: Setting Application ID URI to api://$appId..." -ForegroundColor Cyan
    az ad app update --id $appId --identifier-uris "api://$appId" --output none

    Write-Host "`nStep 3/6: Adding custom scope '$ScopeName' + v2 access tokens + own API in requiredResourceAccess..." -ForegroundColor Cyan
    $scopeId = (New-Guid).ToString()
    $patchBody = @{
        api = @{
            requestedAccessTokenVersion = 2
            oauth2PermissionScopes = @(@{
                id                      = $scopeId
                value                   = $ScopeName
                type                    = 'User'
                isEnabled               = $true
                adminConsentDisplayName = "Access Shlink MCP as User"
                adminConsentDescription = "Allows the app to call the Shlink MCP server on behalf of the signed-in user."
                userConsentDisplayName  = "Access Shlink MCP"
                userConsentDescription  = "Allows the app to call Shlink MCP on your behalf."
            })
        }
        requiredResourceAccess = @(
            @{
                resourceAppId  = $GraphAppId
                resourceAccess = @($graphPermissions | ForEach-Object { @{ id = $_; type = 'Scope' } })
            },
            @{
                resourceAppId  = $appId
                resourceAccess = @(@{ id = $scopeId; type = 'Scope' })
            }
        )
    } | ConvertTo-Json -Depth 10 -Compress

    # Encode body for cross-platform shell escaping. az rest --body reads from stdin
    # via @<file> or accepts a string - use a temp file to avoid escaping hell.
    $tmpBody = New-TemporaryFile
    Set-Content -Path $tmpBody -Value $patchBody -Encoding utf8
    try {
        az rest --method PATCH `
                --uri "https://graph.microsoft.com/v1.0/applications/$objId" `
                --headers "Content-Type=application/json" `
                --body "@$tmpBody" `
                --output none
    } finally {
        Remove-Item $tmpBody -Force -ErrorAction SilentlyContinue
    }

    Write-Host "`nStep 4/6: Creating service principal (needed for admin consent)..." -ForegroundColor Cyan
    # Idempotent: creating an SP that already exists is a no-op error we swallow.
    az ad sp create --id $appId --output none 2>$null

    Write-Host "`nStep 5/6: Generating client secret (valid $SecretYears year(s))..." -ForegroundColor Cyan
    $secretName = "auto-$(Get-Date -Format yyyy-MM-dd)"
    $secret = az ad app credential reset --id $appId `
                                         --display-name $secretName `
                                         --years $SecretYears `
                                         --append `
                                         --output json | ConvertFrom-Json

    if ($NoAdminConsent) {
        Write-Host "`nStep 6/6: Skipping admin consent (--NoAdminConsent set)." -ForegroundColor Yellow
        Write-Host "  Grant manually: Portal -> API Permissions -> 'Grant admin consent for <tenant>'" -ForegroundColor Yellow
    } else {
        Write-Host "`nStep 6/6: Granting tenant-wide admin consent..." -ForegroundColor Cyan
        # Brief settle delay - Azure replicates SP availability across regions.
        Start-Sleep -Seconds 5
        az ad app permission admin-consent --id $appId --output none
    }

    return [PSCustomObject]@{
        AppId        = $appId
        ObjectId     = $objId
        SecretValue  = $secret.password
        SecretExpiry = $secret.endDateTime
    }
}

# --- Rotate-secret mode ------------------------------------------------------

function Update-AzureMcpSecret {
    param(
        [string] $ClientId,
        [int]    $SecretYears
    )

    $app = az ad app show --id $ClientId --output json 2>$null | ConvertFrom-Json
    if (-not $app) {
        throw "App registration with clientId '$ClientId' not found in current tenant."
    }
    Write-Host "Rotating secret for: $($app.displayName) ($($app.appId))" -ForegroundColor Cyan

    Write-Host "`nExisting credentials:" -ForegroundColor DarkGray
    foreach ($cred in $app.passwordCredentials) {
        $expiresIn = (([DateTime]$cred.endDateTime) - (Get-Date)).Days
        $tag = if ($expiresIn -lt 0) { "EXPIRED" } elseif ($expiresIn -lt 30) { "expires in $expiresIn d" } else { "$expiresIn d" }
        Write-Host "  - $($cred.displayName) [keyId=$($cred.keyId)] ($tag)" -ForegroundColor DarkGray
    }

    Write-Host "`nGenerating new secret (valid $SecretYears year(s))..." -ForegroundColor Cyan
    $secretName = "auto-$(Get-Date -Format yyyy-MM-dd-HHmm)"
    $secret = az ad app credential reset --id $ClientId `
                                         --display-name $secretName `
                                         --years $SecretYears `
                                         --append `
                                         --output json | ConvertFrom-Json
    return [PSCustomObject]@{
        AppId        = $ClientId
        SecretValue  = $secret.password
        SecretExpiry = $secret.endDateTime
        SecretName   = $secretName
    }
}

# --- Output helpers ----------------------------------------------------------

function Show-EnvOutput {
    param(
        [Parameter(Mandatory)] [string] $Mode,
        [Parameter(Mandatory)] [string] $TenantId,
        [Parameter(Mandatory)] [string] $AppId,
        [Parameter(Mandatory)] [string] $SecretValue,
        [Parameter(Mandatory)] [string] $SecretExpiry
    )

    Write-Host "`n============================================================" -ForegroundColor Green
    Write-Host " Done. Paste into your .env (secret is shown ONCE)" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host @"

ENTRA_CLIENT_ID=$AppId
ENTRA_CLIENT_SECRET=$SecretValue
ENTRA_TENANT_ID=$TenantId
# Secret expires: $SecretExpiry

"@ -ForegroundColor White

    if ($Mode -eq 'rotate-secret') {
        Write-Host "Old secrets remain valid until their own expiry. Remove them via:" -ForegroundColor Yellow
        Write-Host "  az ad app credential delete --id $AppId --key-id <keyId>" -ForegroundColor DarkGray
    }
}

# --- Main --------------------------------------------------------------------

Test-AzCli
Connect-AzTenant -TenantId $TenantId

switch ($Mode) {
    'create' {
        if (-not $Hostname) {
            throw "-Hostname is required for create mode (e.g. mcp.go.bauer-group.com)"
        }
        if (-not $DisplayName) {
            $DisplayName = "MCP-Server ($Hostname) - URL Shortener"
        }
        $result = New-AzureMcpApp -DisplayName $DisplayName `
                                  -Hostname $Hostname `
                                  -LocalPort $LocalPort `
                                  -ScopeName $ScopeName `
                                  -SecretYears $SecretYears `
                                  -IncludeUserRead:$IncludeGraphUserRead `
                                  -NoAdminConsent:$NoAdminConsent
        Show-EnvOutput -Mode create `
                       -TenantId $TenantId `
                       -AppId $result.AppId `
                       -SecretValue $result.SecretValue `
                       -SecretExpiry $result.SecretExpiry
    }
    'rotate-secret' {
        if (-not $ClientId) {
            throw "-ClientId is required for rotate-secret mode."
        }
        $result = Update-AzureMcpSecret -ClientId $ClientId -SecretYears $SecretYears
        Show-EnvOutput -Mode rotate-secret `
                       -TenantId $TenantId `
                       -AppId $result.AppId `
                       -SecretValue $result.SecretValue `
                       -SecretExpiry $result.SecretExpiry
    }
}
