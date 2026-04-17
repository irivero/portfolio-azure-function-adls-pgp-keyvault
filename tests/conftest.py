"""
Test fixtures and shared configuration for unit and integration tests.

This module provides reusable fixtures for mocking Azure services,
generating test data, and setting up test environments.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from azure.storage.filedatalake import DataLakeServiceClient, FileSystemClient
from azure.keyvault.secrets import SecretClient


@pytest.fixture
def mock_adls_filesystem():
    """
    Mock ADLS file system client for testing without real storage.
    
    Returns a configured mock that simulates ADLS operations:
    - get_paths() returns list of mock file objects
    - get_file_client() returns mock file client
    - get_directory_client() returns mock directory client
    """
    mock_fs = MagicMock(spec=FileSystemClient)
    mock_fs.file_system_name = "test-container"
    
    # Mock file object
    mock_file = MagicMock()
    mock_file.name = "testfolder/sample.csv.pgp"
    mock_file.is_directory = False
    mock_file.content_length = 1024
    
    mock_fs.get_paths.return_value = [mock_file]
    
    # Mock file client for read/write operations
    mock_file_client = MagicMock()
    mock_file_client.download_file.return_value.readall.return_value = b"encrypted data"
    mock_fs.get_file_client.return_value = mock_file_client
    
    # Mock directory client
    mock_dir_client = MagicMock()
    mock_dir_client.exists.return_value = True
    mock_fs.get_directory_client.return_value = mock_dir_client
    
    return mock_fs


@pytest.fixture
def mock_keyvault_client():
    """
    Mock Azure Key Vault client for testing without real secrets.
    
    Returns mock that provides test PGP credentials.
    """
    mock_kv = MagicMock(spec=SecretClient)
    
    # Mock secret responses
    mock_key_secret = MagicMock()
    mock_key_secret.value = """-----BEGIN PGP PRIVATE KEY BLOCK-----
[Test private key - NOT FOR PRODUCTION]
-----END PGP PRIVATE KEY BLOCK-----"""
    
    mock_pass_secret = MagicMock()
    mock_pass_secret.value = "test-passphrase-123"
    
    def get_secret_side_effect(name):
        if "key" in name.lower():
            return mock_key_secret
        elif "pass" in name.lower():
            return mock_pass_secret
        raise ValueError(f"Unknown secret: {name}")
    
    mock_kv.get_secret.side_effect = get_secret_side_effect
    
    return mock_kv


@pytest.fixture
def sample_pgp_keypair():
    """
    Sample PGP key pair for testing encryption/decryption.
    
    WARNING: These are test keys only. NEVER use in production.
    
    Returns:
        Tuple of (private_key, public_key, passphrase)
    """
    private_key = """-----BEGIN PGP PRIVATE KEY BLOCK-----

lQPGBGabc123BCACpQ1234567890abcdefghijklmnopqrstuvwxyz
[Content truncated - test key only]
-----END PGP PRIVATE KEY BLOCK-----"""
    
    public_key = """-----BEGIN PGP PUBLIC KEY BLOCK-----

mQENBGabc123BCAC1234567890abcdefghijklmnopqrstuvwxyz
[Content truncated - test key only]
-----END PGP PUBLIC KEY BLOCK-----"""
    
    passphrase = "test-passphrase-do-not-use-in-prod"
    
    return private_key, public_key, passphrase


@pytest.fixture
def sample_encrypted_data():
    """
    Sample PGP-encrypted data for testing decryption.
    
    This is a small encrypted payload for unit tests.
    """
    return b"""-----BEGIN PGP MESSAGE-----

hQEMA1234567890ABCDEF
[Sample encrypted content]
-----END PGP MESSAGE-----"""


@pytest.fixture
def mock_env_vars(monkeypatch):
    """
    Set up mock environment variables for testing.
    
    Usage:
        def test_something(mock_env_vars):
            # Environment is configured
            pass
    """
    env_vars = {
        "KEY_VAULT_NAME": "test-keyvault",
        "ADLS_ACCOUNT_NAME": "teststorage",
        "PGP_KEY_SECRET_NAME": "test-pgp-key",
        "PGP_PASS_SECRET_NAME": "test-pgp-pass",
        "DECRYPT_WORKERS": "2",
        "LOCAL_DEV": "true"
    }
    
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    
    return env_vars


@pytest.fixture
def mock_http_request():
    """
    Mock Azure Functions HTTP request for testing endpoints.
    
    Returns:
        Mock HttpRequest with configurable body and params
    """
    mock_req = MagicMock()
    mock_req.get_json.return_value = {
        "file_system_name": "test-container",
        "source_folder": "inbound/encrypted",
        "destination_folder": "processed/decrypted",
        "error_folder": "errors",
        "archive_folder": "archive"
    }
    mock_req.params = {}
    
    return mock_req


@pytest.fixture
def mock_gpg_subprocess():
    """
    Mock subprocess calls to GPG binary.
    
    Returns mock that simulates successful GPG operations.
    """
    with patch('subprocess.run') as mock_run:
        # Mock successful import
        import_result = MagicMock()
        import_result.returncode = 0
        import_result.stdout = b"gpg: key imported"
        import_result.stderr = b""
        
        # Mock successful decrypt
        decrypt_result = MagicMock()
        decrypt_result.returncode = 0
        decrypt_result.stdout = b"decrypted data"
        decrypt_result.stderr = b""
        
        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get('args', [])
            if '--import' in cmd:
                return import_result
            elif '--decrypt' in cmd or '--encrypt' in cmd:
                return decrypt_result
            elif '--list-keys' in cmd:
                list_result = MagicMock()
                list_result.returncode = 0
                list_result.stdout = b"fpr:::::::::ABC123DEF456789:::"
                return list_result
            return MagicMock(returncode=0)
        
        mock_run.side_effect = run_side_effect
        yield mock_run


@pytest.fixture(autouse=True)
def reset_environment():
    """
    Reset environment after each test to prevent pollution.
    Runs automatically for all tests.
    """
    yield
    # Cleanup code runs after test completes
    # Clear any temp files, reset globals, etc.


# Test data generators

def generate_test_csv(rows=100, columns=5):
    """Generate sample CSV data for testing."""
    import io
    import csv
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow([f"Column{i}" for i in range(columns)])
    
    # Data rows
    for i in range(rows):
        writer.writerow([f"Value{i}_{j}" for j in range(columns)])
    
    return output.getvalue().encode('utf-8')


def generate_large_file(size_mb=10):
    """Generate large file content for performance testing."""
    return b"X" * (size_mb * 1024 * 1024)