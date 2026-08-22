<#
    azure_discover.ps1 -- read-only inventory of the Azure resources JH Research
    IT provisioned, so we know what we are actually deploying onto.

    Changes nothing. Run it, paste the output back.

    Prerequisites:
        winget install -e --id Microsoft.AzureCLI
        az login
#>

$ErrorActionPreference = 'Continue'

$SUB   = '5fe71151-fb6f-484b-81fc-9104d22a15ea'
$RG    = 'JH-RIT-CRONE-App-RG'
$APP   = 'rit3845-neurorecon-APP'
$PLAN  = 'RIT-3845-neurorecon-ASP'
$SQL   = 'rit3845neuroreconsql01'
$STOR  = 'rit3845neuroreconst01'
$ACR   = 'rit3845neuroreconacr01'

function Section($t) { Write-Host ""; Write-Host ("=" * 70); Write-Host "  $t"; Write-Host ("=" * 70) }

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Host "Azure CLI not found. Install it first:  winget install -e --id Microsoft.AzureCLI"
    exit 1
}

az account set --subscription $SUB 2>$null
Section "Signed in as"
az account show --query "{user:user.name, subscription:name}" -o table

# The critical one. reserved=true means a Linux plan, which is what a container
# image needs. A Windows plan means going back to IT.
Section "App Service Plan  (reserved=True means LINUX -- required for containers)"
az appservice plan show -g $RG -n $PLAN --query "{sku:sku.name, tier:sku.tier, capacity:sku.capacity, kind:kind, reserved:reserved}" -o table

Section "App Service config"
az webapp show -g $RG -n $APP --query "{state:state, kind:kind, host:defaultHostName, https:httpsOnly}" -o table
az webapp config show -g $RG -n $APP --query "{linuxFxVersion:linuxFxVersion, alwaysOn:alwaysOn, workers:numberOfWorkers, ftps:ftpsState}" -o table

Section "App settings currently defined (names only)"
az webapp config appsettings list -g $RG -n $APP --query "[].name" -o tsv

Section "Managed identity (needed for passwordless SQL + registry pull)"
az webapp identity show -g $RG -n $APP -o json

Section "Storage mounts already attached to the App Service"
az webapp config storage-account list -g $RG -n $APP -o json

Section "SQL databases on $SQL  (we need the database NAME)"
az sql db list -g $RG --server $SQL --query "[].{name:name, sku:currentServiceObjectiveName, maxGB:maxSizeBytes, status:status}" -o table

Section "Azure Files shares in $STOR"
az storage share-rm list -g $RG --storage-account $STOR --query "[].{name:name, quotaGiB:shareQuota, access:accessTier}" -o table

Section "Container registry"
az acr show -g $RG -n $ACR --query "{login:loginServer, sku:sku.name, adminEnabled:adminUserEnabled, publicAccess:publicNetworkAccess}" -o table
Write-Host "-- existing repositories (empty on a fresh registry is expected):"
az acr repository list -n $ACR -o tsv

Section "Your role assignments on the registry (need AcrPush to publish)"
$me = az ad signed-in-user show --query id -o tsv 2>$null
$acrId = az acr show -g $RG -n $ACR --query id -o tsv 2>$null
if ($me -and $acrId) {
    az role assignment list --assignee $me --scope $acrId --query "[].roleDefinitionName" -o tsv
} else {
    Write-Host "(could not resolve -- check with IT that you have AcrPush)"
}

Write-Host ""
Write-Host "Done. Paste everything above into the chat."
