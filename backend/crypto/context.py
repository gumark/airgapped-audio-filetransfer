"""Authenticated encryption for independent streaming chunks.

Keys are supplied out-of-band. The salt and nonce prefix may be advertised in
metadata because neither is secret; the password or pre-shared key never is.
"""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

try:  # argon2-cffi is declared in requirements, but keep a documented fallback.
    from argon2.low_level import Type, hash_secret_raw
except ImportError:  # pragma: no cover - exercised only in minimal installs
    Type = None
    hash_secret_raw = None


@dataclass(frozen=True, slots=True)
class EncryptionInfo:
    enabled: bool
    mode: str
    salt: str | None
    nonce_prefix: str | None


class CryptoContext:
    def __init__(self, key: bytes | None = None, *, password: str | None = None, salt: bytes | None = None, nonce_prefix: bytes | None = None) -> None:
        if key is not None and password is not None:
            raise ValueError("provide a key or password, not both")
        if password is not None:
            salt = salt or os.urandom(16)
            if hash_secret_raw is not None:
                key = hash_secret_raw(password.encode(), salt, 2, 64 * 1024, 2, 32, Type.ID)
            else:
                # Minimal environments may lack argon2-cffi or impose a
                # small OpenSSL memory budget. Keep the fallback bounded while
                # production installs use Argon2id above.
                try:
                    key = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
                except ValueError:
                    key = hashlib.scrypt(password.encode(), salt=salt, n=2**13, r=8, p=1, dklen=32)
            self.mode = "password"
        elif key is not None:
            if len(key) != 32:
                raise ValueError("pre-shared key must be exactly 32 bytes")
            self.mode = "key"
        else:
            self.mode = "none"
        self._key = key
        self.salt = salt
        self.nonce_prefix = nonce_prefix or os.urandom(4)
        if len(self.nonce_prefix) != 4:
            raise ValueError("nonce_prefix must be 4 bytes")
        self._cipher = ChaCha20Poly1305(key) if key else None

    @property
    def enabled(self) -> bool:
        return self._cipher is not None

    def info(self) -> EncryptionInfo:
        return EncryptionInfo(
            self.enabled,
            self.mode,
            base64.b64encode(self.salt).decode() if self.salt else None,
            base64.b64encode(self.nonce_prefix).decode() if self.enabled else None,
        )

    def _nonce(self, sequence: int) -> bytes:
        if not 0 <= sequence < 1 << 64:
            raise ValueError("chunk sequence out of range")
        return self.nonce_prefix + sequence.to_bytes(8, "big")

    def seal(self, plaintext: bytes, sequence: int, associated_data: bytes = b"") -> bytes:
        if not self._cipher:
            return plaintext
        return self._cipher.encrypt(self._nonce(sequence), plaintext, associated_data)

    def open(self, ciphertext: bytes, sequence: int, associated_data: bytes = b"") -> bytes:
        if not self._cipher:
            return ciphertext
        return self._cipher.decrypt(self._nonce(sequence), ciphertext, associated_data)


def context_from_metadata(metadata: dict, *, password: str | None = None, key: bytes | None = None) -> CryptoContext:
    if not metadata.get("encryption"):
        return CryptoContext()
    salt = base64.b64decode(metadata["encryption_salt"])
    prefix = base64.b64decode(metadata["nonce_prefix"])
    if metadata.get("encryption_mode") == "password":
        if password is None:
            raise ValueError("a password is required for this transfer")
        return CryptoContext(password=password, salt=salt, nonce_prefix=prefix)
    if key is None:
        raise ValueError("the pre-shared key is required for this transfer")
    return CryptoContext(key=key, salt=salt, nonce_prefix=prefix)
