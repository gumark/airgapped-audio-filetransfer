"""Wire frames.

The audio modem transports bytes; this module deliberately has no socket or file
system code so it can be tested independently. CRC32 rejects corrupted frames
before they reach decryption or FEC reconstruction.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum

MAGIC = b"AFT1"
PROTOCOL_VERSION = 1
_HEADER = struct.Struct(">4sBBQIII")
_CRC = struct.Struct(">I")
MAX_PAYLOAD = 1_048_576


class FrameType(IntEnum):
    SYNC = 1
    HANDSHAKE = 2
    METADATA = 3
    DATA = 4
    PARITY = 5
    END = 6
    ACK = 7
    ERROR = 8


@dataclass(frozen=True, slots=True)
class Frame:
    transfer_id: int
    frame_type: FrameType
    sequence: int
    total_frames: int
    payload: bytes
    version: int = PROTOCOL_VERSION

    def encode(self) -> bytes:
        if not 0 <= self.transfer_id < 1 << 64:
            raise ValueError("transfer_id must fit in uint64")
        if not 0 <= self.sequence < 1 << 32:
            raise ValueError("sequence must fit in uint32")
        if not 0 <= self.total_frames < 1 << 32:
            raise ValueError("total_frames must fit in uint32")
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError(f"payload exceeds {MAX_PAYLOAD} bytes")
        header = _HEADER.pack(
            MAGIC,
            self.version,
            int(self.frame_type),
            self.transfer_id,
            self.sequence,
            self.total_frames,
            len(self.payload),
        )
        return header + self.payload + _CRC.pack(zlib.crc32(header + self.payload) & 0xFFFFFFFF)

    @property
    def wire_size(self) -> int:
        return _HEADER.size + len(self.payload) + _CRC.size


def encode_frame(frame: Frame) -> bytes:
    """Functional wrapper for callers that prefer not to use ``Frame.encode``."""
    return frame.encode()


def decode_frame(raw: bytes, *, max_payload: int = MAX_PAYLOAD) -> Frame:
    """Decode one complete frame and reject malformed or corrupted bytes."""
    if len(raw) < _HEADER.size + _CRC.size:
        raise ValueError("frame is truncated")
    magic, version, frame_type, transfer_id, sequence, total, payload_len = _HEADER.unpack_from(raw)
    if magic != MAGIC:
        raise ValueError("bad frame magic")
    if version != PROTOCOL_VERSION:
        raise ValueError(f"unsupported protocol version {version}")
    if payload_len > max_payload:
        raise ValueError("payload is too large")
    expected_size = _HEADER.size + payload_len + _CRC.size
    if len(raw) != expected_size:
        raise ValueError("frame length does not match payload length")
    payload = raw[_HEADER.size : _HEADER.size + payload_len]
    received_crc = _CRC.unpack_from(raw, _HEADER.size + payload_len)[0]
    actual_crc = zlib.crc32(raw[: _HEADER.size + payload_len]) & 0xFFFFFFFF
    if received_crc != actual_crc:
        raise ValueError("frame CRC mismatch")
    try:
        kind = FrameType(frame_type)
    except ValueError as exc:
        raise ValueError(f"unknown frame type {frame_type}") from exc
    return Frame(transfer_id, kind, sequence, total, payload, version)


def frame_from_prefix(raw: bytes) -> tuple[Frame, int] | None:
    """Parse a frame from a byte buffer when a complete frame is available."""
    if len(raw) < _HEADER.size:
        return None
    if raw[:4] != MAGIC:
        raise ValueError("bad frame magic")
    payload_len = _HEADER.unpack_from(raw)[-1]
    total = _HEADER.size + payload_len + _CRC.size
    if len(raw) < total:
        return None
    return decode_frame(raw[:total]), total
