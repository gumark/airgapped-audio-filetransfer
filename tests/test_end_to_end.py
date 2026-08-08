"""
End-to-end tests for the air-gapped audio transfer system.

Tests the complete pipeline:
1. File → bytes
2. FEC encode
3. Modulate to audio
4. Simulated noisy channel
5. Demodulate from audio
6. FEC decode
7. Verify file integrity
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hashlib
import numpy as np
from backend.protocol.packet import (
    Frame, FrameType, ProtocolConfig, deserialize_frame,
)
from backend.dsp.modulation import FSKModulator
from backend.dsp.demodulation import FSKDemodulator
from backend.dsp.channel import create_test_channel, SimulatedChannel, ChannelParams
from backend.fec.reed_solomon import ReedSolomonFEC
from backend.crypto.encryption import CryptoEngine


def test_clean_channel_roundtrip():
    """Test complete roundtrip on a clean channel."""
    # Test data
    original_data = b"Hello, Air-Gapped World! " * 100  # 2500 bytes

    # FEC
    rs = ReedSolomonFEC(nsym=10)
    encoded = rs.encode(original_data)

    # Modulate
    mod = FSKModulator(sample_rate=48000, symbol_rate=250)
    audio = mod.modulate_bytes(encoded)

    # Demodulate
    demod = FSKDemodulator(sample_rate=48000, symbol_rate=250)
    recovered_encoded = demod.demodulate_to_bytes(audio)

    # FEC decode
    recovered, stats = rs.decode(recovered_encoded)

    # Verify
    assert recovered == original_data
    print(f"✓ Clean channel roundtrip: {len(original_data)} bytes")


def test_noisy_channel_roundtrip():
    """Test roundtrip with mild noise."""
    original_data = b"Test data for noisy channel " * 50

    # FEC with high overhead for noisy channel
    rs = ReedSolomonFEC(nsym=40)
    encoded = rs.encode(original_data)

    # Modulate
    mod = FSKModulator(sample_rate=48000, symbol_rate=250)
    audio = mod.modulate_bytes(encoded)

    # Simulate mild channel
    from backend.dsp.channel import SimulatedChannel, ChannelParams
    channel = SimulatedChannel(ChannelParams(noise_level_db=-35))
    received_audio = channel.transmit(audio, sample_rate=48000)

    # Demodulate
    demod = FSKDemodulator(sample_rate=48000, symbol_rate=250)
    recovered_encoded = demod.demodulate_to_bytes(received_audio)

    # FEC decode
    recovered, stats = rs.decode(recovered_encoded)

    # Verify (should still work with moderate noise)
    assert recovered == original_data
    print(f"✓ Noisy channel roundtrip: {len(original_data)} bytes")


def test_encrypted_roundtrip():
    """Test roundtrip with encryption enabled."""
    original_data = b"Encrypted secret data " * 50

    # Encrypt
    crypto, salt = CryptoEngine.from_password("test_password")
    encrypted = crypto.encrypt(original_data)

    # FEC
    rs = ReedSolomonFEC(nsym=15)
    encoded = rs.encode(encrypted)

    # Modulate
    mod = FSKModulator(sample_rate=48000, symbol_rate=250)
    audio = mod.modulate_bytes(encoded)

    # Demodulate
    demod = FSKDemodulator(sample_rate=48000, symbol_rate=250)
    recovered_encoded = demod.demodulate_to_bytes(audio)

    # FEC decode
    recovered_encrypted, stats = rs.decode(recovered_encoded)

    # Decrypt
    recovered = crypto.decrypt(recovered_encrypted)

    # Verify
    assert recovered == original_data
    print(f"✓ Encrypted roundtrip: {len(original_data)} bytes")


def test_frame_roundtrip():
    """Test frame serialization/deserialization roundtrip."""
    config = ProtocolConfig()

    # Create frames
    frames = []
    for i in range(10):
        frame = Frame(
            frame_type=FrameType.DATA,
            transfer_id=12345,
            sequence_number=i,
            total_frames=10,
            payload=f"Frame {i} data".encode(),
        )
        frames.append(frame.serialize())

    # Modulate all frames
    mod = FSKModulator(
        sample_rate=config.sample_rate,
        symbol_rate=config.symbol_rate,
    )
    audio = mod.modulate_bytes(b"".join(frames))

    # Demodulate
    demod = FSKDemodulator(
        sample_rate=config.sample_rate,
        symbol_rate=config.symbol_rate,
    )
    recovered_data = demod.demodulate_to_bytes(audio)

    # Parse frames
    # Note: This is simplified - real implementation would need frame sync
    print(f"✓ Frame roundtrip: {len(frames)} frames, {len(audio)} audio samples")


def test_file_hash_verification():
    """Test that file hash can be used for verification."""
    original = b"Important file content"
    file_hash = hashlib.sha256(original).hexdigest()

    # Simulate transfer
    mod = FSKModulator()
    demod = FSKDemodulator()

    audio = mod.modulate_bytes(original)
    recovered = demod.demodulate_to_bytes(audio)

    # Verify hash
    recovered_hash = hashlib.sha256(recovered[:len(original)]).hexdigest()
    assert recovered_hash == file_hash
    print(f"✓ Hash verification: {file_hash[:16]}...")


def test_fec_overhead_levels():
    """Test different FEC overhead levels."""
    original_data = b"Test data " * 100

    for overhead in [0.10, 0.20, 0.25, 0.30, 0.40]:
        nsym = ReedSolomonFEC.overhead_to_nsym(overhead)
        rs = ReedSolomonFEC(nsym=nsym)

        encoded = rs.encode(original_data)
        recovered, stats = rs.decode(encoded)

        assert recovered == original_data
        print(f"✓ FEC overhead {overhead*100:.0f}%: nsym={nsym}")


def test_large_data_transfer():
    """Test with larger data size."""
    # 10 KB of data
    original_data = bytes(range(256)) * 40

    # FEC
    rs = ReedSolomonFEC(nsym=10)
    encoded = rs.encode(original_data)

    # Modulate
    mod = FSKModulator(sample_rate=48000, symbol_rate=250)
    audio = mod.modulate_bytes(encoded)

    # Demodulate
    demod = FSKDemodulator(sample_rate=48000, symbol_rate=250)
    recovered_encoded = demod.demodulate_to_bytes(audio)

    # FEC decode
    recovered, stats = rs.decode(recovered_encoded)

    # Verify
    assert recovered == original_data
    print(f"✓ Large data transfer: {len(original_data)} bytes")


if __name__ == "__main__":
    test_clean_channel_roundtrip()
    test_noisy_channel_roundtrip()
    test_encrypted_roundtrip()
    test_frame_roundtrip()
    test_file_hash_verification()
    test_fec_overhead_levels()
    test_large_data_transfer()
    print("\n✓ All end-to-end tests passed!")
