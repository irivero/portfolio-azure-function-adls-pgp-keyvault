"""
Azure Function: Batch PGP Decryption with Azure Key Vault Integration

This function scans an ADLS folder for encrypted .pgp files, decrypts them using
credentials stored in Azure Key Vault, and routes files based on processing results:
  - Success: Decrypted file → destination_folder, Original → archive_folder
  - Failure: Original → error_folder

Endpoint: POST /api/decrypt-move-file-kv
"""
import json
import logging
import os
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import azure.functions as func

from .helpers import (
    _get_adls_filesystem,
    _get_pgp_secrets,
    _pgp_decrypt,
    _write_file,
    _route_source_file,
    _move_file,
    _MAX_FILE_BYTES,
)

bp = func.Blueprint()


@bp.function_name(name="DecryptAndMoveFileWithKeyVault")
@bp.route(route="decrypt-move-file-kv", auth_level=func.AuthLevel.FUNCTION)
def decrypt_and_move_file_with_keyvault(req: func.HttpRequest) -> func.HttpResponse:
    """
    Batch decrypt PGP files from ADLS using Azure Key Vault credentials.
    
    Request Body (JSON):
        file_system_name (str): ADLS container name
        source_folder (str): Folder to scan for .pgp files
        destination_folder (str): Output folder for decrypted files
        error_folder (str): Folder for files that failed decryption
        archive_folder (str): Folder for successfully processed originals
        
    Environment Variables:
        KEY_VAULT_NAME (str): Azure Key Vault name
        PGP_KEY_SECRET_NAME (str): Secret name for PGP private key (default: pgp-private-key)
        PGP_PASS_SECRET_NAME (str): Secret name for passphrase (default: pgp-passphrase)
        DECRYPT_WORKERS (int): Concurrent workers (default: 4)
    
    Returns:
        JSON response with processing summary:
        {
            "total": int,
            "ok": int,
            "failed": int,
            "skipped": int,
            "processed_files": [str],
            "skipped_files": [str],
            "error_files": [str]
        }
    
    Status Codes:
        200: All files processed successfully
        207: Partial success (some files failed)
        400: Invalid request parameters
        500: Processing error or all files failed
    """
    logging.info('Starting PGP batch decrypt-and-move process with Key Vault')

    try:
        try:
            req_body = req.get_json()
        except ValueError:
            req_body = {}
        except Exception as parse_err:
            logging.warning(f"Could not parse request body: {parse_err}")
            req_body = {}

        def param(name):
            """Extract parameter from body or query string."""
            v = req_body.get(name) if req_body else None
            if v is not None and str(v).strip():
                return str(v).strip()
            v = req.params.get(name)
            return v.strip() if v else None

        # Extract request parameters
        file_system_name   = param('file_system_name')
        source_folder      = param('source_folder')
        destination_folder = param('destination_folder')
        error_folder       = param('error_folder')
        archive_folder     = param('archive_folder')
        
        # Key Vault configuration from environment
        key_vault_name     = os.environ.get('KEY_VAULT_NAME')
        pgp_key_secret     = os.environ.get('PGP_KEY_SECRET_NAME',  'pgp-private-key')
        pgp_pass_secret    = os.environ.get('PGP_PASS_SECRET_NAME', 'pgp-passphrase')

        # Validate required parameters
        missing = [n for n, v in {
            'file_system_name':   file_system_name,
            'source_folder':      source_folder,
            'destination_folder': destination_folder,
            'error_folder':       error_folder,
            'archive_folder':     archive_folder,
        }.items() if not v]

        if missing:
            return func.HttpResponse(
                f"Missing required parameters: {', '.join(missing)}",
                status_code=400
            )
        if not key_vault_name:
            return func.HttpResponse(
                "KEY_VAULT_NAME not configured. Set KEY_VAULT_NAME environment variable.",
                status_code=400
            )

        # ── Security: Path Traversal Validation ──────────────────────────────
        def _is_safe_path(value: str) -> bool:
            """
            Validate path to prevent directory traversal attacks.
            Checks for:
            - Percent-encoded traversal sequences (%2e%2e, %252e)
            - Unicode normalization attacks (fullwidth dots)
            - Mixed encoding attacks (..%2f, ..%5c)
            """
            # Decode percent-encoding
            decoded = urllib.parse.unquote(value)
            # Normalize unicode characters
            decoded = unicodedata.normalize('NFC', decoded)
            # Check for .. segments
            if '..' in decoded.replace('\\', '/').split('/'):
                return False
            # Check for encoded traversal patterns
            raw_lower = value.lower()
            for pattern in ['../', '.\\\\', '%2e%2e', '%252e', '..%2f', '..%5c']:
                if pattern in raw_lower:
                    return False
            return True

        for _pname, _pval in [
            ('source_folder', source_folder),
            ('destination_folder', destination_folder),
            ('error_folder', error_folder),
            ('archive_folder', archive_folder)
        ]:
            if not _is_safe_path(_pval):
                return func.HttpResponse(
                    f"Invalid path (path traversal not allowed): {_pname}",
                    status_code=400
                )

        # ── 1. Retrieve PGP Secrets from Key Vault ───────────────────────────
        try:
            pgp_key_armored, pgp_passphrase = _get_pgp_secrets(
                key_vault_name, pgp_key_secret, pgp_pass_secret
            )
        except Exception as kv_err:
            logging.error(f"Key Vault error: {kv_err}")
            return func.HttpResponse(
                "Failed to retrieve PGP credentials from Key Vault. Check permissions and secret names.",
                status_code=500
            )

        # ── 2. Connect to ADLS ───────────────────────────────────────────────
        try:
            file_system_client = _get_adls_filesystem(file_system_name)
        except EnvironmentError as env_err:
            logging.error(f"ADLS connection error: {env_err}")
            return func.HttpResponse(
                "Storage connection error. Set ADLS_ACCOUNT_NAME or ADLS_CONNECTION_STRING.",
                status_code=500
            )

        # ── 3. List Files in Source Folder ───────────────────────────────────
        paths = list(file_system_client.get_paths(path=source_folder, recursive=False))
        files = [p for p in paths if not p.is_directory]

        if not files:
            return func.HttpResponse(
                json.dumps({
                    "total": 0,
                    "ok": 0,
                    "failed": 0,
                    "skipped": 0,
                    "processed_files": [],
                    "skipped_files": [],
                    "error_files": [],
                    "message": f"No files found in source folder: {source_folder}"
                }),
                status_code=200,
                mimetype="application/json"
            )

        logging.info(f"Found {len(files)} file(s) in {source_folder}")

        # Batch size limit to prevent resource exhaustion
        _MAX_FILES = 500
        if len(files) > _MAX_FILES:
            return func.HttpResponse(
                f"Too many files in source folder ({len(files)}). Maximum allowed: {_MAX_FILES}",
                status_code=400
            )

        ok_files, err_files, skipped_files = [], [], []

        def _process_one(path_item):
            """
            Process a single encrypted file.
            
            Returns:
                Tuple of (status, filename, message)
                status: 'ok', 'error', or 'skipped'
            """
            file_path = path_item.name
            filename  = file_path.split('/')[-1]

            # Skip non-PGP files
            if not filename.lower().endswith('.pgp'):
                logging.warning(f"Skipping non-PGP file: {filename}")
                try:
                    _move_file(file_system_client, file_path, f"{error_folder}/{filename}")
                except Exception as skip_move_err:
                    logging.error(f"Could not move non-PGP file {filename}: {skip_move_err}")
                return 'skipped', filename, "file does not have .pgp extension"

            # Generate target paths
            dest_name = filename[:-4]  # Remove .pgp extension
            dest_path = f"{destination_folder}/{dest_name}"
            err_path  = f"{error_folder}/{filename}"
            arch_path = f"{archive_folder}/{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}_{filename}"

            try:
                t_start = time.perf_counter()
                logging.info(f"Processing: {filename}")

                # Validate file size
                file_size = getattr(path_item, 'content_length', None)
                if file_size is not None and file_size > _MAX_FILE_BYTES:
                    raise ValueError(
                        f"File size {file_size:,} bytes exceeds limit of {_MAX_FILE_BYTES:,} bytes"
                    )

                # Download and decrypt
                encrypted_data = file_system_client.get_file_client(file_path).download_file().readall()
                if len(encrypted_data) > _MAX_FILE_BYTES:
                    raise ValueError(
                        f"Downloaded file size {len(encrypted_data):,} bytes exceeds limit"
                    )
                
                decrypted_data = _pgp_decrypt(pgp_key_armored, pgp_passphrase, encrypted_data)
                del encrypted_data  # Free memory
                
                # Write decrypted file
                _write_file(file_system_client, dest_path, decrypted_data)

                # Archive original file
                try:
                    _route_source_file(file_system_client, file_path, arch_path, success=True)
                except Exception as arch_err:
                    logging.error(f"Archive failed for {filename}: {arch_err}")
                    # Try to clean up source file anyway
                    try:
                        file_system_client.get_file_client(file_path).delete_file()
                        logging.info(f"File removed from source: {file_path}")
                    except Exception as rb_err:
                        logging.error(f"Could not remove {file_path}: {rb_err}")
                    raise RuntimeError(f"Archive step failed: {arch_err}") from arch_err

                elapsed = time.perf_counter() - t_start
                logging.info(f"SUCCESS: {filename} -> {dest_path} ({elapsed:.2f}s)")
                return 'ok', filename, ""

            except Exception as e:
                logging.error(f"FAILED {filename}: {e}")
                short_msg = str(e).splitlines()[0][:200]
                
                # Move to error folder
                try:
                    _route_source_file(file_system_client, file_path, err_path, success=False)
                    return 'error', filename, short_msg
                except Exception as move_err:
                    logging.error(f"Could not move {filename} to error folder: {move_err}")
                    return 'error', filename, (
                        f"{short_msg} | WARNING: file stuck in source_folder"
                    )

        # ── 4. Process Files Concurrently ────────────────────────────────────
        max_workers = int(os.environ.get('DECRYPT_WORKERS', '4'))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_process_one, p): p for p in files}
            for future in as_completed(futures):
                status, filename, message = future.result()
                if status == 'ok':
                    ok_files.append(filename)
                elif status == 'skipped':
                    skipped_files.append(filename)
                else:
                    err_files.append(f"{filename}: {message}" if message else filename)

        # ── 5. Return Summary ────────────────────────────────────────────────
        result = {
            "total":           len(files),
            "ok":              len(ok_files),
            "failed":          len(err_files),
            "skipped":         len(skipped_files),
            "processed_files": ok_files,
            "skipped_files":   skipped_files,
            "error_files":     err_files,
        }
        
        # Determine status code
        if not err_files:
            status_code = 200  # All successful
        elif ok_files:
            status_code = 207  # Partial success
        else:
            status_code = 500  # All failed
            
        return func.HttpResponse(
            json.dumps(result),
            status_code=status_code,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Unexpected error: {e}", exc_info=True)
        return func.HttpResponse(
            "Unexpected error occurred. Check function logs for details.",
            status_code=500
        )
