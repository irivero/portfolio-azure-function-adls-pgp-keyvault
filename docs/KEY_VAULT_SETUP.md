# Key Vault Setup Guide

Complete guide for configuring Azure Key Vault to securely store PGP credentials.

## Overview

Azure Key Vault provides secure storage for:
- PGP private keys (for decryption)
- PGP passphrases
- Connection strings (development only)
- API keys and other secrets

## Prerequisites

- Azure subscription with Key Vault creation permissions
- Azure CLI installed and authenticated
- PGP key pair generated

## Step 1: Generate PGP Key Pair

### Option A: Using GPG Command Line

```bash
# Generate new RSA 2048-bit key pair
gpg --batch --gen-key <<EOF
%no-protection
Key-Type: RSA
Key-Length: 2048
Name-Real: Your Organization
Name-Email: pgp@yourcompany.com
Expire-Date: 1y
%commit
EOF

# List keys to verify
gpg --list-keys

# Export public key (share with partners)
gpg --armor --export pgp@yourcompany.com > public-key.asc

# Export private key (store in Key Vault)
gpg --armor --export-secret-keys pgp@yourcompany.com > private-key.asc
```

### Option B: Using Kleopatra (Windows GUI)

1. Download and install [GPG4Win](https://gpg4win.org/)
2. Open Kleopatra
3. File → New Key Pair
4. Enter name and email
5. Click "Create"
6. Export public and private keys

## Step 2: Create Key Vault

```bash
# Set variables
RESOURCE_GROUP="rg-pgp-processing"
KEY_VAULT_NAME="kv-pgp-proc-prod"
LOCATION="eastus"

# Create Key Vault
az keyvault create \
  --name $KEY_VAULT_NAME \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --enable-rbac-authorization false \
  --enabled-for-template-deployment true
```

## Step 3: Store PGP Private Key

```bash
# Upload private key as secret
az keyvault secret set \
  --vault-name $KEY_VAULT_NAME \
  --name "pgp-private-key" \
  --file private-key.asc \
  --description "PGP private key for file decryption" \
  --tags environment=production purpose=encryption

# Verify secret was stored
az keyvault secret show \
  --vault-name $KEY_VAULT_NAME \
  --name "pgp-private-key" \
  --query "attributes.enabled"
```

## Step 4: Store PGP Passphrase

```bash
# Store passphrase (use strong passphrase!)
az keyvault secret set \
  --vault-name $KEY_VAULT_NAME \
  --name "pgp-passphrase" \
  --value "YourSecurePassphraseHere" \
  --description "Passphrase for PGP private key" \
  --tags environment=production purpose=encryption

# Verify
az keyvault secret list \
  --vault-name $KEY_VAULT_NAME \
  --query "[].{Name:name, Enabled:attributes.enabled}"
```

## Step 5: Configure Access Policies

### For Function App (Managed Identity)

```bash
# Get Function App Managed Identity Principal ID
PRINCIPAL_ID=$(az functionapp identity show \
  --name your-function-app \
  --resource-group $RESOURCE_GROUP \
  --query principalId \
  --output tsv)

# Grant read access to secrets
az keyvault set-policy \
  --name $KEY_VAULT_NAME \
  --object-id $PRINCIPAL_ID \
  --secret-permissions get list

echo "✓ Function App granted access to Key Vault"
```

### For Development Team (Your User)

```bash
# Get your user ID
USER_ID=$(az ad signed-in-user show --query id --output tsv)

# Grant yourself full access (for management)
az keyvault set-policy \
  --name $KEY_VAULT_NAME \
  --object-id $USER_ID \
  --secret-permissions get list set delete backup restore recover purge

echo "✓ User access configured"
```

### For CI/CD Service Principal

```bash
# Create service principal for deployments
SP=$(az ad sp create-for-rbac \
  --name "sp-pgp-deployment" \
  --role Contributor \
  --scopes /subscriptions/YOUR_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP \
  --output json)

SP_OBJECT_ID=$(echo $SP | jq -r '.appId' | \
  xargs -I {} az ad sp show --id {} --query id --output tsv)

# Grant service principal access
az keyvault set-policy \
  --name $KEY_VAULT_NAME \
  --object-id $SP_OBJECT_ID \
  --secret-permissions get list

# Save credentials for GitHub Secrets
echo "Add these to GitHub Secrets:"
echo "AZURE_CREDENTIALS: $SP"
```

## Step 6: Enable Security Features

### Enable Soft Delete and Purge Protection

```bash
# Soft delete (90-day retention)
az keyvault update \
  --name $KEY_VAULT_NAME \
  --enable-soft-delete true \
  --retention-days 90

# Purge protection (prevents permanent deletion)
az keyvault update \
  --name $KEY_VAULT_NAME \
  --enable-purge-protection true

echo "✓ Soft delete and purge protection enabled"
```

### Configure Diagnostic Logging

```bash
# Create Log Analytics workspace (if needed)
LOG_WORKSPACE=$(az monitor log-analytics workspace create \
  --resource-group $RESOURCE_GROUP \
  --workspace-name "law-keyvault-audit" \
  --query id \
  --output tsv)

# Enable diagnostic logs
az monitor diagnostic-settings create \
  --name "keyvault-audit-logs" \
  --resource $(az keyvault show \
    --name $KEY_VAULT_NAME \
    --query id --output tsv) \
  --logs '[
    {
      "category": "AuditEvent",
      "enabled": true,
      "retentionPolicy": {"enabled": true, "days": 365}
    }
  ]' \
  --workspace $LOG_WORKSPACE

echo "✓ Audit logging enabled"
```

### Network Security (Optional)

```bash
# Restrict to specific networks
az keyvault update \
  --name $KEY_VAULT_NAME \
  --default-action Deny

# Allow Azure services
az keyvault update \
  --name $KEY_VAULT_NAME \
  --bypass AzureServices

# Add your IP for management
YOUR_IP=$(curl -s ifconfig.me)
az keyvault network-rule add \
  --name $KEY_VAULT_NAME \
  --ip-address $YOUR_IP

# Add Function App subnet (if using VNet integration)
az keyvault network-rule add \
  --name $KEY_VAULT_NAME \
  --subnet /subscriptions/.../subnets/func-subnet
```

## Step 7: Verify Configuration

```bash
# Test secret retrieval
az keyvault secret show \
  --vault-name $KEY_VAULT_NAME \
  --name "pgp-private-key" \
  --query "value" \
  --output tsv | head -n 1

# Check access policies
az keyvault show \
  --name $KEY_VAULT_NAME \
  --query "properties.accessPolicies[].{ObjectId:objectId, Permissions:permissions}"

# View audit logs
az monitor diagnostic-settings show \
  --resource $(az keyvault show --name $KEY_VAULT_NAME --query id --output tsv)
```

## Step 8: Configure Function App

```bash
# Set Key Vault name in Function App settings
az functionapp config appsettings set \
  --name your-function-app \
  --resource-group $RESOURCE_GROUP \
  --settings \
    KEY_VAULT_NAME=$KEY_VAULT_NAME \
    PGP_KEY_SECRET_NAME=pgp-private-key \
    PGP_PASS_SECRET_NAME=pgp-passphrase
```

## Secret Rotation Strategy

### Automated Rotation (Recommended)

```bash
# Set secret expiration
az keyvault secret set-attributes \
  --vault-name $KEY_VAULT_NAME \
  --name "pgp-passphrase" \
  --expires $(date -d '+90 days' -u +%Y-%m-%dT%H:%M:%SZ)

# Create alert for expiring secrets
az monitor metrics alert create \
  --name "keyvault-secret-expiration" \
  --resource-group $RESOURCE_GROUP \
  --scopes $(az keyvault show --name $KEY_VAULT_NAME --query id --output tsv) \
  --condition "total SecretNearExpiration > 0" \
  --window-size 1d \
  --evaluation-frequency 1h \
  --action email admin@yourcompany.com
```

### Manual Rotation Process

1. Generate new PGP key pair
2. Store new key with versioned name: `pgp-private-key-v2`
3. Update Function App settings to use new secret name
4. Test thoroughly
5. Disable old secret
6. After 30 days, delete old secret (if soft-delete enabled)

## Backup and Disaster Recovery

```bash
# Backup all secrets
for SECRET in $(az keyvault secret list \
  --vault-name $KEY_VAULT_NAME \
  --query "[].name" -o tsv)
do
  az keyvault secret backup \
    --vault-name $KEY_VAULT_NAME \
    --name $SECRET \
    --file "${SECRET}.backup"
done

# Store backups securely (encrypted, off-site)
# Example: Azure Blob Storage with encryption
az storage blob upload-batch \
  --account-name backupstorage \
  --destination keyvault-backups \
  --source . \
  --pattern "*.backup"
```

## Security Best Practices

### 1. **Principle of Least Privilege**
- Grant only required permissions
- Use separate Key Vaults for dev/test/prod
- Regular access review

### 2. **Secret Management**
- ✅ Use Key Vault references in App Settings
- ✅ Enable secret expiration
- ✅ Never log secret values
- ✅ Rotate secrets regularly (90 days)
- ❌ Never commit secrets to source control

### 3. **Monitoring**
- Enable audit logging
- Set up alerts for:
  - Unusual access patterns
  - Failed authentication attempts
  - Secret expiration
  - Secret updates

### 4. **Network Security**
- Use Private Endpoints for production
- Restrict public access
- Allow only trusted networks/subnets

## Troubleshooting

### Issue: "Access denied" error

```bash
# Check access policies
az keyvault show \
  --name $KEY_VAULT_NAME \
  --query "properties.accessPolicies[?objectId=='$PRINCIPAL_ID']"

# Verify Managed Identity is enabled
az functionapp identity show \
  --name your-function-app \
  --resource-group $RESOURCE_GROUP
```

### Issue: Secret not found

```bash
# List all secrets
az keyvault secret list \
  --vault-name $KEY_VAULT_NAME

# Check secret name matches Function App setting
az functionapp config appsettings list \
  --name your-function-app \
  --resource-group $RESOURCE_GROUP \
  --query "[?name=='PGP_KEY_SECRET_NAME'].value"
```

### Issue: Network connectivity

```bash
# Test connectivity
nslookup ${KEY_VAULT_NAME}.vault.azure.net

# Check firewall rules
az keyvault network-rule list \
  --name $KEY_VAULT_NAME
```

## Compliance and Governance

### Azure Policy

```json
{
  "policyRule": {
    "if": {
      "allOf": [
        {
          "field": "type",
          "equals": "Microsoft.KeyVault/vaults"
        },
        {
          "field": "Microsoft.KeyVault/vaults/enableSoftDelete",
          "notEquals": "true"
        }
      ]
    },
    "then": {
      "effect": "deny"
    }
  }
}
```

### Tags for Organization

```bash
az keyvault update \
  --name $KEY_VAULT_NAME \
  --tags \
    Environment=Production \
    Application=PGPProcessing \
    Owner=DataEngineering \
    CostCenter=IT-001 \
    Compliance=PCI-DSS
```

## Additional Resources

- [Azure Key Vault Documentation](https://learn.microsoft.com/azure/key-vault/)
- [Key Vault Best Practices](https://learn.microsoft.com/azure/key-vault/general/best-practices)
- [Managed Identities with Key Vault](https://learn.microsoft.com/azure/active-directory/managed-identities-azure-resources/)
- [Key Vault Security](https://learn.microsoft.com/azure/key-vault/general/security-features)

---

**Security Checklist**:
- [ ] Soft delete enabled
- [ ] Purge protection enabled
- [ ] Audit logging configured
- [ ] Access policies follow least privilege
- [ ] Secrets have expiration dates
- [ ] Network restrictions configured (production)
- [ ] Backup strategy implemented
- [ ] Alert rules configured
- [ ] Tags applied for governance
