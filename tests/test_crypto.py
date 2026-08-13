import pytest

from backend.crypto import CryptoContext


def test_password_context_is_deterministic_for_shared_parameters():
    first = CryptoContext(password="correct horse battery staple", salt=b"0123456789abcdef", nonce_prefix=b"abcd")
    second = CryptoContext(password="correct horse battery staple", salt=b"0123456789abcdef", nonce_prefix=b"abcd")
    sealed = first.seal(b"secret", 9, b"ad")
    assert second.open(sealed, 9, b"ad") == b"secret"
    with pytest.raises(Exception):
        second.open(sealed, 9, b"wrong")


def test_key_must_be_32_bytes():
    with pytest.raises(ValueError):
        CryptoContext(key=b"short")
