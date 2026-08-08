"""
Authenticated encryption engine.

Provides end-to-end encryption for files before audio transmission.
Supports two key derivation modes:

1. Password mode: User enters same password on both computers.
   Key is derived using Argon2id KDF.

2. Pre-shared key mode: User provides a raw key through a separate
   physical method (e.g., USB drive, QR code, written down).

IMPORTANT: The encryption key is NEVER transmitted through the audio channel.
"""

import os
import hashlib
import struct
from typing import Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import (
    ChaCha20Poly1305,
    AESGCM,
)
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend


class CryptoEngine:
    """
    Authenticated encryption engine supporting ChaCha20-Poly1305 and AES-256-GCM.

    Usage:
        # Password mode
        crypto = CryptoEngine.from_password("my secret password")

        # Pre-shared key mode
        crypto = CryptoEngine.from_key(raw_key_bytes)

        # Encrypt
        ciphertext = crypto.encrypt(plaintext)

        # Decrypt
        plaintext = crypto.decrypt(ciphertext)
    """

    # Supported algorithms
    ALGORITHMS = {
        "chacha20-poly1305": ChaCha20Poly1305,
        "aes-256-gcm": AESGCM,
    }

    # Key sizes for each algorithm
    KEY_SIZES = {
        "chacha20-poly1305": 32,  # 256 bits
        "aes-256-gcm": 32,       # 256 bits
    }

    # Nonce sizes for each algorithm
    NONCE_SIZES = {
        "chacha20-poly1305": 12,  # 96 bits
        "aes-256-gcm": 12,       # 96 bits
    }

    # Auth tag sizes
    TAG_SIZES = {
        "chacha20-poly1305": 16,  # 128 bits
        "aes-256-gcm": 16,       # 128 bits
    }

    def __init__(
        self,
        key: bytes,
        algorithm: str = "chacha20-poly1305",
    ):
        """
        Initialize crypto engine with a raw key.

        Args:
            key: Encryption key (must match KEY_SIZES[algorithm])
            algorithm: Encryption algorithm name
        """
        if algorithm not in self.ALGORITHMS:
            raise ValueError(f"Unsupported algorithm: {algorithm}")

        expected_size = self.KEY_SIZES[algorithm]
        if len(key) != expected_size:
            raise ValueError(
                f"Key must be {expected_size} bytes for {algorithm}, "
                f"got {len(key)}"
            )

        self.algorithm = algorithm
        self.key = key
        self._cipher_class = self.ALGORITHMS[algorithm]

    @classmethod
    def from_password(
        cls,
        password: str,
        algorithm: str = "chacha20-poly1305",
        salt: Optional[bytes] = None,
        argon2_time_cost: int = 3,
        argon2_memory_cost: int = 65536,  # 64 MB
        argon2_parallelism: int = 4,
    ) -> Tuple["CryptoEngine", bytes]:
        """
        Create crypto engine from a password using Argon2id KDF.

        Args:
            password: User password (same on both computers)
            algorithm: Encryption algorithm
            salt: Optional salt (generated randomly if None)
            argon2_time_cost: Number of iterations
            argon2_memory_cost: Memory usage in KB
            argon2_parallelism: Number of parallel threads

        Returns:
            Tuple of (CryptoEngine, salt)
            The salt must be transmitted in the metadata (it's not secret).
        """
        if salt is None:
            salt = os.urandom(16)

        # Derive key using Argon2id
        kdf = Argon2id(
            iterations=argon2_time_cost,
            memory_cost=argon2_memory_cost,
            lanes=argon2_parallelism,
            length=cls.KEY_SIZES[algorithm],
            salt=salt,
        )

        key = kdf.derive(password.encode("utf-8"))
        return cls(key, algorithm), salt

    @classmethod
    def from_key(cls, key: bytes, algorithm: str = "chacha20-poly1305") -> "CryptoEngine":
        """
        Create crypto engine from a pre-shared raw key.

        The key should be provided through a separate physical method
        (e.g., USB drive, QR code, written down).
        """
        return cls(key, algorithm)

    def encrypt(self, plaintext: bytes, associated_data: Optional[bytes] = None) -> bytes:
        """
        Encrypt data with authenticated encryption.

        Output format: [NONCE(12)] [CIPHERTEXT] [TAG(16)]

        Args:
            plaintext: Data to encrypt
            associated_data: Optional authenticated but not encrypted data

        Returns:
            Encrypted data with nonce and auth tag
        """
        cipher = self._cipher_class(self.key)
        nonce = os.urandom(self.NONCE_SIZES[self.algorithm])

        ciphertext = cipher.encrypt(nonce, plaintext, associated_data)

        # Output: nonce + ciphertext (includes auth tag)
        return nonce + ciphertext

    def decrypt(self, data: bytes, associated_data: Optional[bytes] = None) -> bytes:
        """
        Decrypt and verify authenticated encryption.

        Args:
            data: Encrypted data (nonce + ciphertext + tag)
            associated_data: Optional authenticated data

        Returns:
            Decrypted plaintext

        Raises:
            ValueError: If authentication fails (data tampered)
        """
        nonce_size = self.NONCE_SIZES[self.algorithm]
        if len(data) < nonce_size:
            raise ValueError("Encrypted data too short")

        nonce = data[:nonce_size]
        ciphertext = data[nonce_size:]

        cipher = self._cipher_class(self.key)
        try:
            return cipher.decrypt(nonce, ciphertext, associated_data)
        except Exception as e:
            raise ValueError(f"Decryption failed (authentication error): {e}") from e

    def encrypt_chunk(self, chunk: bytes, chunk_index: int) -> bytes:
        """
        Encrypt a file chunk with the chunk index as associated data.

        This allows verification that chunks are in the correct order.
        """
        aad = struct.pack(">I", chunk_index)
        return self.encrypt(chunk, aad)

    def decrypt_chunk(self, data: bytes, chunk_index: int) -> bytes:
        """
        Decrypt a file chunk, verifying the chunk index.
        """
        aad = struct.pack(">I", chunk_index)
        return self.decrypt(data, aad)

    @staticmethod
    def compute_file_hash(data: bytes, algorithm: str = "sha256") -> str:
        """
        Compute cryptographic hash of file data.

        Args:
            data: File data
            algorithm: Hash algorithm (sha256, sha384, sha512)

        Returns:
            Hex-encoded hash string
        """
        h = hashlib.new(algorithm)
        h.update(data)
        return h.hexdigest()

    @staticmethod
    def compute_file_hash_streaming(
        file_path: str, algorithm: str = "sha256", chunk_size: int = 65536
    ) -> str:
        """
        Compute hash of a file without loading it entirely into memory.
        """
        h = hashlib.new(algorithm)
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def get_info(self) -> dict:
        """Get information about the current crypto configuration."""
        return {
            "algorithm": self.algorithm,
            "key_size": len(self.key),
            "nonce_size": self.NONCE_SIZES[self.algorithm],
            "tag_size": self.TAG_SIZES[self.algorithm],
            "key_hex": self.key.hex()[:16] + "...",  # First 16 hex chars only
        }
