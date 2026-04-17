"""
Shared utility functions for Azure Data Lake Storage (ADLS), 
Azure Key Vault, and PGP encryption/decryption operations.

This module provides reusable helpers for:
- ADLS file system operations (read, write, move)
- Azure Key Vault secret retrieval
- PGP encryption/decryption using GnuPG subprocess
- Path validation and security checks
"""
import base64
import logging
import os
import subprocess
import tempfile
import time

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.filedatalake import DataLakeServiceClient


# ---------------------------------------------------------------------------
# ADLS Helper Functions
# ---------------------------------------------------------------------------

def _ensure_dir(file_system_client, path: str) -> None:
    """Create the parent directory of path if it does not exist."""
    parent = '/'.join(path.split('/')[:-1])
    if parent:
        d = file_system_client.get_directory_client(parent)
        if not d.exists():
            d.create_directory()
            logging.info(f"Directory created: {parent}")


def _write_file(file_system_client, path: str, data: bytes) -> None:
    """Write binary data to ADLS file, creating parent directories as needed."""
    _ensure_dir(file_system_client, path)
    client = file_system_client.get_file_client(path)
    client.upload_data(data, overwrite=True)
    logging.info(f"File written: {path} ({len(data)} bytes)")


def _move_file(file_system_client, source_path: str, target_path: str) -> None:
    """
    Move file within ADLS using atomic rename when possible.
    Falls back to copy+delete if rename is not supported.
    """
    _ensure_dir(file_system_client, target_path)
    source_client = file_system_client.get_file_client(source_path)
    try:
        fs_name = file_system_client.file_system_name
        source_client.rename_file(f"{fs_name}/{target_path}")
        logging.info(f"File moved: {source_path} -> {target_path}")
    except Exception as rename_err:
        logging.warning(f"Rename failed, using copy/delete: {rename_err}")
        content = source_client.download_file().readall()
        dest_client = file_system_client.get_file_client(target_path)
        dest_client.upload_data(content, overwrite=True)
        source_client.delete_file()
        logging.info(f"File copied+deleted: {source_path} -> {target_path}")


def _route_source_file(file_system_client, source_path: str, destination_path: str, success: bool) -> str:
    """
    Route source file based on processing result.
    
    Args:
        file_system_client: ADLS file system client
        source_path: Original file path
        destination_path: Target path (archive or error folder)
        success: Whether processing succeeded
    
    Returns:
        Final destination path
    """
    if not success:
        logging.warning(f"Processing failed — routing to quarantine: {destination_path}")
        _move_file(file_system_client, source_path, destination_path)
        return destination_path

    try:
        _move_file(file_system_client, source_path, destination_path)
        return destination_path
    except Exception as move_err:
        logging.error(f"Move to '{destination_path}' failed: {move_err}")
        return destination_path


def _get_adls_filesystem(file_system_name: str):
    """
    Return an ADLS filesystem client using appropriate authentication.

    Production (Azure): Set ADLS_ACCOUNT_NAME → uses DefaultAzureCredential (Managed Identity).
    Local dev: Set ADLS_CONNECTION_STRING → uses Account Key (for local testing).
    
    Environment Variables:
        ADLS_ACCOUNT_NAME: Storage account name (production)
        ADLS_CONNECTION_STRING: Connection string (local development)
    
    Returns:
        DataLakeFileSystemClient configured for the specified container
    
    Raises:
        EnvironmentError: If neither credential method is configured
    """
    account_name = os.environ.get('ADLS_ACCOUNT_NAME')
    if account_name:
        account_url = f"https://{account_name}.dfs.core.windows.net"
        return DataLakeServiceClient(account_url=account_url, credential=DefaultAzureCredential()) \
            .get_file_system_client(file_system=file_system_name)

    connection_string = os.environ.get('ADLS_CONNECTION_STRING')
    if connection_string:
        return DataLakeServiceClient.from_connection_string(connection_string) \
            .get_file_system_client(file_system=file_system_name)

    raise EnvironmentError(
        "ADLS not configured. Set ADLS_ACCOUNT_NAME (production) "
        "or ADLS_CONNECTION_STRING (local dev) in environment variables."
    )


# ---------------------------------------------------------------------------
# PGP Helper Functions
# ---------------------------------------------------------------------------

def _reformat_pgp_armor(armor_text) -> str:
    """
    Re-wrap PGP ASCII armor body to standard 76-character lines with CRC24 checksum.
    Handles various armor formats and repairs malformed PGP blocks.
    
    Args:
        armor_text: PGP armored text (may be dict from Key Vault)
    
    Returns:
        Properly formatted PGP ASCII armor block
    """
    if isinstance(armor_text, dict):
        armor_text = armor_text.get('value') or armor_text.get('Value') or ''
    armor_text = str(armor_text).replace('\r\n', '\n').strip()
    lines = armor_text.split('\n')

    begin = next((l for l in lines if l.startswith('-----BEGIN PGP')), None)
    end   = next((l for l in lines if l.startswith('-----END PGP')),   None)
    
    if not begin or not end:
        # Missing headers — attempt to reconstruct from raw base64
        armor_text = armor_text.replace('\n', '').replace('\r', '').replace(' ', '')
        try:
            body_bytes = base64.b64decode(armor_text)
        except Exception:
            return armor_text
        wrapped = '\n'.join(armor_text[i:i+76] for i in range(0, len(armor_text), 76))
        crc = 0xB704CE
        for b in body_bytes:
            crc ^= b << 16
            for _ in range(8):
                crc <<= 1
                if crc & 0x1000000:
                    crc ^= 0x864CFB
        crc &= 0xFFFFFF
        crc_line = '=' + base64.b64encode(crc.to_bytes(3, 'big')).decode('ascii')
        return '-----BEGIN PGP PRIVATE KEY BLOCK-----\n\n' + wrapped + '\n' + crc_line + '\n-----END PGP PRIVATE KEY BLOCK-----\n'

    sub_headers, body_chunks = [], []
    in_block = False
    past_blank = False
    
    for line in lines:
        if line.startswith('-----BEGIN PGP'):
            in_block = True
            continue
        if line.startswith('-----END PGP'):
            break
        if not in_block:
            continue
        if not past_blank:
            if line == '':
                past_blank = True
            else:
                sub_headers.append(line)
        else:
            if line.startswith('=') and len(line) == 5:
                continue
            body_chunks.append(line)

    full_b64 = ''.join(body_chunks)
    wrapped = '\n'.join(full_b64[i:i+76] for i in range(0, len(full_b64), 76))

    # Calculate CRC24 checksum
    body_bytes = base64.b64decode(full_b64)
    crc = 0xB704CE
    for b in body_bytes:
        crc ^= b << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x864CFB
    crc &= 0xFFFFFF
    crc_line = '=' + base64.b64encode(crc.to_bytes(3, 'big')).decode('ascii')

    parts = [begin] + sub_headers + ['', wrapped, crc_line, end]
    return '\n'.join(parts) + '\n'


# GPG binary locations: Linux (Azure), Windows fallback
_GPG_CANDIDATES = [
    '/usr/bin/gpg2',
    '/usr/bin/gpg',
]

# Maximum file size allowed for in-memory operations (500 MB)
_MAX_FILE_BYTES = 500 * 1024 * 1024


def _find_gpg() -> str:
    """
    Locate GPG executable on the system.
    
    Returns:
        Path to gpg or gpg2 binary
    
    Raises:
        EnvironmentError: If GPG is not found
    """
    import shutil
    for candidate in _GPG_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which('gpg2') or shutil.which('gpg')
    if found:
        return found
    raise EnvironmentError(
        'gpg executable not found. On Azure Linux: add startup.sh with apt-get install -y gnupg2'
    )


def _get_pgp_secrets(key_vault_name: str, key_secret: str, passphrase_secret: str) -> tuple:
    """
    Retrieve PGP private key and passphrase from Azure Key Vault.
    
    Args:
        key_vault_name: Name of the Azure Key Vault
        key_secret: Secret name containing PGP private key
        passphrase_secret: Secret name containing passphrase
    
    Returns:
        Tuple of (private_key_armored, passphrase)
    
    Raises:
        Exception: If Key Vault access fails or secrets are not found
    """
    kv_uri = f"https://{key_vault_name}.vault.azure.net"
    sc = SecretClient(vault_url=kv_uri, credential=DefaultAzureCredential())
    private_key_armored = sc.get_secret(key_secret).value
    passphrase = sc.get_secret(passphrase_secret).value
    logging.info(f"PGP secrets retrieved from Key Vault: {key_vault_name}")
    return private_key_armored, passphrase


def _pgp_decrypt(private_key_armored: str, passphrase: str, encrypted_data: bytes) -> bytes:
    """
    Decrypt PGP-encrypted data using GnuPG subprocess with isolated temporary keyring.
    
    Security features:
    - Isolated temporary keyring per operation (no host keyring contamination)
    - Passphrase passed via pipe FD (never written to disk)
    - Encrypted data piped via stdin (never written to disk)
    - Automatic cleanup of temporary directories
    
    Supports all PGP algorithms including AEAD (RFC 4880bis / GnuPG 2.3+).
    
    Args:
        private_key_armored: PGP private key in ASCII armor format
        passphrase: Passphrase for the private key
        encrypted_data: Encrypted file content (bytes)
    
    Returns:
        Decrypted data (bytes)
    
    Raises:
        RuntimeError: If GPG import or decryption fails
    """
    private_key_armored = _reformat_pgp_armor(private_key_armored)
    if isinstance(passphrase, dict):
        passphrase = passphrase.get('value') or passphrase.get('Value') or ''
    passphrase = str(passphrase).strip()

    gpg = _find_gpg()

    with tempfile.TemporaryDirectory(prefix='pgp_tmp_') as tmpdir:
        gpg_home = os.path.join(tmpdir, 'gnupg')
        os.makedirs(gpg_home, mode=0o700)

        base_args = [gpg, '--homedir', gpg_home, '--batch', '--yes', '--no-tty']

        # Import private key
        import_result = subprocess.run(
            base_args + ['--import'],
            input=private_key_armored.encode('utf-8'),
            capture_output=True,
        )
        if import_result.returncode != 0:
            logging.error(f'gpg --import failed: {import_result.stderr.decode(errors="replace")}')
            raise RuntimeError('gpg --import failed. Check function logs for details.')

        dec_path = os.path.join(tmpdir, 'output.bin')

        # Use dedicated pipe fd for passphrase (security: never touches disk)
        # stdin (fd 0) carries the ciphertext
        pass_r_fd, pass_w_fd = os.pipe()
        try:
            os.write(pass_w_fd, passphrase.encode('utf-8'))
        finally:
            os.close(pass_w_fd)  # Close write-end so GPG sees EOF

        try:
            decrypt_result = subprocess.run(
                base_args + [
                    '--passphrase-fd', str(pass_r_fd),
                    '--pinentry-mode', 'loopback',
                    '--output', dec_path,
                    '--decrypt',
                ],
                input=encrypted_data,   # Ciphertext via stdin
                capture_output=True,
                pass_fds=(pass_r_fd,),  # Inherit only this fd
            )
        finally:
            os.close(pass_r_fd)
            
        if decrypt_result.returncode != 0:
            logging.error(f'gpg --decrypt failed: {decrypt_result.stderr.decode(errors="replace")}')
            raise RuntimeError('gpg --decrypt failed. Check function logs for details.')

        with open(dec_path, 'rb') as f:
            result = f.read()

    logging.info(f'PGP decryption successful: {len(encrypted_data)} -> {len(result)} bytes')
    return result
