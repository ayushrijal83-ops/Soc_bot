"""Token encryption utilities using Fernet (AES-128-GCM)."""

import os

from cryptography.fernet import Fernet, InvalidToken


class TokenEncryptionError(Exception):
    """Raised when token encryption/decryption fails."""


class TokenEncryption:
    """Handles encryption and decryption of OAuth tokens using Fernet."""

    def __init__(self, key: bytes | None = None):
        """
        Initialize with encryption key.

        Args:
            key: 32-byte base64-encoded key. If not provided, reads from ENCRYPTION_KEY env var.
        """
        if key is None:
            key = os.environ.get("ENCRYPTION_KEY", "").encode()
            if not key:
                raise TokenEncryptionError(
                    "ENCRYPTION_KEY environment variable not set. "
                    "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> bytes:
        """
        Encrypt a plaintext string.

        Args:
            plaintext: The string to encrypt.

        Returns:
            Encrypted bytes.
        """
        if not plaintext:
            raise TokenEncryptionError("Cannot encrypt empty string")
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        """
        Decrypt ciphertext bytes to plaintext string.

        Args:
            ciphertext: The encrypted bytes.

        Returns:
            Decrypted plaintext string.

        Raises:
            TokenEncryptionError: If decryption fails (invalid key, corrupted data, etc.).
        """
        if not ciphertext:
            raise TokenEncryptionError("Cannot decrypt empty bytes")
        try:
            return self._fernet.decrypt(ciphertext).decode()
        except InvalidToken as e:
            raise TokenEncryptionError("Decryption failed: invalid token or wrong key") from e

    def encrypt_optional(self, plaintext: str | None) -> bytes | None:
        """Encrypt a string, returning None if input is None or empty."""
        if plaintext is None or plaintext == "":
            return None
        return self.encrypt(plaintext)

    def decrypt_optional(self, ciphertext: bytes | None) -> str | None:
        """Decrypt bytes, returning None if input is None."""
        if ciphertext is None:
            return None
        return self.decrypt(ciphertext)


def generate_key() -> str:
    """
    Generate a new Fernet encryption key.

    Returns:
        Base64-encoded 32-byte key as string.
    """
    return Fernet.generate_key().decode()


if __name__ == "__main__":
    # Allow running this module to generate a key
    print(generate_key())