"""
Azure Function: PGP Encryption with File Archival

This function reads a staging file from ADLS, encrypts it with a PGP public key,
writes the encrypted output to a designated folder, archives the original file
with date partitioning (YYYY/MM/DD/), and cleans up staging files.

Endpoint: POST /api/EncryptAndRename

Use Case: Secure file transmission pipeline where sensitive data must be
encrypted before being picked up by external systems.
"""
import os
import json
import logging
import re
import subprocess
import tempfile
import azure.functions as func
from datetime import datetime, timezone
from azure.identity import DefaultAzureCredential
from azure.storage.filedatalake import DataLakeServiceClient

bp = func.Blueprint()


def _get_credential() -> DefaultAzureCredential:
    """
    Return DefaultAzureCredential, optionally excluding Managed Identity during local dev.
    
    Environment Variables:
        LOCAL_DEV (str): Set to "true" to exclude MSI (for local development)
    """
    exclude_msi = os.environ.get("LOCAL_DEV", "false").lower() == "true"
    return DefaultAzureCredential(exclude_managed_identity_credential=exclude_msi)


_GPG_CANDIDATES = ['/usr/bin/gpg2', '/usr/bin/gpg']
_MAX_FILE_BYTES = 500 * 1024 * 1024


def _find_gpg() -> str:
    """Locate GPG executable on system."""
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


def _write_error_log(fs_client, error_folder: str, storage_account_name: str,
                     container: str, temp_folder: str, error_message: str) -> None:
    """Write error log to ADLS error folder. Never raises."""
    try:
        client = fs_client or DataLakeServiceClient(
            account_url=f"https://{storage_account_name}.dfs.core.windows.net",
            credential=_get_credential()
        ).get_file_system_client(file_system=container)
        
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        error_log_path = f"{error_folder}/error_{timestamp}.log"
        log_content = (
            f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            f"Error: {error_message}\n"
            f"TempFolder: {temp_folder}\n"
        )
        client.get_file_client(error_log_path).upload_data(
            log_content.encode("utf-8"), overwrite=True
        )
        logging.info(f"Error log written to {error_log_path}")
    except Exception as log_err:
        logging.warning(f"Could not write error log to ADLS: {log_err}")


@bp.function_name(name="EncryptAndRename")
@bp.route(route="EncryptAndRename", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def encrypt_and_rename(req: func.HttpRequest) -> func.HttpResponse:
    """
    Encrypt staging file with PGP and archive with date partitioning.
    
    Request Body (JSON):
        storageAccountName (str): ADLS storage account name
        container (str): ADLS container/filesystem name
        tempFolder (str): Staging folder containing source file
        outputFolder (str): Destination folder for encrypted output
        pgpKeyPath (str): ADLS path to PGP public key (.asc) file
        archiveFolder (str): Root archive folder (YYYY/MM/DD subfolders created automatically)
        errorFolder (str): Folder for error logs
        filePrefix (str, optional): Output filename prefix (default: "data_encrypted_")
        runId (str, optional): Pipeline run identifier for logging/traceability
    
    Returns:
        JSON response with encryption result:
        Success (200):
        {
            "status": "SUCCESS",
            "message": "File encrypted and renamed successfully.",
            "outputFileName": str,
            "outputPath": str,
            "archivedFileName": str,
            "archivedPath": str,
            "originalSize": int,
            "encryptedSize": int
        }
        
        Errors (4xx/5xx):
        {
            "status": "ERROR_CODE",
            "message": str,
            "outputFileName": null,
            "tempFolder": str
        }
    
    Status Codes:
        200: Success
        400: Invalid request parameters
        404: No staging file found
        422: Empty staging file
        500: Processing error
    
    Processing Phases:
        1. Setup - Connect to ADLS and load PGP public key
        2. Encrypt - Read staging file and encrypt with GPG
        3. Persist - Write output, archive original, cleanup staging
    """
    logging.info("EncryptAndRename function triggered.")

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON body", status_code=400)

    # Extract parameters
    storage_account_name = body.get("storageAccountName")
    container = body.get("container")
    temp_folder = body.get("tempFolder", "").rstrip("/")
    output_folder = body.get("outputFolder", "").rstrip("/")
    pgp_key_path = body.get("pgpKeyPath")
    file_prefix = body.get("filePrefix", "data_encrypted_")
    archive_folder = body.get("archiveFolder")
    error_folder = body.get("errorFolder", "").rstrip("/")

    # Validate required parameters
    if not all([storage_account_name, container, temp_folder, output_folder, 
                archive_folder, pgp_key_path, error_folder]):
        return func.HttpResponse(
            "Missing required parameters: storageAccountName, container, tempFolder, "
            "outputFolder, archiveFolder, pgpKeyPath, errorFolder are all required.",
            status_code=400
        )

    # Security: Prevent path traversal in pgp_key_path
    if '..' in pgp_key_path or pgp_key_path.startswith('/'):
        return func.HttpResponse(
            "Invalid pgpKeyPath: path traversal not permitted.",
            status_code=400
        )

    # Sanitize runId for filename use (removes special characters)
    run_id = re.sub(r'[^A-Za-z0-9]', '', body.get("runId", "").strip())[:20]

    credential = _get_credential()
    fs_client = None

    # -----------------------------------------------------------------------
    # Phase 1: Setup - Connect to ADLS and locate source file
    # -----------------------------------------------------------------------
    try:
        account_url = f"https://{storage_account_name}.dfs.core.windows.net"
        fs_client = DataLakeServiceClient(
            account_url=account_url, 
            credential=credential
        ).get_file_system_client(file_system=container)

        logging.info(f"Reading PGP public key from ADLS: {pgp_key_path}...")
        pgp_public_key_text = fs_client.get_file_client(pgp_key_path) \
            .download_file().readall().decode("utf-8")
        logging.info("PGP public key loaded from ADLS.")

        logging.info(f"Searching for data file in {temp_folder}...")
        part_file_path = None
        all_temp_paths = []
        
        for path_item in fs_client.get_paths(path=temp_folder):
            name = path_item.name.split("/")[-1]
            if path_item.is_directory:
                continue
            all_temp_paths.append(path_item.name)
            # Find first file not starting with underscore (not a system file)
            if not name.startswith("_"):
                part_file_path = path_item.name

        if not part_file_path:
            logging.info("No data file found in temp folder — nothing to process.")
            return func.HttpResponse(
                json.dumps({
                    "status": "NO_FILE",
                    "message": "No staging file found in temp folder.",
                    "outputFileName": None,
                    "tempFolder": temp_folder
                }),
                status_code=404,
                mimetype="application/json"
            )

    except Exception as e:
        logging.error(f"Setup/ADLS connectivity failed: {e}")
        return func.HttpResponse(
            json.dumps({
                "status": "SETUP_FAILED",
                "message": f"Could not connect to ADLS or locate required files: {str(e)[:400]}",
                "outputFileName": None,
                "tempFolder": temp_folder
            }),
            status_code=500,
            mimetype="application/json"
        )

    # -----------------------------------------------------------------------
    # Phase 2: Encrypt - Read, validate, and encrypt
    # -----------------------------------------------------------------------
    file_content = None
    encrypted_bytes = None
    try:
        logging.info(f"Found file: {part_file_path}")
        file_content = fs_client.get_file_client(part_file_path).download_file().readall()
        logging.info(f"Read {len(file_content)} bytes from {part_file_path}")

        if len(file_content) == 0:
            return func.HttpResponse(
                json.dumps({
                    "status": "EMPTY_FILE",
                    "message": "Staging file is empty — no payload to encrypt.",
                    "outputFileName": None,
                    "tempFolder": temp_folder
                }),
                status_code=422,
                mimetype="application/json"
            )

        logging.info("Encrypting file with PGP...")
        gpg = _find_gpg()

        with tempfile.TemporaryDirectory(prefix='pgp_enc_') as tmpdir:
            gpg_home = os.path.join(tmpdir, 'gnupg')
            os.makedirs(gpg_home, mode=0o700)
            base_args = [gpg, '--homedir', gpg_home, '--batch', '--yes', '--no-tty']

            # Import public key
            import_result = subprocess.run(
                base_args + ['--import'],
                input=pgp_public_key_text.encode('utf-8'),
                capture_output=True,
            )
            if import_result.returncode != 0:
                raise RuntimeError(
                    f'gpg --import failed: {import_result.stderr.decode(errors="replace")}'
                )
            logging.info("Public key imported into temporary keyring")

            # Extract key fingerprint for encryption
            list_result = subprocess.run(
                base_args + ['--list-keys', '--with-colons'],
                capture_output=True,
            )
            fingerprint = None
            for line in list_result.stdout.decode(errors='replace').split('\n'):
                if line.startswith('fpr:'):
                    parts = line.split(':')
                    if len(parts) > 9 and parts[9]:
                        fingerprint = parts[9]
                        break
            if not fingerprint:
                raise RuntimeError('Could not extract key fingerprint from public key')

            # Set key trust
            trust_input = f"{fingerprint}:6:\n"
            subprocess.run(
                base_args + ['--import-ownertrust'],
                input=trust_input.encode('utf-8'),
                capture_output=True,
            )
            logging.info("Key trust configured; proceeding with encryption.")

            # Encrypt file
            plain_path = os.path.join(tmpdir, 'input.bin')
            enc_path = os.path.join(tmpdir, 'output.pgp')
            
            with open(plain_path, 'wb') as f:
                f.write(file_content)

            encrypt_result = subprocess.run(
                base_args + [
                    '--recipient', fingerprint,
                    '--output', enc_path,
                    '--encrypt', plain_path,
                ],
                capture_output=True,
            )
            if encrypt_result.returncode != 0:
                raise RuntimeError(
                    f'gpg --encrypt failed: {encrypt_result.stderr.decode(errors="replace")}'
                )

            with open(enc_path, 'rb') as f:
                encrypted_bytes = f.read()

        logging.info(f"Encrypted size: {len(encrypted_bytes)} bytes")

    except Exception as e:
        error_message = str(e)
        logging.error(f"Encryption failed: {error_message}")
        _write_error_log(fs_client, error_folder, storage_account_name, 
                        container, temp_folder, error_message)
        return func.HttpResponse(
            json.dumps({
                "status": "ENCRYPTION_FAILED",
                "message": "Encryption process failed. Check error log for details.",
                "outputFileName": None,
                "tempFolder": temp_folder
            }),
            status_code=500,
            mimetype="application/json"
        )

    # -----------------------------------------------------------------------
    # Phase 3: Persist - Write output, archive, and cleanup
    # -----------------------------------------------------------------------
    try:
        # Generate output filenames with timestamp
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        if run_id:
            logging.info(f"Pipeline RunId (for reference): {run_id}")
            
        renamed_file_name = f"{file_prefix}{timestamp}.csv"
        output_file_name  = f"{file_prefix}{timestamp}.csv.pgp"
        output_path = f"{output_folder}/{output_file_name}"

        # Clear output folder before writing (full load pattern)
        logging.info(f"Clearing output folder: {output_folder}...")
        try:
            for existing in fs_client.get_paths(path=output_folder, recursive=False):
                if not existing.is_directory:
                    fs_client.get_file_client(existing.name).delete_file()
                    logging.info(f"Deleted existing output file: {existing.name}")
        except Exception as del_err:
            logging.warning(f"Could not clear output folder (may be empty): {del_err}")

        # Write encrypted file to output folder
        logging.info(f"Writing encrypted file to {output_path}...")
        fs_client.get_file_client(output_path).upload_data(
            encrypted_bytes, overwrite=True
        )

        # Archive plain file with date partitioning (YYYY/MM/DD/)
        now = datetime.now(timezone.utc)
        archive_base = (
            f"{archive_folder}/"
            f"{now.strftime('%Y')}/"
            f"{now.strftime('%m')}/"
            f"{now.strftime('%d')}"
        )
        archive_path = f"{archive_base}/{renamed_file_name}"
        fs_client.get_file_client(archive_path).upload_data(
            file_content, overwrite=True
        )
        logging.info(f"Archived plain file: {archive_path}")

        # Clean up staging files
        for temp_path in all_temp_paths:
            try:
                fs_client.get_file_client(temp_path).delete_file()
                logging.info(f"Deleted from staging: {temp_path}")
            except Exception as cleanup_err:
                logging.warning(f"Could not delete {temp_path}: {cleanup_err}")

        result = {
            "status": "SUCCESS",
            "message": "File encrypted and archived successfully.",
            "outputFileName": output_file_name,
            "outputPath": output_path,
            "archivedFileName": renamed_file_name,
            "archivedPath": archive_path,
            "originalSize": len(file_content),
            "encryptedSize": len(encrypted_bytes)
        }
        logging.info(f"EncryptAndRename completed: {output_file_name}")
        return func.HttpResponse(
            json.dumps(result),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        error_message = str(e)
        logging.error(f"Post-encryption persist failed: {error_message}")
        _write_error_log(fs_client, error_folder, storage_account_name,
                        container, temp_folder, error_message)
        return func.HttpResponse(
            json.dumps({
                "status": "POST_ENCRYPT_FAILED",
                "message": (
                    "Encryption succeeded but writing output or archiving failed. "
                    "Verify if encrypted file already exists before retrying."
                ),
                "outputFileName": None,
                "tempFolder": temp_folder
            }),
            status_code=500,
            mimetype="application/json"
        )
