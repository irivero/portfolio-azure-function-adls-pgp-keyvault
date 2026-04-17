"""
Example API request payloads for testing endpoints.

These examples demonstrate the required parameters and typical use cases
for each function.
"""

# =============================================================================
# Decrypt Function Examples
# =============================================================================

# Example 1: Basic decryption request
decrypt_basic = {
    "file_system_name": "mycontainer",
    "source_folder": "inbound/encrypted",
    "destination_folder": "processed/decrypted",
    "error_folder": "errors/decryption-failed",
    "archive_folder": "archive/originals"
}

# Example 2: Decryption with nested folder structure
decrypt_nested = {
    "file_system_name": "dataexchange",
    "source_folder": "partners/vendor-a/inbound/2026/04",
    "destination_folder": "processed/vendor-a/decrypted",
    "error_folder": "errors/vendor-a/failed",
    "archive_folder": "archive/vendor-a/originals"
}

# Example 3: Production-ready with explicit paths
decrypt_production = {
    "file_system_name": "prod-data-lake",
    "source_folder": "ingress/encrypted/daily-reports",
    "destination_folder": "curated/reports/plaintext",
    "error_folder": "monitoring/errors/decryption",
    "archive_folder": "archive/encrypted-originals"
}

# =============================================================================
# Encrypt Function Examples
# =============================================================================

# Example 1: Basic encryption request
encrypt_basic = {
    "storageAccountName": "mystorage",
    "container": "dataexchange",
    "tempFolder": "staging/outbound",
    "outputFolder": "outbound/encrypted",
    "pgpKeyPath": "keys/partner-public-key.asc",
    "archiveFolder": "archive/sent",
    "errorFolder": "errors/encryption-failed",
    "filePrefix": "export_data_"
}

# Example 2: With Azure Data Factory integration (runId for traceability)
encrypt_with_adf = {
    "storageAccountName": "prodstorageacct",
    "container": "dataexchange",
    "tempFolder": "staging/exports/temp",
    "outputFolder": "outbound/partner/encrypted",
    "pgpKeyPath": "keys/partner-a-public-key.asc",
    "archiveFolder": "archive/exports",
    "errorFolder": "errors/encryption",
    "filePrefix": "employee_export_",
    "runId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"  # ADF pipeline run ID
}

# Example 3: Multi-partner scenario
encrypt_partner_specific = {
    "storageAccountName": "partnerstorageacct",
    "container": "partner-exchange",
    "tempFolder": "exports/staging/partner-b",
    "outputFolder": "outbound/partner-b/encrypted",
    "pgpKeyPath": "keys/partner-b-2026-public-key.asc",
    "archiveFolder": "archive/partner-b/exports",
    "errorFolder": "errors/partner-b",
    "filePrefix": "PARTNERB_DAILY_"
}

# =============================================================================
# curl Command Examples
# =============================================================================

# Decrypt function (local testing)
curl_decrypt_local = """
curl -X POST http://localhost:7071/api/decrypt-move-file-kv \\
  -H "Content-Type: application/json" \\
  -d '{
    "file_system_name": "testcontainer",
    "source_folder": "inbound/encrypted",
    "destination_folder": "processed/decrypted",
    "error_folder": "errors",
    "archive_folder": "archive"
  }'
"""

# Encrypt function (local testing)
curl_encrypt_local = """
curl -X POST http://localhost:7071/api/EncryptAndRename \\
  -H "Content-Type: application/json" \\
  -d '{
    "storageAccountName": "teststorage",
    "container": "testcontainer",
    "tempFolder": "staging",
    "outputFolder": "outbound",
    "pgpKeyPath": "keys/test-public-key.asc",
    "archiveFolder": "archive",
    "errorFolder": "errors",
    "filePrefix": "test_export_"
  }'
"""

# Decrypt function (Azure, with function key)
curl_decrypt_azure = """
curl -X POST https://your-function-app.azurewebsites.net/api/decrypt-move-file-kv?code=YOUR_FUNCTION_KEY \\
  -H "Content-Type: application/json" \\
  -d '{
    "file_system_name": "prodcontainer",
    "source_folder": "inbound/encrypted",
    "destination_folder": "processed/decrypted",
    "error_folder": "errors",
    "archive_folder": "archive"
  }'
"""

# =============================================================================
# Python requests Examples
# =============================================================================

import requests
import json

# Decrypt function call
def call_decrypt_function(base_url, function_key=None):
    """Example: Call decrypt function from Python."""
    endpoint = f"{base_url}/api/decrypt-move-file-kv"
    
    headers = {"Content-Type": "application/json"}
    if function_key:
        endpoint += f"?code={function_key}"
    
    payload = {
        "file_system_name": "mycontainer",
        "source_folder": "inbound/encrypted",
        "destination_folder": "processed/decrypted",
        "error_folder": "errors",
        "archive_folder": "archive"
    }
    
    response = requests.post(endpoint, headers=headers, json=payload)
    
    if response.status_code == 200:
        result = response.json()
        print(f"✓ Success: {result['ok']} files processed")
        print(f"✗ Failed: {result['failed']} files")
        return result
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None

# Encrypt function call
def call_encrypt_function(base_url, function_key=None):
    """Example: Call encrypt function from Python."""
    endpoint = f"{base_url}/api/EncryptAndRename"
    
    headers = {"Content-Type": "application/json"}
    if function_key:
        endpoint += f"?code={function_key}"
    
    payload = {
        "storageAccountName": "mystorage",
        "container": "dataexchange",
        "tempFolder": "staging/outbound",
        "outputFolder": "outbound/encrypted",
        "pgpKeyPath": "keys/partner-public-key.asc",
        "archiveFolder": "archive/sent",
        "errorFolder": "errors",
        "filePrefix": "export_"
    }
    
    response = requests.post(endpoint, headers=headers, json=payload)
    
    if response.status_code == 200:
        result = response.json()
        print(f"✓ Encrypted: {result['outputFileName']}")
        print(f"  Size: {result['originalSize']} → {result['encryptedSize']} bytes")
        return result
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None


# Example usage:
if __name__ == "__main__":
    # Local testing
    local_url = "http://localhost:7071"
    call_decrypt_function(local_url)
    
    # Azure testing
    # azure_url = "https://your-function-app.azurewebsites.net"
    # function_key = "your-function-key-here"
    # call_decrypt_function(azure_url, function_key)
