# Azure Functions - Secure File Processing with PGP Encryption

[![Azure Functions](https://img.shields.io/badge/Azure-Functions-0078D4?logo=microsoft-azure)](https://azure.microsoft.com/services/functions/)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> **Reference Implementation** - This project demonstrates enterprise-grade architecture patterns for secure file processing in Azure. All configuration values and credentials shown are examples only.

## 📋 Overview

This Azure Functions application provides a production-ready solution for secure file processing with PGP encryption/decryption in cloud storage environments. It showcases:

- **🔐 Security-First Design** - Azure Key Vault integration, isolated GPG keyrings, path traversal prevention
- **☁️ Cloud-Native Architecture** - ADLS Gen2 integration, Managed Identity authentication, serverless compute
- **⚡ Enterprise Patterns** - Concurrent processing, comprehensive error handling, audit logging
- **🎯 Real-World Use Case** - B2B file exchange pipelines requiring encryption compliance

### Use Cases

- **Inbound Processing**: Decrypt encrypted files received from external partners
- **Outbound Processing**: Encrypt sensitive data before transmission to third parties
- **Compliance**: Meet regulatory requirements for data encryption in transit and at rest
- **Integration**: Fit into Azure Data Factory or Logic Apps orchestration workflows

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     Azure Functions App                          │
│                                                                  │
│  ┌────────────────────┐          ┌──────────────────────┐        │
│  │ Decrypt Function   │          │  Encrypt Function    │        │
│  │ POST /api/decrypt  │          │  POST /api/encrypt   │        │
│  └─────────┬──────────┘          └──────────┬───────────┘        │
│            │                                │                    │
│            └──────────┬─────────────────────┘                    │
│                       │                                          │
│              ┌────────▼────────┐                                 │
│              │  Shared Helpers │                                 │
│              │  • ADLS Ops     │                                 │
│              │  • PGP Crypto   │                                 │
│              │  • Key Vault    │                                 │
│              └────────┬────────┘                                 │
└───────────────────────┼──────────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
   ┌────▼──────┐  ┌────▼──────┐  ┌────▼──────┐
   │   ADLS    │  │ Key Vault │  │    GPG    │
   │  Storage  │  │  Secrets  │  │  Binary   │
   └───────────┘  └───────────┘  └───────────┘
```

### Components

| Component | Purpose | Technology |
|-----------|---------|------------|
| **Function App** | Serverless compute host | Azure Functions v4 (Python 3.11) |
| **ADLS Gen2** | Cloud data lake storage | Azure Data Lake Storage |
| **Key Vault** | Secure secret management | Azure Key Vault |
| **GPG** | PGP cryptographic operations | GnuPG 2.x |
| **Managed Identity** | Passwordless authentication | Azure Active Directory |

## 📡 API Endpoints

### 1. Decrypt and Move Files (Batch)

Scan ADLS folder for `.pgp` files, decrypt with Key Vault credentials, route based on result.

**Endpoint**: `POST /api/decrypt-move-file-kv`

**Request Body**:
```json
{
  "file_system_name": "mycontainer",
  "source_folder": "inbound/encrypted",
  "destination_folder": "processed/decrypted",
  "error_folder": "errors/decryption-failed",
  "archive_folder": "archive/originals"
}
```

**Response** (200/207/500):
```json
{
  "total": 10,
  "ok": 8,
  "failed": 2,
  "skipped": 0,
  "processed_files": ["file1.csv", "file2.csv"],
  "error_files": ["file3.pgp: decryption failed"]
}
```

**Status Codes**:
- `200` - All files processed successfully
- `207` - Partial success (some files failed)
- `400` - Invalid request parameters
- `500` - All files failed or system error

### 2. Encrypt and Archive (Single)

Find staging file, encrypt with public key, archive with date partitioning.

**Endpoint**: `POST /api/EncryptAndRename`

**Request Body**:
```json
{
  "storageAccountName": "mystorage",
  "container": "dataexchange",
  "tempFolder": "staging/outbound",
  "outputFolder": "outbound/encrypted",
  "pgpKeyPath": "keys/partner-public-key.asc",
  "archiveFolder": "archive/sent",
  "errorFolder": "errors/encryption-failed",
  "filePrefix": "export_data_",
  "runId": "optional-pipeline-id"
}
```

**Response** (200):
```json
{
  "status": "SUCCESS",
  "outputFileName": "export_data_20260417120530.csv.pgp",
  "outputPath": "outbound/encrypted/export_data_20260417120530.csv.pgp",
  "archivedPath": "archive/sent/2026/04/17/export_data_20260417120530.csv",
  "originalSize": 1048576,
  "encryptedSize": 1049600
}
```

## 🚀 Quick Start

### Prerequisites

- **Python 3.11** or higher
- **Azure Functions Core Tools 4.x** ([Install](https://learn.microsoft.com/azure/azure-functions/functions-run-local))
- **Azure CLI** ([Install](https://learn.microsoft.com/cli/azure/install-azure-cli))
- **GnuPG 2.x** (Linux: `apt-get install gnupg2`, Windows: [GPG4Win](https://gpg4win.org/))

### Local Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/azure-functions-pgp-processing.git
   cd azure-functions-pgp-processing
   ```

2. **Create virtual environment**
   ```bash
   python -m venv .venv
   
   # Windows
   .venv\Scripts\activate
   
   # Linux/Mac
   source .venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure local settings**
   
   Copy `examples/local.settings.json.example` to `local.settings.json` and update:
   
   ```json
   {
     "IsEncrypted": false,
     "Values": {
       "AzureWebJobsStorage": "UseDevelopmentStorage=true",
       "FUNCTIONS_WORKER_RUNTIME": "python",
       "ADLS_CONNECTION_STRING": "DefaultEndpointsProtocol=https;AccountName=...",
       "KEY_VAULT_NAME": "your-keyvault-name",
       "PGP_KEY_SECRET_NAME": "pgp-private-key",
       "PGP_PASS_SECRET_NAME": "pgp-passphrase",
       "LOCAL_DEV": "true"
     }
   }
   ```

5. **Run locally**
   ```bash
   func start
   ```
   
   Functions available at:
   - Decrypt: `http://localhost:7071/api/decrypt-move-file-kv`
   - Encrypt: `http://localhost:7071/api/EncryptAndRename`

## 🔐 Security Features

### 1. **Azure Key Vault Integration**
- PGP private keys and passphrases stored securely in Key Vault
- Never hardcoded or committed to source control
- Retrieved at runtime using Managed Identity

### 2. **Isolated GPG Keyrings**
- Each operation uses a temporary, isolated GPG home directory
- No contamination of host keyring
- Automatic cleanup after operation

### 3. **Path Traversal Protection**
- Validates all folder paths for `..` segments
- Blocks percent-encoded traversal attempts (`%2e%2e`, `%252e`)
- Normalizes Unicode to prevent lookalike character attacks

### 4. **Resource Limits**
- Maximum file size: 500 MB (configurable)
- Maximum batch size: 500 files per request
- In-memory processing guards against disk exhaustion

### 5. **Passphrase Handling**
- Passphrase passed to GPG via pipe file descriptor
- Never written to disk or logged
- Cleared from memory after use

## 🧪 Testing

See [docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md) for comprehensive test cases and strategy.

### Quick Test

```bash
# Install test dependencies
pip install pytest pytest-mock pytest-cov

# Run tests
pytest tests/ -v --cov=blueprints
```

## 📦 Deployment

### Azure Deployment

1. **Create Azure resources**
   ```bash
   # Create Resource Group
   az group create --name rg-functions-pgp --location eastus
   
   # Create Storage Account
   az storage account create \
     --name stfuncpgp \
     --resource-group rg-functions-pgp \
     --sku Standard_LRS \
     --enable-hierarchical-namespace true
   
   # Create Function App
   az functionapp create \
     --name func-pgp-processing \
     --resource-group rg-functions-pgp \
     --storage-account stfuncpgp \
     --runtime python \
     --runtime-version 3.11 \
     --os-type Linux \
     --functions-version 4
   
   # Enable Managed Identity
   az functionapp identity assign \
     --name func-pgp-processing \
     --resource-group rg-functions-pgp
   ```

2. **Configure Key Vault access**
   ```bash
   # Get Managed Identity Principal ID
   PRINCIPAL_ID=$(az functionapp identity show \
     --name func-pgp-processing \
     --resource-group rg-functions-pgp \
     --query principalId -o tsv)
   
   # Grant Key Vault access
   az keyvault set-policy \
     --name your-keyvault-name \
     --object-id $PRINCIPAL_ID \
     --secret-permissions get list
   ```

3. **Set application settings**
   ```bash
   az functionapp config appsettings set \
     --name func-pgp-processing \
     --resource-group rg-functions-pgp \
     --settings \
       ADLS_ACCOUNT_NAME=stfuncpgp \
       KEY_VAULT_NAME=your-keyvault-name \
       PGP_KEY_SECRET_NAME=pgp-private-key \
       PGP_PASS_SECRET_NAME=pgp-passphrase \
       DECRYPT_WORKERS=4
   ```

4. **Deploy function code**
   ```bash
   func azure functionapp publish func-pgp-processing
   ```

For detailed deployment guide, see [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md).

## 📚 Documentation

- **[Architecture Design](docs/ARCHITECTURE.md)** - Detailed system design and patterns
- **[Deployment Guide](docs/DEPLOYMENT_GUIDE.md)** - Step-by-step Azure deployment
- **[Testing Strategy](docs/TESTING_STRATEGY.md)** - Test cases and validation approach
- **[Key Vault Setup](docs/KEY_VAULT_SETUP.md)** - Secret management configuration
- **[API Reference](docs/API_REFERENCE.md)** - Complete endpoint documentation

## 🛠️ Configuration Reference

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ADLS_ACCOUNT_NAME` | Yes (Prod) | - | ADLS storage account name (for Managed Identity) |
| `ADLS_CONNECTION_STRING` | Yes (Dev) | - | ADLS connection string (for local development) |
| `KEY_VAULT_NAME` | Yes | - | Azure Key Vault name |
| `PGP_KEY_SECRET_NAME` | No | `pgp-private-key` | Key Vault secret name for PGP private key |
| `PGP_PASS_SECRET_NAME` | No | `pgp-passphrase` | Key Vault secret name for passphrase |
| `DECRYPT_WORKERS` | No | `4` | Concurrent workers for batch decryption |
| `LOCAL_DEV` | No | `false` | Set to `true` to exclude MSI in local dev |

### Authentication Modes

| Environment | ADLS | Key Vault | Method |
|-------------|------|-----------|--------|
| **Local Dev** | Connection String | DefaultAzureCredential | Visual Studio Code / Azure CLI |
| **Production** | Managed Identity | Managed Identity | System-assigned MI |

## 🤝 Contributing

This is a reference implementation for portfolio purposes. Feel free to fork and adapt for your own use cases.

### Customization Ideas

- Add support for multiple key pairs (multi-tenant scenarios)
- Implement async processing with Azure Storage Queues
- Add Azure Application Insights for advanced telemetry
- Extend to support S/MIME or other encryption standards
- Add webhook notifications for processing status

## 📄 License

This project is provided as a reference implementation under the MIT License. See [LICENSE](LICENSE) file for details.

## ⚠️ Disclaimer

This is a reference project for demonstration and portfolio purposes. All credentials, keys, and configuration values are examples. Never commit real secrets to source control.

For production use:
- Review and adapt security configurations to your requirements
- Implement proper RBAC and network security
- Enable Azure Monitor and logging
- Follow your organization's compliance and governance policies

## 📧 Contact

For questions or feedback about this reference implementation:
- Open an issue on GitHub
- Connect on [LinkedIn](https://linkedin.com/in/yourprofile)

---

**Built with Azure Functions | Python | PGP/GPG**

