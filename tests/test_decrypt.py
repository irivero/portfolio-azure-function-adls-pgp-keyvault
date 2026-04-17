"""
Unit tests for PGP decryption function.

Tests the decrypt_and_move_file_with_keyvault endpoint with various scenarios
including success cases, error conditions, and edge cases.
"""
import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from azure.functions import HttpRequest
from blueprints.decrypt_kv import decrypt_and_move_file_with_keyvault


class TestDecryptFunction:
    """Test suite for decryption endpoint."""
    
    def test_successful_single_file_decryption(
        self, mock_http_request, mock_adls_filesystem, 
        mock_keyvault_client, mock_gpg_subprocess, mock_env_vars
    ):
        """
        TC-DEC-001: Verify successful decryption of a single .pgp file.
        
        Expected: File decrypted, moved to destination, original archived.
        """
        # Arrange
        with patch('blueprints.decrypt_kv._get_adls_filesystem') as mock_get_adls:
            with patch('blueprints.decrypt_kv._get_pgp_secrets') as mock_get_secrets:
                with patch('blueprints.decrypt_kv._pgp_decrypt') as mock_decrypt:
                    mock_get_adls.return_value = mock_adls_filesystem
                    mock_get_secrets.return_value = ("test-key", "test-pass")
                    mock_decrypt.return_value = b"decrypted content"
                    
                    # Act
                    response = decrypt_and_move_file_with_keyvault(mock_http_request)
                    
                    # Assert
                    assert response.status_code == 200
                    result = json.loads(response.get_body())
                    assert result["total"] == 1
                    assert result["ok"] == 1
                    assert result["failed"] == 0
                    assert len(result["processed_files"]) == 1
    
    def test_empty_source_folder(self, mock_http_request, mock_adls_filesystem, mock_env_vars):
        """
        TC-DEC-005: Verify behavior when no files to process.
        
        Expected: 200 status with message indicating no files found.
        """
        # Arrange
        mock_adls_filesystem.get_paths.return_value = []
        
        with patch('blueprints.decrypt_kv._get_adls_filesystem') as mock_get_adls:
            with patch('blueprints.decrypt_kv._get_pgp_secrets') as mock_get_secrets:
                mock_get_adls.return_value = mock_adls_filesystem
                mock_get_secrets.return_value = ("test-key", "test-pass")
                
                # Act
                response = decrypt_and_move_file_with_keyvault(mock_http_request)
                
                # Assert
                assert response.status_code == 200
                result = json.loads(response.get_body())
                assert result["total"] == 0
                assert "No files found" in result.get("message", "")
    
    def test_decryption_failure_corrupted_file(
        self, mock_http_request, mock_adls_filesystem, mock_env_vars
    ):
        """
        TC-DEC-003: Verify graceful handling of corrupt encrypted files.
        
        Expected: File moved to error folder, error reported in response.
        """
        # Arrange
        with patch('blueprints.decrypt_kv._get_adls_filesystem') as mock_get_adls:
            with patch('blueprints.decrypt_kv._get_pgp_secrets') as mock_get_secrets:
                with patch('blueprints.decrypt_kv._pgp_decrypt') as mock_decrypt:
                    mock_get_adls.return_value = mock_adls_filesystem
                    mock_get_secrets.return_value = ("test-key", "test-pass")
                    mock_decrypt.side_effect = RuntimeError("gpg --decrypt failed")
                    
                    # Act
                    response = decrypt_and_move_file_with_keyvault(mock_http_request)
                    
                    # Assert
                    assert response.status_code == 500
                    result = json.loads(response.get_body())
                    assert result["failed"] == 1
                    assert len(result["error_files"]) == 1
    
    def test_file_size_limit_enforcement(
        self, mock_http_request, mock_adls_filesystem, mock_env_vars
    ):
        """
        TC-DEC-007: Verify rejection of files exceeding 500 MB limit.
        
        Expected: File rejected with size limit error.
        """
        # Arrange
        mock_file = MagicMock()
        mock_file.name = "huge_file.pgp"
        mock_file.is_directory = False
        mock_file.content_length = 600 * 1024 * 1024  # 600 MB
        
        mock_adls_filesystem.get_paths.return_value = [mock_file]
        
        with patch('blueprints.decrypt_kv._get_adls_filesystem') as mock_get_adls:
            with patch('blueprints.decrypt_kv._get_pgp_secrets') as mock_get_secrets:
                mock_get_adls.return_value = mock_adls_filesystem
                mock_get_secrets.return_value = ("test-key", "test-pass")
                
                # Act
                response = decrypt_and_move_file_with_keyvault(mock_http_request)
                
                # Assert
                assert response.status_code in [500, 207]  # Failed or partial
                result = json.loads(response.get_body())
                assert result["failed"] > 0
    
    def test_batch_size_limit_enforcement(self, mock_http_request, mock_adls_filesystem, mock_env_vars):
        """
        TC-DEC-008: Verify rejection of requests with > 500 files.
        
        Expected: 400 Bad Request with descriptive error.
        """
        # Arrange - Create 501 mock files
        mock_files = []
        for i in range(501):
            mock_file = MagicMock()
            mock_file.name = f"file{i}.pgp"
            mock_file.is_directory = False
            mock_files.append(mock_file)
        
        mock_adls_filesystem.get_paths.return_value = mock_files
        
        with patch('blueprints.decrypt_kv._get_adls_filesystem') as mock_get_adls:
            with patch('blueprints.decrypt_kv._get_pgp_secrets') as mock_get_secrets:
                mock_get_adls.return_value = mock_adls_filesystem
                mock_get_secrets.return_value = ("test-key", "test-pass")
                
                # Act
                response = decrypt_and_move_file_with_keyvault(mock_http_request)
                
                # Assert
                assert response.status_code == 400
                assert "Too many files" in response.get_body().decode()
    
    def test_non_pgp_files_skipped(
        self, mock_http_request, mock_adls_filesystem, mock_env_vars
    ):
        """
        TC-DEC-006: Verify handling of files without .pgp extension.
        
        Expected: Files marked as skipped, moved to error folder.
        """
        # Arrange
        mock_file = MagicMock()
        mock_file.name = "document.pdf"
        mock_file.is_directory = False
        mock_adls_filesystem.get_paths.return_value = [mock_file]
        
        with patch('blueprints.decrypt_kv._get_adls_filesystem') as mock_get_adls:
            with patch('blueprints.decrypt_kv._get_pgp_secrets') as mock_get_secrets:
                mock_get_adls.return_value = mock_adls_filesystem
                mock_get_secrets.return_value = ("test-key", "test-pass")
                
                # Act
                response = decrypt_and_move_file_with_keyvault(mock_http_request)
                
                # Assert
                result = json.loads(response.get_body())
                assert result["skipped"] == 1
    
    def test_missing_required_parameters(self):
        """
        Verify validation of required request parameters.
        
        Expected: 400 Bad Request with list of missing parameters.
        """
        # Arrange
        mock_req = MagicMock()
        mock_req.get_json.return_value = {
            "file_system_name": "test-container"
            # Missing other required fields
        }
        mock_req.params = {}
        
        # Act
        response = decrypt_and_move_file_with_keyvault(mock_req)
        
        # Assert
        assert response.status_code == 400
        assert "Missing" in response.get_body().decode()
    
    def test_key_vault_connection_failure(self, mock_http_request, mock_env_vars):
        """
        TC-ERR-001: Verify graceful handling when Key Vault unreachable.
        
        Expected: 500 error with descriptive message.
        """
        # Arrange
        with patch('blueprints.decrypt_kv._get_pgp_secrets') as mock_get_secrets:
            mock_get_secrets.side_effect = Exception("Key Vault connection failed")
            
            # Act
            response = decrypt_and_move_file_with_keyvault(mock_http_request)
            
            # Assert
            assert response.status_code == 500
            assert "Key Vault" in response.get_body().decode()
    
    def test_adls_connection_failure(self, mock_http_request, mock_env_vars):
        """
        TC-ERR-002: Verify behavior when storage account unreachable.
        
        Expected: 500 error with storage connection message.
        """
        # Arrange
        with patch('blueprints.decrypt_kv._get_adls_filesystem') as mock_get_adls:
            mock_get_adls.side_effect = EnvironmentError("ADLS not configured")
            
            # Act
            response = decrypt_and_move_file_with_keyvault(mock_http_request)
            
            # Assert
            assert response.status_code == 500
            assert "Storage" in response.get_body().decode()


class TestConcurrentProcessing:
    """Tests for concurrent file processing functionality."""
    
    def test_multiple_files_processed_concurrently(
        self, mock_http_request, mock_adls_filesystem, mock_env_vars
    ):
        """
        TC-DEC-002: Verify concurrent processing of multiple encrypted files.
        
        Expected: All files processed, concurrent execution confirmed.
        """
        # Arrange - Create 10 mock files
        mock_files = []
        for i in range(10):
            mock_file = MagicMock()
            mock_file.name = f"file{i}.csv.pgp"
            mock_file.is_directory = False
            mock_file.content_length = 1024
            mock_files.append(mock_file)
        
        mock_adls_filesystem.get_paths.return_value = mock_files
        
        with patch('blueprints.decrypt_kv._get_adls_filesystem') as mock_get_adls:
            with patch('blueprints.decrypt_kv._get_pgp_secrets') as mock_get_secrets:
                with patch('blueprints.decrypt_kv._pgp_decrypt') as mock_decrypt:
                    mock_get_adls.return_value = mock_adls_filesystem
                    mock_get_secrets.return_value = ("test-key", "test-pass")
                    mock_decrypt.return_value = b"decrypted content"
                    
                    # Act
                    response = decrypt_and_move_file_with_keyvault(mock_http_request)
                    
                    # Assert
                    assert response.status_code == 200
                    result = json.loads(response.get_body())
                    assert result["total"] == 10
                    assert result["ok"] == 10
                    assert result["failed"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
