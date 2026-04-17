# Azure Deployment Guide

Complete step-by-step guide for deploying the PGP file processing solution to Azure.

## Prerequisites

Before starting deployment, ensure you have:

- ✅ **Azure Subscription** with appropriate permissions
- ✅ **Azure CLI** installed and configured ([Install Guide](https://learn.microsoft.com/cli/azure/install-azure-cli))
- ✅ **Azure Functions Core Tools 4.x** ([Install Guide](https://learn.microsoft.com/azure/azure-functions/functions-run-local))
- ✅ **Python 3.11** installed locally
- ✅ **PGP key pair** generated for your use case

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│ Resource Group: rg-pgp-processing-prod                  │
│                                                          │
│  ┌──────────────────┐     ┌────────────────┐           │
│  │  Function App    │────▶│  ADLS Gen2     │           │
│  │  (Linux Python)  │     │  Storage       │           │
│  └────────┬─────────┘     └────────────────┘           │
│           │                                             │
│           │ Managed Identity                            │
│           ▼                                             │
│  ┌──────────────────┐                                  │
│  │  Key Vault       │                                  │
│  │  (PGP Secrets)   │                                  │
│  └──────────────────┘                                  │
│                                                          │
│  ┌──────────────────┐                                  │
│  │  App Insights    │                                  │
│  │  (Monitoring)    │                                  │
│  └──────────────────┘                                  │
└─────────────────────────────────────────────────────────┘
```

## Deployment Steps

### Step 1: Login to Azure

```bash
# Login to Azure
az login

# Set default subscription (if you have multiple)
az account set --subscription "Your Subscription Name"

# Verify selected subscription
az account show
```

### Step 2: Create Resource Group

```bash
# Define variables
RESOURCE_GROUP="rg-pgp-processing-prod"
LOCATION="eastus"

# Create resource group
az group create \
  --name $RESOURCE_GROUP \
  --location $LOCATION

echo "✓ Resource group created: $RESOURCE_GROUP"
```

### Step 3: Create Storage Account (ADLS Gen2)

```bash
STORAGE_ACCOUNT="stpgpprocessing$(date +%s | tail -c 6)"  # Unique name
CONTAINER="dataexchange"

# Create storage account with hierarchical namespace (ADLS Gen2)
az storage account create \
  --name $STORAGE_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --sku Standard_LRS \
  --kind StorageV2 \
  --enable-hierarchical-namespace true \
  --allow-blob-public-access false

echo "✓ Storage account created: $STORAGE_ACCOUNT"

# Create container
az storage container create \
  --name $CONTAINER \
  --account-name $STORAGE_ACCOUNT \
  --auth-mode login

# Create folder structure
for folder in "inbound/encrypted" "processed/decrypted" "errors" "archive" "staging" "outbound/encrypted" "keys"
do
  az storage fs directory create \
    --name $folder \
    --file-system $CONTAINER \
    --account-name $STORAGE_ACCOUNT \
    --auth-mode login
done

echo "✓ Container and folders created"
```

### Step 4: Create Key Vault

```bash
KEY_VAULT_NAME="kv-pgp-proc-$(date +%s | tail -c 6)"  # Unique name

# Create Key Vault
az keyvault create \
  --name $KEY_VAULT_NAME \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --enable-rbac-authorization false

echo "✓ Key Vault created: $KEY_VAULT_NAME"
```

### Step 5: Store PGP Secrets in Key Vault

```bash
# Store PGP private key
# Note: Replace with your actual key file path
az keyvault secret set \
  --vault-name $KEY_VAULT_NAME \
  --name "pgp-private-key" \
  --file /path/to/your/private-key.asc \
  --description "PGP private key for file decryption"

# Store PGP passphrase
# Note: Replace with your actual passphrase
az keyvault secret set \
  --vault-name $KEY_VAULT_NAME \
  --name "pgp-passphrase" \
  --value "YourSecurePassphraseHere" \
  --description "Passphrase for PGP private key"

echo "✓ PGP secrets stored in Key Vault"
```

### Step 6: Create Function App

```bash
FUNCTION_APP_NAME="func-pgp-proc-$(date +%s | tail -c 6)"  # Unique name

# Create Function App (Linux, Python 3.11)
az functionapp create \
  --name $FUNCTION_APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --storage-account $STORAGE_ACCOUNT \
  --runtime python \
  --runtime-version 3.11 \
  --os-type Linux \
  --functions-version 4 \
  --consumption-plan-location $LOCATION

echo "✓ Function App created: $FUNCTION_APP_NAME"

# Enable system-assigned Managed Identity
az functionapp identity assign \
  --name $FUNCTION_APP_NAME \
  --resource-group $RESOURCE_GROUP

echo "✓ Managed Identity enabled"
```

### Step 7: Configure Function App Settings

```bash
# Get Managed Identity Principal ID
PRINCIPAL_ID=$(az functionapp identity show \
  --name $FUNCTION_APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --query principalId \
  --output tsv)

echo "Managed Identity Principal ID: $PRINCIPAL_ID"

# Set application settings
az functionapp config appsettings set \
  --name $FUNCTION_APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --settings \
    ADLS_ACCOUNT_NAME=$STORAGE_ACCOUNT \
    KEY_VAULT_NAME=$KEY_VAULT_NAME \
    PGP_KEY_SECRET_NAME=pgp-private-key \
    PGP_PASS_SECRET_NAME=pgp-passphrase \
    DECRYPT_WORKERS=4 \
    FUNCTIONS_WORKER_RUNTIME=python \
    PYTHON_ISOLATE_WORKER_DEPENDENCIES=1

echo "✓ Application settings configured"
```

### Step 8: Grant Managed Identity Permissions

#### Grant Storage Access

```bash
# Get storage account resource ID
STORAGE_ID=$(az storage account show \
  --name $STORAGE_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --query id \
  --output tsv)

# Assign "Storage Blob Data Contributor" role
az role assignment create \
  --assignee $PRINCIPAL_ID \
  --role "Storage Blob Data Contributor" \
  --scope $STORAGE_ID

echo "✓ Storage access granted to Managed Identity"
```

#### Grant Key Vault Access

```bash
# Grant Key Vault secrets access
az keyvault set-policy \
  --name $KEY_VAULT_NAME \
  --object-id $PRINCIPAL_ID \
  --secret-permissions get list

echo "✓ Key Vault access granted to Managed Identity"
```

### Step 9: Configure Startup Command (Install GPG)

```bash
# Set startup command to install GnuPG
az functionapp config set \
  --name $FUNCTION_APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --startup-file "startup.sh"

echo "✓ Startup script configured"
```

### Step 10: Deploy Function Code

```bash
# Navigate to project directory
cd /path/to/azure-functions-pgp-processing

# Deploy to Azure
func azure functionapp publish $FUNCTION_APP_NAME

echo "✓ Function code deployed"
```

### Step 11: Upload PGP Public Key to Storage

```bash
# Upload partner's public key for encryption
az storage blob upload \
  --account-name $STORAGE_ACCOUNT \
  --container-name $CONTAINER \
  --name "keys/partner-public-key.asc" \
  --file /path/to/partner-public-key.asc \
  --auth-mode login

echo "✓ Public key uploaded to storage"
```

### Step 12: Verify Deployment

```bash
# Get function URL
FUNCTION_URL=$(az functionapp function show \
  --name $FUNCTION_APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --function-name DecryptAndMoveFileWithKeyVault \
  --query invokeUrlTemplate \
  --output tsv)

echo "Decrypt Function URL: $FUNCTION_URL"

# Get function key
FUNCTION_KEY=$(az functionapp keys list \
  --name $FUNCTION_APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --query functionKeys.default \
  --output tsv)

echo "Function Key: $FUNCTION_KEY"

# Test endpoint (with sample request)
curl -X POST "${FUNCTION_URL}?code=${FUNCTION_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "file_system_name": "dataexchange",
    "source_folder": "inbound/encrypted",
    "destination_folder": "processed/decrypted",
    "error_folder": "errors",
    "archive_folder": "archive"
  }'
```

## Post-Deployment Configuration

### Enable Application Insights (Optional but Recommended)

```bash
# Create Application Insights
az monitor app-insights component create \
  --app $FUNCTION_APP_NAME \
  --location $LOCATION \
  --resource-group $RESOURCE_GROUP

# Get instrumentation key
INSTRUMENTATION_KEY=$(az monitor app-insights component show \
  --app $FUNCTION_APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --query instrumentationKey \
  --output tsv)

# Configure Function App to use App Insights
az functionapp config appsettings set \
  --name $FUNCTION_APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --settings \
    APPINSIGHTS_INSTRUMENTATIONKEY=$INSTRUMENTATION_KEY

echo "✓ Application Insights configured"
```

### Configure Diagnostic Logs

```bash
# Enable diagnostic logs
az monitor diagnostic-settings create \
  --name "function-logs" \
  --resource $(az functionapp show \
    --name $FUNCTION_APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --query id --output tsv) \
  --logs '[
    {
      "category": "FunctionAppLogs",
      "enabled": true,
      "retentionPolicy": {"enabled": true, "days": 30}
    }
  ]' \
  --workspace $(az monitor log-analytics workspace show \
    --resource-group $RESOURCE_GROUP \
    --workspace-name "law-monitoring" \
    --query id --output tsv)
```

## Security Hardening

### 1. Network Security

```bash
# Restrict storage account to specific networks (optional)
az storage account update \
  --name $STORAGE_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --default-action Deny

# Allow Function App subnet
az storage account network-rule add \
  --account-name $STORAGE_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --subnet $(az functionapp vnet-integration list \
    --name $FUNCTION_APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --query [0].id --output tsv)
```

### 2. Key Vault Network Rules

```bash
# Restrict Key Vault access (optional)
az keyvault update \
  --name $KEY_VAULT_NAME \
  --default-action Deny

# Allow Function App IP
az keyvault network-rule add \
  --name $KEY_VAULT_NAME \
  --ip-address $(az functionapp show \
    --name $FUNCTION_APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --query outboundIpAddresses --output tsv | cut -d',' -f1)
```

### 3. Enable HTTPS Only

```bash
az functionapp update \
  --name $FUNCTION_APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --set httpsOnly=true
```

## Troubleshooting

### Check Function Logs

```bash
# Stream live logs
func azure functionapp logstream $FUNCTION_APP_NAME

# Or via Azure CLI
az webapp log tail \
  --name $FUNCTION_APP_NAME \
  --resource-group $RESOURCE_GROUP
```

### Verify GPG Installation

```bash
# Check startup script execution
az webapp log download \
  --name $FUNCTION_APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --log-file logs.zip
```

### Test Managed Identity Access

```bash
# Test Key Vault access
az webapp run \
  --name $FUNCTION_APP_NAME \
  --command "curl -H 'Metadata: true' http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://vault.azure.net"
```

## Cleanup (Development/Testing)

```bash
# Delete entire resource group (WARNING: Deletes all resources)
az group delete \
  --name $RESOURCE_GROUP \
  --yes \
  --no-wait

echo "✓ Resource group deletion initiated"
```

## Cost Estimation

Estimated monthly costs for production deployment:

| Service | SKU/Tier | Estimated Cost |
|---------|----------|----------------|
| Function App | Consumption Plan | $0 - $20 (first 1M executions free) |
| Storage (ADLS Gen2) | Standard LRS | $0.02/GB stored + operations |
| Key Vault | Standard | $0.03/10K operations |
| App Insights | Pay-as-you-go | $2.30/GB ingested |

**Total Estimated**: $10-30/month (low volume)

## Next Steps

1. ✅ Set up CI/CD pipeline with GitHub Actions (see `.github/workflows/deploy.yml`)
2. ✅ Configure alerts and monitoring in Azure Monitor
3. ✅ Implement key rotation policy in Key Vault
4. ✅ Set up Azure Data Factory pipelines to trigger functions
5. ✅ Configure backup and disaster recovery

---

**Deployment Status Checklist**:

- [ ] Resource group created
- [ ] Storage account (ADLS Gen2) created
- [ ] Key Vault created with PGP secrets
- [ ] Function App created
- [ ] Managed Identity configured
- [ ] RBAC permissions granted
- [ ] GPG startup script configured
- [ ] Function code deployed
- [ ] Public key uploaded
- [ ] Endpoints tested successfully
- [ ] Application Insights enabled
- [ ] Network security configured

For additional support, see [Azure Functions Documentation](https://learn.microsoft.com/azure/azure-functions/).
