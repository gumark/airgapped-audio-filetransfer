"""Regression tests for the high-level transfer manager."""

import numpy as np
import pytest

from backend.protocol.packet import Frame, FrameType, ProtocolConfig, ProtocolError
from backend.transfer.manager import TransferManager, TransferState


def test_transfer_manager_roundtrip(tmp_path):
    """A complete encoded audio stream can be received and verified."""
    original = b"manager integration test data " * 8
    source = tmp_path / "source.bin"
    source.write_bytes(original)

    config = ProtocolConfig(
        symbol_rate=500,
        fec_overhead=0.10,
        compression_enabled=True,
    )
    transmitter = TransferManager("transmitter")
    transmitter.configure(config)
    transmitter.load_file(str(source))
    audio = transmitter.start_transmission()

    assert transmitter.state is TransferState.COMPLETE
    assert transmitter.progress.frames_sent >= 5
    assert isinstance(audio, np.ndarray)
    assert len(audio) > 0

    receiver = TransferManager("receiver")
    receiver.configure(config)
    result = None
    for start in range(0, len(audio), 4096):
        result = receiver.process_audio_chunk(audio[start:start + 4096]) or result
        if result and result.get("complete"):
            break

    assert result is not None
    assert result["complete"] is True
    received, verified = receiver.get_received_file()
    assert verified is True
    assert received == original


def test_protocol_rejects_invalid_configuration():
    config = ProtocolConfig(frequencies=[1200, 1200, 2000, 2400])
    with pytest.raises(ProtocolError):
        config.validate()


def test_frame_rejects_oversized_payload():
    frame = Frame(frame_type=FrameType.DATA, payload=b"x" * 2049)
    with pytest.raises(ProtocolError):
        frame.serialize()
