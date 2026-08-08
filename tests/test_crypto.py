"""
Tests for the cryptographic module.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.crypto.encryption import CryptoEngine


def test_basic_encryption_decryption():
    """Test basic encrypt/decrypt roundtrip."""
    key = CryptoEngine.compute_file_hash(b"test_key").encode()[:32]
    crypto = CryptoEngine(key)

    plaintext = b"Hello, World! This is secret data."
    ciphertext = crypto.encrypt(plaintext)

    assert ciphertext != plaintext
    assert len(ciphertext) > len(plaintext)

    decrypted = crypto.decrypt(ciphertext)
    assert decrypted == plaintext


def test_password_derivation():
    """Test key derivation from password."""
    crypto, salt = CryptoEngine.from_password("my_secret_password")

    assert len(salt) == 16
    assert crypto.key is not None
    assert len(crypto.key) == 32

    # Same password + same salt should give same key
    crypto2, _ = CryptoEngine.from_password(
        "my_secret_password", salt=salt
    )
    assert crypto.key == crypto2.key


def test_different_passwords_different_keys():
    """Test that different passwords produce different keys."""
    crypto1, _ = CryptoEngine.from_password("password1")
    crypto2, _ = CryptoEngine.from_password("password2")

    assert crypto1.key != crypto2.key


def test_authenticated_encryption():
    """Test that authentication detects tampering."""
    key = CryptoEngine.compute_file_hash(b"test").encode()[:32]
    crypto = CryptoEngine(key)

    plaintext = b"Sensitive data"
    ciphertext = crypto.encrypt(plaintext)

    # Tamper with ciphertext
    tampered = bytearray(ciphertext)
    tampered[15] ^= 0xFF
    tampered = bytes(tampered)

    try:
        crypto.decrypt(tampered)
        assert False, "Should have raised"
    except ValueError:
        pass  # Expected


def test_chunk_encryption():
    """Test chunk-based encryption."""
    key = CryptoEngine.compute_file_hash(b"test").encode()[:32]
    crypto = CryptoEngine(key)

    data = b"Chunk data for encryption test"
    chunk_idx = 42

    encrypted = crypto.encrypt_chunk(data, chunk_idx)
    decrypted = crypto.decrypt_chunk(encrypted, chunk_idx)

    assert decrypted == data


def test_wrong_key_fails():
    """Test that wrong key fails decryption."""
    key1 = CryptoEngine.compute_file_hash(b"key1").encode()[:32]
    key2 = CryptoEngine.compute_file_hash(b"key2").encode()[:32]

    crypto1 = CryptoEngine(key1)
    crypto2 = CryptoEngine(key2)

    plaintext = b"Secret data"
    ciphertext = crypto1.encrypt(plaintext)

    try:
        crypto2.decrypt(ciphertext)
        assert False, "Should have raised"
    except ValueError:
        pass  # Expected


def test_file_hash():
    """Test file hash computation."""
    data = b"Test file content"
    hash1 = CryptoEngine.compute_file_hash(data)
    hash2 = CryptoEngine.compute_file_hash(data)

    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 hex


def test_algorithms():
    """Test different encryption algorithms."""
    for algo in ["chacha20-poly1305", "aes-256-gcm"]:
        key = CryptoEngine.compute_file_hash(b"test").encode()[:32]
        crypto = CryptoEngine(key, algorithm=algo)

        plaintext = b"Test data for " + algo.encode()
        ciphertext = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(ciphertext)

        assert decrypted == plaintext


if __name__ == "__main__":
    test_basic_encryption_decryption()
    test_password_derivation()
    test_different_passwords_different_keys()
    test_authenticated_encryption()
    test_chunk_encryption()
    test_wrong_key_fails()
    test_file_hash()
    test_algorithms()
    print("All crypto tests passed!")
