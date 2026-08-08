"""Regression tests for bugs found during the code audit."""

import numpy as np
import pytest

from backend.dsp.demodulation import FSKDemodulator, goertzel_magnitude
from backend.dsp.modulation import FSKModulator
from backend.protocol.packet import (
    Frame,
    FrameType,
    ProtocolConfig,
    ProtocolError,
    encode_handshake_payload,
    encode_metadata_payload,
)
from backend.transfer.manager import TransferManager, TransferState


def test_three_bit_symbols_roundtrip():
    """Non-byte-aligned 8-FSK symbols preserve all input bits."""
    frequencies = [800, 1400, 2000, 2600, 3200, 3800, 4400, 5000]
    data = bytes([0x00, 0x01, 0x12, 0xAB, 0xFF, 0x80])
    modulator = FSKModulator(48000, frequencies, 500)
    demodulator = FSKDemodulator(48000, frequencies, 500)

    recovered = demodulator.demodulate_to_bytes(modulator.modulate_bytes(data))

    assert recovered == data


def test_configure_clears_previous_transfer_state(tmp_path):
    """Reusing a manager cannot retain frames or file data from a prior run."""
    source = tmp_path / "source.bin"
    source.write_bytes(b"old data")
    manager = TransferManager("transmitter")
    manager.configure(ProtocolConfig(compression_enabled=False, fec_enabled=False))
    manager.load_file(str(source))
    manager._received_frames[0] = b"stale"
    manager._file_data = b"stale file data"

    manager.configure(ProtocolConfig(compression_enabled=False, fec_enabled=False))

    assert manager._received_frames == {}
    assert manager._file_data == b""
    assert manager.state is TransferState.IDLE


def test_valid_eight_fsk_roundtrip_configuration():
    config = ProtocolConfig(
        frequencies=[800, 1400, 2000, 2600, 3200, 3800, 4400, 5000],
        bits_per_symbol=3,
        symbol_rate=500,
    )
    config.validate()


def test_empty_dsp_inputs_are_safe():
    demodulator = FSKDemodulator()
    empty = np.array([], dtype=np.float32)

    assert goertzel_magnitude(empty, 1000, 48000) == 0.0
    assert demodulator.detect_clipping(empty) == 0.0


def test_detector_bins_reject_ambiguous_frequencies():
    config = ProtocolConfig(
        frequencies=[800, 1000, 1200, 1400, 1600, 1800, 2000, 2200],
        bits_per_symbol=3,
        symbol_rate=500,
    )

    with pytest.raises(ValueError, match="detector bins"):
        config.validate()


def test_receiver_rejects_handshake_mismatch():
    receiver = TransferManager("receiver")
    receiver.configure(ProtocolConfig(fec_enabled=False, compression_enabled=False))
    frame = Frame(
        FrameType.HANDSHAKE,
        transfer_id=1,
        total_frames=1,
        payload=encode_handshake_payload(
            ProtocolConfig(
                fec_enabled=False,
                compression_enabled=False,
                symbol_rate=500,
            )
        ),
    )
    receiver._transfer_id = 1
    with pytest.raises(ProtocolError, match="handshake symbol_rate"):
        receiver._process_frame(frame)


def test_receiver_rejects_browser_parity_metadata():
    receiver = TransferManager("receiver")
    receiver.configure(ProtocolConfig(fec_enabled=False, compression_enabled=False))
    metadata = encode_metadata_payload(
        filename="x",
        filesize=1,
        mime_type="application/octet-stream",
        chunk_size=128,
        total_chunks=1,
        hash_algorithm="sha256",
        file_hash="00",
        compression_enabled=False,
        encryption_enabled=False,
        fec_overhead=0.25,
        fec_enabled=True,
        fec_algorithm="xor-parity",
    )
    receiver._transfer_id = 1
    with pytest.raises(ProtocolError, match="unsupported FEC algorithm"):
        receiver._process_frame(Frame(FrameType.METADATA, transfer_id=1, payload=metadata))
