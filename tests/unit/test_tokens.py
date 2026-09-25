"""Tests for token encryption utilities."""

import os

import pytest

from src.storage.tokens import TokenEncryption, TokenEncryptionError, generate_key


class TestTokenEncryption:
    """Tests for TokenEncryption class."""

    def test_generate_key(self):
        """Test that key generation produces valid base64 key."""
        key = generate_key()
        assert isinstance(key, str)
        assert len(key) > 0
        # Should be valid URL-safe base64
        import base64
        decoded = base64.urlsafe_b64decode(key)
        assert len(decoded) == 32  # Fernet key is 32 bytes

    def test_encrypt_decrypt_roundtrip(self):
        """Test encrypt/decrypt roundtrip."""
        key = generate_key()
        encryption = TokenEncryption(key.encode())

        plaintext = "test_access_token_12345"
        encrypted = encryption.encrypt(plaintext)
        decrypted = encryption.decrypt(encrypted)

        assert decrypted == plaintext
        assert encrypted != plaintext.encode()
        assert isinstance(encrypted, bytes)

    def test_encrypt_decrypt_with_special_chars(self):
        """Test encryption with special characters in token."""
        key = generate_key()
        encryption = TokenEncryption(key.encode())

        # Tokens can have various characters
        plaintext = "ya29.a0AfH6SMC_xyz123.ABC-DEF_GHI.jkl_mno"
        encrypted = encryption.encrypt(plaintext)
        decrypted = encryption.decrypt(encrypted)

        assert decrypted == plaintext

    def test_encrypt_empty_string_raises(self):
        """Test that encrypting empty string raises error."""
        key = generate_key()
        encryption = TokenEncryption(key.encode())

        with pytest.raises(TokenEncryptionError):
            encryption.encrypt("")

    def test_decrypt_empty_bytes_raises(self):
        """Test that decrypting empty bytes raises error."""
        key = generate_key()
        encryption = TokenEncryption(key.encode())

        with pytest.raises(TokenEncryptionError):
            encryption.decrypt(b"")

    def test_decrypt_with_wrong_key_raises(self):
        """Test that decrypting with wrong key raises error."""
        key1 = generate_key()
        key2 = generate_key()

        encryption1 = TokenEncryption(key1.encode())
        encryption2 = TokenEncryption(key2.encode())

        plaintext = "test_token"
        encrypted = encryption1.encrypt(plaintext)

        with pytest.raises(TokenEncryptionError):
            encryption2.decrypt(encrypted)

    def test_decrypt_corrupted_data_raises(self):
        """Test that decrypting corrupted data raises error."""
        key = generate_key()
        encryption = TokenEncryption(key.encode())

        plaintext = "test_token"
        encrypted = encryption.encrypt(plaintext)

        # Corrupt the data
        corrupted = encrypted[:-1] + b"X"

        with pytest.raises(TokenEncryptionError):
            encryption.decrypt(corrupted)

    def test_encrypt_optional_none(self):
        """Test encrypt_optional with None returns None."""
        key = generate_key()
        encryption = TokenEncryption(key.encode())

        assert encryption.encrypt_optional(None) is None
        assert encryption.encrypt_optional("") is None

    def test_encrypt_optional_string(self):
        """Test encrypt_optional with string returns encrypted bytes."""
        key = generate_key()
        encryption = TokenEncryption(key.encode())

        result = encryption.encrypt_optional("test_token")
        assert result is not None
        assert isinstance(result, bytes)

    def test_decrypt_optional_none(self):
        """Test decrypt_optional with None returns None."""
        key = generate_key()
        encryption = TokenEncryption(key.encode())

        assert encryption.decrypt_optional(None) is None

    def test_decrypt_optional_bytes(self):
        """Test decrypt_optional with bytes returns string."""
        key = generate_key()
        encryption = TokenEncryption(key.encode())

        encrypted = encryption.encrypt("test_token")
        result = encryption.decrypt_optional(encrypted)

        assert result == "test_token"

    def test_init_without_env_key_raises(self):
        """Test that TokenEncryption without key raises error."""
        # Temporarily remove ENCRYPTION_KEY
        old_key = os.environ.pop("ENCRYPTION_KEY", None)
        try:
            with pytest.raises(TokenEncryptionError):
                TokenEncryption()
        finally:
            if old_key:
                os.environ["ENCRYPTION_KEY"] = old_key

    def test_init_with_env_key(self):
        """Test TokenEncryption reads key from environment."""
        key = generate_key()
        os.environ["ENCRYPTION_KEY"] = key
        try:
            encryption = TokenEncryption()
            assert encryption is not None
        finally:
            os.environ.pop("ENCRYPTION_KEY", None)