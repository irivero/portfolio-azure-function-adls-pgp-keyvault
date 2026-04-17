# Testing Strategy and Test Cases

## Overview

This document outlines the comprehensive testing strategy for the Azure Functions PGP file processing application, including unit tests, integration tests, and end-to-end validation scenarios.

## Testing Philosophy

- **Security First**: All security features must be validated with explicit test cases
- **Production Scenarios**: Test cases reflect real-world production conditions
- **Failure Modes**: Explicit testing of error conditions and edge cases
- **Performance**: Validate concurrent processing and resource limits

## Test Levels

### 1. Unit Tests
Focus: Individual functions and methods in isolation

### 2. Integration Tests
Focus: Component interactions (ADLS, Key Vault, GPG subprocess)

### 3. End-to-End Tests
Focus: Complete workflows from HTTP request to file output

### 4. Security Tests
Focus: Path traversal, resource limits, credential handling

## Test Environment Setup

### Prerequisites

```bash
# Install test dependencies
pip install pytest pytest-mock pytest-cov pytest-asyncio

# Install Azure Storage Emulator (Windows)
# Or use Azurite (cross-platform)
npm install -g azurite
```

### Test Configuration

File: `tests/conftest.py` (fixtures and mocks)

```python
import pytest
from unittest.mock import Mock, MagicMock
from azure.storage.filedatalake import DataLakeServiceClient

@pytest.fixture
def mock_adls_client():
    """Mock ADLS client for testing without real storage."""
    client = MagicMock(spec=DataLakeServiceClient)
    # Configure mock behavior
    return client

@pytest.fixture
def mock_keyvault_client():
    """Mock Key Vault client for testing without real secrets."""
    client = MagicMock()
    client.get_secret.return_value.value = "test-secret"
    return client

@pytest.fixture
def sample_pgp_keypair():
    """Sample PGP key pair for testing (NOT FOR PRODUCTION)."""
    private_key = """-----BEGIN PGP PRIVATE KEY BLOCK-----
[Test key content - generated for testing only]
-----END PGP PRIVATE KEY BLOCK-----"""
    
    public_key = """-----BEGIN PGP PUBLIC KEY BLOCK-----
[Test key content]
-----END PGP PUBLIC KEY BLOCK-----"""
    
    passphrase = "test-passphrase-123"
    return private_key, public_key, passphrase
```

## Test Cases

### Category 1: Decryption Function Tests

#### TC-DEC-001: Successful Single File Decryption
**Objective**: Verify successful decryption of a single .pgp file

**Prerequisites**:
- Valid PGP private key in Key Vault
- One .pgp encrypted file in source folder

**Test Steps**:
1. Place encrypted file `test_data.csv.pgp` in source folder
2. Call decrypt endpoint with valid parameters
3. Verify decrypted file appears in destination folder
4. Verify original file moved to archive folder
5. Verify filename extension stripped (.csv not .csv.pgp)

**Expected Result**: 
```json
{
  "total": 1,
  "ok": 1,
  "failed": 0,
  "processed_files": ["test_data.csv"]
}
```
**Status Code**: 200

---

#### TC-DEC-002: Batch Decryption (Multiple Files)
**Objective**: Verify concurrent processing of multiple encrypted files

**Test Steps**:
1. Place 10 encrypted files in source folder
2. Call decrypt endpoint
3. Verify all files processed concurrently
4. Verify processing time < (sequential time / workers)

**Expected Result**: All 10 files decrypted successfully
**Performance**: < 5 seconds for 10 × 1MB files (4 workers)

---

#### TC-DEC-003: Decryption Failure - Invalid PGP Format
**Objective**: Verify graceful handling of corrupt encrypted files

**Test Steps**:
1. Place file with `.pgp` extension but corrupted content
2. Call decrypt endpoint
3. Verify file moved to error folder
4. Verify error logged with detailed message

**Expected Result**:
```json
{
  "total": 1,
  "ok": 0,
  "failed": 1,
  "error_files": ["corrupt.pgp: gpg --decrypt failed"]
}
```
**Status Code**: 500

---

#### TC-DEC-004: Decryption Failure - Wrong Passphrase
**Objective**: Verify behavior when Key Vault passphrase is incorrect

**Test Steps**:
1. Configure Key Vault with incorrect passphrase
2. Place valid encrypted file in source folder
3. Call decrypt endpoint

**Expected Result**: 
- File moved to error folder
- Error message: "decryption failed"
- Original file not lost

**Status Code**: 500

---

#### TC-DEC-005: Empty Source Folder
**Objective**: Verify behavior when no files to process

**Test Steps**:
1. Ensure source folder is empty
2. Call decrypt endpoint

**Expected Result**:
```json
{
  "total": 0,
  "ok": 0,
  "failed": 0,
  "message": "No files found in source folder"
}
```
**Status Code**: 200

---

#### TC-DEC-006: Non-PGP Files in Source Folder
**Objective**: Verify handling of files without .pgp extension

**Test Steps**:
1. Place `document.pdf` and `data.csv` in source folder
2. Call decrypt endpoint

**Expected Result**:
- Files moved to error folder
- Marked as "skipped"
- No decryption attempted

---

#### TC-DEC-007: File Size Limit Enforcement
**Objective**: Verify rejection of files exceeding 500 MB limit

**Test Steps**:
1. Place 600 MB encrypted file in source folder
2. Call decrypt endpoint

**Expected Result**:
- File moved to error folder
- Error: "File size exceeds limit"
- No memory exhaustion

---

#### TC-DEC-008: Batch Size Limit Enforcement
**Objective**: Verify rejection of requests with > 500 files

**Test Steps**:
1. Place 501 files in source folder
2. Call decrypt endpoint

**Expected Result**:
- HTTP 400 Bad Request
- Error: "Too many files in source folder (501). Maximum allowed: 500"
- No files processed

---

### Category 2: Encryption Function Tests

#### TC-ENC-001: Successful File Encryption
**Objective**: Verify successful encryption of staging file

**Prerequisites**:
- PGP public key stored in ADLS at `keys/public-key.asc`
- Data file in staging folder

**Test Steps**:
1. Place `export_data.csv` in staging folder
2. Call encryption endpoint with valid parameters
3. Verify encrypted file created with timestamp
4. Verify original archived with date partitioning (YYYY/MM/DD)
5. Verify staging folder cleaned up

**Expected Result**:
```json
{
  "status": "SUCCESS",
  "outputFileName": "data_encrypted_20260417120530.csv.pgp",
  "outputPath": "outbound/encrypted/data_encrypted_20260417120530.csv.pgp",
  "archivedPath": "archive/sent/2026/04/17/data_encrypted_20260417120530.csv",
  "originalSize": 1048576,
  "encryptedSize": 1049600
}
```
**Status Code**: 200

---

#### TC-ENC-002: No Staging File Found
**Objective**: Verify behavior when staging folder is empty

**Test Steps**:
1. Ensure staging folder is empty
2. Call encryption endpoint

**Expected Result**:
```json
{
  "status": "NO_FILE",
  "message": "No staging file found in temp folder"
}
```
**Status Code**: 404

---

#### TC-ENC-003: Empty Staging File
**Objective**: Verify rejection of zero-byte files

**Test Steps**:
1. Place 0-byte file in staging folder
2. Call encryption endpoint

**Expected Result**:
```json
{
  "status": "EMPTY_FILE",
  "message": "Staging file is empty — no payload to encrypt"
}
```
**Status Code**: 422

---

#### TC-ENC-004: Invalid PGP Public Key
**Objective**: Verify error handling when public key is malformed

**Test Steps**:
1. Replace public key with invalid content
2. Place data file in staging
3. Call encryption endpoint

**Expected Result**:
- Error log written to error folder
- Status: "ENCRYPTION_FAILED"
- Staging file not deleted (allows retry)

---

#### TC-ENC-005: Output Folder Cleanup
**Objective**: Verify output folder cleared before writing (full load pattern)

**Test Steps**:
1. Place old files in output folder
2. Execute encryption
3. Verify old files deleted before new file written
4. Verify only new file present in output folder

---

#### TC-ENC-006: Archive Date Partitioning
**Objective**: Verify correct date-based folder structure in archive

**Test Steps**:
1. Execute encryption on April 17, 2026 at 14:30:15
2. Verify archive path: `archive/sent/2026/04/17/data_encrypted_20260417143015.csv`
3. Execute again on April 18, 2026
4. Verify new path: `archive/sent/2026/04/18/data_encrypted_20260418*.csv`

---

### Category 3: Security Tests

#### TC-SEC-001: Path Traversal - Double Dot Attack
**Objective**: Prevent directory traversal via `..` segments

**Test Steps**:
1. Call decrypt endpoint with `source_folder: "../../etc/passwd"`
2. Call with `archive_folder: "../../../private"`

**Expected Result**:
- HTTP 400 Bad Request
- Error: "Invalid path (path traversal not allowed)"
- No file system access outside allowed paths

---

#### TC-SEC-002: Path Traversal - URL Encoding
**Objective**: Prevent encoded traversal attempts

**Attack Vectors**:
```
%2e%2e%2f     (../)
%2e%2e/       (../)
..%2f         (../)
%252e%252e%252f  (double-encoded ../)
```

**Expected Result**: All blocked with 400 Bad Request

---

#### TC-SEC-003: Path Traversal - Unicode Normalization
**Objective**: Prevent unicode lookalike character attacks

**Test Steps**:
1. Send request with fullwidth period (U+FF0E) instead of dot
2. Send unicode combining characters

**Expected Result**: Normalized and detected, request rejected

---

#### TC-SEC-004: PGP Key Vault Secret Validation
**Objective**: Verify secrets never logged or exposed

**Test Steps**:
1. Enable full logging
2. Execute decryption
3. Search logs for passphrase content
4. Search logs for private key content

**Expected Result**: 
- Secrets NOT present in logs
- Only message: "PGP secrets retrieved from Key Vault: <vault-name>"

---

#### TC-SEC-005: GPG Keyring Isolation
**Objective**: Verify operations don't contaminate host keyring

**Test Steps**:
1. List GPG keys before test: `gpg --list-keys`
2. Execute decryption
3. List GPG keys after test
4. Verify no new keys added to host keyring

---

#### TC-SEC-006: Temporary File Cleanup
**Objective**: Verify no sensitive data left on disk

**Test Steps**:
1. Execute decryption
2. Search `/tmp` for files matching `pgp_tmp_*`
3. Verify all temp directories deleted

---

### Category 4: Error Handling Tests

#### TC-ERR-001: Key Vault Connection Failure
**Objective**: Verify graceful handling when Key Vault unreachable

**Test Steps**:
1. Configure invalid Key Vault name
2. Call decrypt endpoint

**Expected Result**:
- HTTP 500 Internal Server Error
- Error: "Failed to retrieve PGP credentials from Key Vault"
- Detailed error logged

---

#### TC-ERR-002: ADLS Connection Failure
**Objective**: Verify behavior when storage account unreachable

**Test Steps**:
1. Configure invalid ADLS account name
2. Call decrypt endpoint

**Expected Result**:
- HTTP 500 Internal Server Error
- Error: "Storage connection error"
- Clear troubleshooting guidance in logs

---

#### TC-ERR-003: Missing Environment Variables
**Objective**: Verify startup validation of required configuration

**Test Steps**:
1. Remove `KEY_VAULT_NAME` from environment
2. Call decrypt endpoint

**Expected Result**:
- HTTP 400 Bad Request
- Error: "KEY_VAULT_NAME not configured"

---

#### TC-ERR-004: GPG Binary Not Found
**Objective**: Verify clear error when GPG not installed

**Test Steps**:
1. Rename/remove GPG binary
2. Call decrypt endpoint

**Expected Result**:
- HTTP 500 Internal Server Error
- Error: "gpg executable not found. On Azure Linux: add startup.sh with apt-get install -y gnupg2"

---

### Category 5: Performance Tests

#### TC-PERF-001: Concurrent Processing Performance
**Objective**: Measure throughput of batch decryption

**Test Data**:
- 100 files × 5 MB each
- 4 concurrent workers

**Metrics**:
- Total processing time
- Files per second
- Memory usage peak

**Target**: > 10 files/second, < 2 GB memory

---

#### TC-PERF-002: Large File Handling
**Objective**: Verify performance with maximum allowed file size

**Test Steps**:
1. Encrypt 500 MB file
2. Decrypt 500 MB file
3. Measure processing time

**Target**: < 60 seconds per file

---

## Running Tests

### Run All Tests
```bash
pytest tests/ -v
```

### Run with Coverage
```bash
pytest tests/ --cov=blueprints --cov-report=html
```

### Run Specific Category
```bash
# Decryption tests only
pytest tests/test_decrypt.py -v

# Security tests only
pytest tests/test_security.py -v
```

### Run Performance Tests
```bash
pytest tests/test_performance.py -v --benchmark
```

## Test Data Management

### Generate Test PGP Keys

```bash
# Generate test keypair (DO NOT use in production)
gpg --batch --gen-key <<EOF
%no-protection
Key-Type: RSA
Key-Length: 2048
Name-Real: Test User
Name-Email: test@example.com
Expire-Date: 0
%commit
EOF

# Export public key
gpg --armor --export test@example.com > tests/data/test-public-key.asc

# Export private key
gpg --armor --export-secret-keys test@example.com > tests/data/test-private-key.asc
```

### Generate Test Files

```bash
# Create sample CSV file
python tests/generate_test_data.py --size 1MB --output tests/data/sample.csv

# Encrypt with test key
gpg --recipient test@example.com --output tests/data/sample.csv.pgp --encrypt tests/data/sample.csv
```

## Continuous Integration

### GitHub Actions Workflow

File: `.github/workflows/test.yml`

```yaml
name: Test

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install GPG
        run: sudo apt-get install -y gnupg2
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Run tests
        run: pytest tests/ -v --cov=blueprints
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
```

## Test Maintenance

### When to Update Tests

1. **New Feature**: Add test cases for new functionality
2. **Bug Fix**: Add regression test to prevent recurrence
3. **Security Update**: Add security validation tests
4. **Performance Change**: Update performance benchmarks

### Test Review Checklist

- [ ] All happy path scenarios covered
- [ ] Error conditions explicitly tested
- [ ] Security features validated
- [ ] Edge cases (empty, large, malformed) included
- [ ] Performance targets defined
- [ ] Test data properly isolated (no production data)
- [ ] Mocks used for external dependencies
- [ ] Tests are deterministic (no flaky tests)

## Known Limitations

1. **GPG Subprocess**: Unit tests mock GPG calls; integration tests require real GPG binary
2. **Azure Services**: Integration tests require Azure emulator or test account
3. **Performance Tests**: Run times vary by hardware; use relative benchmarks

## Next Steps

- Implement automated load testing with Azure Load Testing
- Add chaos engineering tests (network failures, timeouts)
- Create synthetic monitoring for production endpoints

---

**Last Updated**: April 2026
**Test Coverage Target**: > 80% line coverage
**Status**: Reference Implementation