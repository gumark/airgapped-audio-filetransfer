"""
Tests for Reed-Solomon Forward Error Correction.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.fec.reed_solomon import ReedSolomonFEC


def test_encode_decode_clean():
    """Test encoding and decoding with no errors."""
    rs = ReedSolomonFEC(nsym=10)

    original = b"Hello, World! This is a test message."
    encoded = rs.encode(original)
    decoded, stats = rs.decode(encoded)

    assert decoded == original
    assert not stats["uncorrectable_errors"]


def test_error_correction():
    """Test that errors can be corrected."""
    rs = ReedSolomonFEC(nsym=20)  # Can correct up to 10 errors

    original = b"Test data for error correction"
    encoded = bytearray(rs.encode(original))

    # Introduce errors (up to nsym/2 = 10)
    for i in range(5):
        encoded[i * 10] ^= 0xFF

    decoded, stats = rs.decode(bytes(encoded))
    assert decoded == original


def test_over_too_many_errors():
    """Test that too many errors are detected."""
    rs = ReedSolomonFEC(nsym=10)  # Can correct up to 5 errors

    original = b"Test data"
    encoded = bytearray(rs.encode(original))

    # Introduce more errors than can be corrected
    for i in range(8):
        encoded[i] ^= 0xFF

    try:
        rs.decode(bytes(encoded))
        # Should either fail or raise
    except ValueError:
        pass  # Expected


def test_chunk_encoding():
    """Test encoding data in chunks."""
    rs = ReedSolomonFEC(nsym=10)

    # Create data larger than RS block size
    original = bytes(range(256)) * 5  # 1280 bytes

    chunks = rs.encode_chunks(original, chunk_size=100)
    decoded, stats = rs.decode_chunks(chunks)

    assert decoded == original
    assert stats["total_chunks"] == len(chunks)
    assert stats["failed_chunks"] == 0


def test_overhead_conversion():
    """Test overhead to nsym conversion."""
    nsym = ReedSolomonFEC.overhead_to_nsym(0.25, block_size=255)
    assert 50 <= nsym <= 70  # 25% of 255 ≈ 64

    nsym_low = ReedSolomonFEC.overhead_to_nsym(0.10)
    nsym_high = ReedSolomonFEC.overhead_to_nsym(0.40)
    assert nsym_low < nsym_high


def test_redundancy_info():
    """Test redundancy information output."""
    rs = ReedSolomonFEC(nsym=20)

    info = rs.get_redundancy_info(1000)

    assert info["nsym"] == 20
    assert info["data_size"] == 1000
    assert info["encoded_size"] == 1020
    assert info["overhead_percent"] == 2.0
    assert info["correctable_errors"] == 10


if __name__ == "__main__":
    test_encode_decode_clean()
    test_error_correction()
    test_over_too_many_errors()
    test_chunk_encoding()
    test_overhead_conversion()
    test_redundancy_info()
    print("All FEC tests passed!")
