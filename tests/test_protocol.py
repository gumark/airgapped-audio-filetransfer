"""
Tests for the protocol packet structure.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from backend.protocol.packet import (
    Frame, FrameType, ProtocolConfig,
    deserialize_frame, calculate_crc, MAGIC, PROTOCOL_VERSION,
    encode_metadata_payload, decode_metadata_payload,
    encode_handshake_payload, decode_handshake_payload, ProtocolError,
)


def test_frame_serialization_roundtrip():
    """Test that a frame can be serialized and deserialized correctly."""
    original = Frame(
        frame_type=FrameType.DATA,
        transfer_id=12345,
        sequence_number=42,
        total_frames=100,
        payload=b"Hello, World!",
    )

    serialized = original.serialize()
    deserialized = deserialize_frame(serialized)

    assert deserialized is not None
    assert deserialized.frame_type == FrameType.DATA
    assert deserialized.transfer_id == 12345
    assert deserialized.sequence_number == 42
    assert deserialized.total_frames == 100
    assert deserialized.payload == b"Hello, World!"


def test_frame_crc_detection():
    """Test that CRC errors are detected."""
    original = Frame(
        frame_type=FrameType.DATA,
        payload=b"Test data",
    )

    serialized = bytearray(original.serialize())

    # Corrupt a byte in the payload
    serialized[20] ^= 0xFF

    deserialized = deserialize_frame(bytes(serialized))
    assert deserialized is None  # Should fail CRC check


def test_frame_types():
    """Test all frame types can be created."""
    for ft in FrameType:
        frame = Frame(frame_type=ft, payload=b"test")
        serialized = frame.serialize()
        deserialized = deserialize_frame(serialized)
        assert deserialized is not None
        assert deserialized.frame_type == ft


def test_metadata_encoding():
    """Test metadata payload encoding and decoding."""
    original = encode_metadata_payload(
        filename="test.txt",
        filesize=1024,
        mime_type="text/plain",
        chunk_size=4096,
        total_chunks=1,
        hash_algorithm="sha256",
        file_hash="abc123",
        compression_enabled=True,
        encryption_enabled=False,
    )

    decoded = decode_metadata_payload(original)

    assert decoded["filename"] == "test.txt"
    assert decoded["filesize"] == 1024
    assert decoded["mime_type"] == "text/plain"
    assert decoded["chunk_size"] == 4096
    assert decoded["total_chunks"] == 1
    assert decoded["hash_algorithm"] == "sha256"
    assert decoded["file_hash"] == "abc123"
    assert decoded["compression_enabled"] is True
    assert decoded["encryption_enabled"] is False
    assert decoded["fec_algorithm"] == "reed-solomon"


def test_metadata_disabled_fec_has_zero_overhead_and_none_algorithm():
    payload = encode_metadata_payload(
        filename="x",
        filesize=0,
        mime_type="application/octet-stream",
        chunk_size=128,
        total_chunks=1,
        hash_algorithm="sha256",
        file_hash="00",
        compression_enabled=False,
        encryption_enabled=False,
        fec_overhead=0.25,
        fec_enabled=False,
    )
    decoded = decode_metadata_payload(payload)
    assert decoded["fec_overhead"] == 0.0
    assert decoded["fec_enabled"] is False
    assert decoded["fec_algorithm"] == "none"


def test_handshake_roundtrip():
    config = ProtocolConfig(
        fec_enabled=False,
        compression_enabled=False,
        sync_preamble_symbols=32,
    )
    decoded = decode_handshake_payload(encode_handshake_payload(config))
    assert decoded["sample_rate"] == config.sample_rate
    assert decoded["symbol_rate"] == config.symbol_rate
    assert decoded["bits_per_symbol"] == config.bits_per_symbol
    assert decoded["frequencies"] == config.frequencies
    assert decoded["fec_enabled"] is False
    assert decoded["fec_algorithm"] == "none"
    assert decoded["fec_overhead"] == 0.0


def test_handshake_rejects_trailing_bytes():
    config = ProtocolConfig()
    with pytest.raises(ProtocolError, match="invalid length"):
        decode_handshake_payload(encode_handshake_payload(config) + b"x")


def test_protocol_config():
    """Test protocol configuration."""
    config = ProtocolConfig()

    assert config.sample_rate == 48000
    assert config.symbol_rate == 250
    assert config.bits_per_symbol == 2
    assert len(config.frequencies) == 4

    # Test derived values
    assert config.symbol_duration() == 0.004  # 1/250
    assert config.samples_per_symbol() == 192  # 48000/250


def test_large_payload():
    """Test frames with large payloads."""
    large_payload = bytes(range(256)) * 8  # 2048 bytes

    frame = Frame(
        frame_type=FrameType.DATA,
        payload=large_payload,
    )

    serialized = frame.serialize()
    deserialized = deserialize_frame(serialized)

    assert deserialized is not None
    assert deserialized.payload == large_payload


if __name__ == "__main__":
    test_frame_serialization_roundtrip()
    test_frame_crc_detection()
    test_frame_types()
    test_metadata_encoding()
    test_protocol_config()
    test_large_payload()
    print("All protocol tests passed!")
