"""
Security tests for path traversal prevention and credential handling.

Tests validate that the application properly protects against:
- Directory traversal attacks
- Credential exposure in logs
- GPG keyring contamination
- Resource exhaustion
"""
import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from blueprints.decrypt_kv import decrypt_and_move_file_with_keyvault


class TestPathTraversalPrevention:
    """Test suite for path traversal attack prevention."""
    
    @pytest.mark.parametrize("malicious_path", [
        "../../../etc/passwd",
        "../../private/secrets",
        "folder/../../../win.ini",
        "..\\..\\..\\windows\\system32",
    ])
    def test_path_traversal_double_dot(self, mock_env_vars, malicious_path):
        """
        TC-SEC-001: Prevent directory traversal via .. segments.
        
        Expected: 400 Bad Request for all traversal attempts.
        """
        # Arrange
        mock_req = MagicMock()
        mock_req.get_json.return_value = {
            "file_system_name": "test-container",
            "source_folder": malicious_path,
            "destination_folder": "valid/path",
            "error_folder": "errors",
            "archive_folder": "archive"
        }
        mock_req.params = {}
        
        # Act
        response = decrypt_and_move_file_with_keyvault(mock_req)
        
        # Assert
        assert response.status_code == 400
        assert "path traversal" in response.get_body().decode().lower()
    
    @pytest.mark.parametrize("encoded_path", [
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",  # ..%2F..%2Fetc%2Fpasswd
        "%2e%2e/sensitive",                 # ..%2Fsensitive
        "..%2fprivate",                     # ..%2Fprivate
        "..%5c..%5cwindows",                # ..%5C..%5Cwindows
        "%252e%252e%252f",                  # Double-encoded ../
    ])
    def test_path_traversal_url_encoding(self, mock_env_vars, encoded_path):
        """
        TC-SEC-002: Prevent URL-encoded traversal attempts.
        
        Expected: All encoding variants detected and blocked.
        """
        # Arrange
        mock_req = MagicMock()
        mock_req.get_json.return_value = {
            "file_system_name": "test-container",
            "source_folder": "valid",
            "destination_folder": encoded_path,
            "error_folder": "errors",
            "archive_folder": "archive"
        }
        mock_req.params = {}
        
        # Act
        response = decrypt_and_move_file_with_keyvault(mock_req)
        
        # Assert
        assert response.status_code == 400
    
    def test_path_traversal_mixed_separators(self, mock_env_vars):
        """
        Test mixed forward/back slashes don't bypass protection.
        
        Expected: Normalized and detected as traversal attempt.
        """
        # Arrange
        mock_req = MagicMock()
        mock_req.get_json.return_value = {
            "file_system_name": "test-container",
            "source_folder": "folder/..\\..\\system",
            "destination_folder": "valid",
            "error_folder": "errors",
            "archive_folder": "archive"
        }
        mock_req.params = {}
        
        # Act
        response = decrypt_and_move_file_with_keyvault(mock_req)
        
        # Assert
        assert response.status_code == 400
    
    def test_valid_paths_allowed(self, mock_http_request, mock_adls_filesystem, mock_env_vars):
        """
        Verify legitimate paths are not blocked by traversal protection.
        
        Expected: Valid paths process normally.
        """
        # Arrange
        valid_request = {
            "file_system_name": "container",
            "source_folder": "inbound/data/2026/04",
            "destination_folder": "processed/decrypted",
            "error_folder": "errors/failed",
            "archive_folder": "archive/originals"
        }
        mock_http_request.get_json.return_value = valid_request
        mock_adls_filesystem.get_paths.return_value = []
        
        with patch('blueprints.decrypt_kv._get_adls_filesystem') as mock_get_adls:
            with patch('blueprints.decrypt_kv._get_pgp_secrets') as mock_get_secrets:
                mock_get_adls.return_value = mock_adls_filesystem
                mock_get_secrets.return_value = ("key", "pass")
                
                # Act
                response = decrypt_and_move_file_with_keyvault(mock_http_request)
                
                # Assert
                assert response.status_code == 200  # Not blocked


class TestCredentialSecurity:
    """Tests for secure credential handling."""
    
    def test_secrets_not_logged(self, mock_http_request, mock_adls_filesystem, 
                                 mock_env_vars, caplog):
        """
        TC-SEC-004: Verify secrets never appear in logs.
        
        Expected: Passphrase and private key content not in log output.
        """
        import logging
        caplog.set_level(logging.DEBUG)
        
        # Arrange
        test_passphrase = "SuperSecretPassphrase123!@#"
        test_key = "-----BEGIN PGP PRIVATE KEY BLOCK-----\nSECRET_KEY_CONTENT\n-----END-----"
        
        mock_adls_filesystem.get_paths.return_value = []
        
        with patch('blueprints.decrypt_kv._get_adls_filesystem') as mock_get_adls:
            with patch('blueprints.decrypt_kv._get_pgp_secrets') as mock_get_secrets:
                mock_get_adls.return_value = mock_adls_filesystem
                mock_get_secrets.return_value = (test_key, test_passphrase)
                
                # Act
                decrypt_and_move_file_with_keyvault(mock_http_request)
                
                # Assert - Check all log messages
                log_text = '\n'.join([rec.message for rec in caplog.records])
                assert test_passphrase not in log_text
                assert "SECRET_KEY_CONTENT" not in log_text
                assert "SuperSecretPassphrase" not in log_text
    
    def test_secrets_not_in_error_messages(self, mock_http_request, mock_env_vars):
        """
        Verify secrets don't leak in error responses.
        
        Expected: Error messages sanitized, no secret content exposed.
        """
        # Arrange
        test_passphrase = "P@ssw0rd!Secret"
        
        with patch('blueprints.decrypt_kv._get_pgp_secrets') as mock_get_secrets:
            mock_get_secrets.side_effect = Exception(f"Failed with passphrase: {test_passphrase}")
            
            # Act
            response = decrypt_and_move_file_with_keyvault(mock_http_request)
            
            # Assert
            response_body = response.get_body().decode()
            assert test_passphrase not in response_body
            assert "Key Vault" in response_body  # Generic error message


class TestGPGIsolation:
    """Tests for GPG keyring isolation."""
    
    def test_temporary_gpg_home_created(self, mock_gpg_subprocess):
        """
        TC-SEC-005: Verify operations use isolated temporary keyring.
        
        Expected: --homedir parameter points to temp directory.
        """
        from blueprints.helpers import _pgp_decrypt
        
        # Arrange
        test_key = "-----BEGIN PGP PRIVATE KEY BLOCK-----\ntest\n-----END-----"
        test_pass = "test-pass"
        test_data = b"encrypted content"
        
        # Act
        with patch('builtins.open', create=True):
            with patch('os.makedirs'):
                try:
                    _pgp_decrypt(test_key, test_pass, test_data)
                except:
                    pass  # We're just checking subprocess calls
        
        # Assert
        # Verify subprocess.run was called with --homedir containing temp path
        calls = mock_gpg_subprocess.call_args_list
        homedir_calls = [c for c in calls if '--homedir' in str(c)]
        assert len(homedir_calls) > 0
        
        # Verify temp directory pattern
        for call in homedir_calls:
            args = call[0][0] if call[0] else call[1].get('args', [])
            if '--homedir' in args:
                homedir_index = args.index('--homedir')
                homedir_path = args[homedir_index + 1]
                assert 'tmp' in homedir_path.lower() or 'temp' in homedir_path.lower()
    
    def test_gpg_keyring_cleanup(self):
        """
        Verify temporary GPG directories are cleaned up after use.
        
        Expected: No temp directories persist after operation.
        """
        import tempfile
        import os
        from blueprints.helpers import _pgp_decrypt
        
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
            
            # Track temp directories created
            temp_dirs_created = []
            original_tempdir = tempfile.TemporaryDirectory
            
            def track_tempdir(*args, **kwargs):
                temp_obj = original_tempdir(*args, **kwargs)
                temp_dirs_created.append(temp_obj.name)
                return temp_obj
            
            with patch('tempfile.TemporaryDirectory', side_effect=track_tempdir):
                with patch('builtins.open', create=True):
                    try:
                        test_key = "-----BEGIN PGP PRIVATE KEY BLOCK-----\ntest\n-----END-----"
                        _pgp_decrypt(test_key, "pass", b"data")
                    except:
                        pass
            
            # Verify temp directories were created then cleaned up
            for temp_dir in temp_dirs_created:
                assert not os.path.exists(temp_dir), f"Temp dir not cleaned: {temp_dir}"


class TestResourceLimits:
    """Tests for resource exhaustion protection."""
    
    def test_file_size_limit_prevents_memory_exhaustion(
        self, mock_http_request, mock_adls_filesystem, mock_env_vars
    ):
        """
        TC-DEC-007: Verify 500 MB file size limit prevents memory issues.
        
        Expected: Large files rejected before loading into memory.
        """
        # Arrange - 600 MB file
        mock_file = MagicMock()
        mock_file.name = "huge.pgp"
        mock_file.is_directory = False
        mock_file.content_length = 600 * 1024 * 1024
        
        mock_adls_filesystem.get_paths.return_value = [mock_file]
        
        with patch('blueprints.decrypt_kv._get_adls_filesystem') as mock_get_adls:
            with patch('blueprints.decrypt_kv._get_pgp_secrets') as mock_get_secrets:
                mock_get_adls.return_value = mock_adls_filesystem
                mock_get_secrets.return_value = ("key", "pass")
                
                # Act
                response = decrypt_and_move_file_with_keyvault(mock_http_request)
                
                # Assert
                result = json.loads(response.get_body())
                assert result["failed"] > 0
                assert any("exceeds limit" in str(err).lower() for err in result["error_files"])
    
    def test_batch_limit_prevents_resource_exhaustion(
        self, mock_http_request, mock_adls_filesystem, mock_env_vars
    ):
        """
        Verify 500-file batch limit prevents resource exhaustion.
        
        Expected: Requests with > 500 files rejected immediately.
        """
        # Arrange - 501 files
        mock_files = [MagicMock(name=f"file{i}.pgp", is_directory=False) 
                     for i in range(501)]
        mock_adls_filesystem.get_paths.return_value = mock_files
        
        with patch('blueprints.decrypt_kv._get_adls_filesystem') as mock_get_adls:
            with patch('blueprints.decrypt_kv._get_pgp_secrets') as mock_get_secrets:
                mock_get_adls.return_value = mock_adls_filesystem
                mock_get_secrets.return_value = ("key", "pass")
                
                # Act
                response = decrypt_and_move_file_with_keyvault(mock_http_request)
                
                # Assert
                assert response.status_code == 400
                assert "Too many files" in response.get_body().decode()


class TestPassphraseHandling:
    """Tests for secure passphrase handling."""
    
    def test_passphrase_passed_via_pipe_fd(self, mock_gpg_subprocess):
        """
        TC-SEC-005: Verify passphrase passed via pipe FD, not command line.
        
        Expected: --passphrase-fd used, passphrase not in subprocess args.
        """
        from blueprints.helpers import _pgp_decrypt
        
        # Arrange
        test_passphrase = "SecretPassphrase!"
        test_key = "-----BEGIN PGP PRIVATE KEY BLOCK-----\ntest\n-----END-----"
        
        # Act
        with patch('builtins.open', create=True):
            with patch('os.pipe') as mock_pipe:
                with patch('os.write'):
                    with patch('os.close'):
                        mock_pipe.return_value = (3, 4)  # Mock pipe FDs
                        try:
                            _pgp_decrypt(test_key, test_passphrase, b"encrypted")
                        except:
                            pass
        
        # Assert
        # Verify --passphrase-fd used (not --passphrase with value)
        calls = mock_gpg_subprocess.call_args_list
        decrypt_calls = [c for c in calls if '--decrypt' in str(c)]
        
        for call in decrypt_calls:
            args = call[0][0] if call[0] else call[1].get('args', [])
            # Should have --passphrase-fd
            assert '--passphrase-fd' in args
            # Should NOT have --passphrase <value>
            assert test_passphrase not in args


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
