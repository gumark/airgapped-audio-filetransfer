"""
Core packet structure for the air-gapped audio transfer protocol.

Frame Layout (binary):
  [MAGIC(4)] [VERSION(1)] [TRANSFER_ID(4)] [FRAME_TYPE(1)]
  [SEQ_NUM(4)] [TOTAL_FRAMES(4)] [PAYLOAD_LEN(2)] [PAYLOAD(var)] [CRC(2)]

All multi-byte integers are big-endian.
CRC-16 covers everything except the CRC field itself.
"""

import struct
import binascii
import uuid
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Optional

# --- Protocol Constants ---

MAGIC = b"ATFR"  # Audio Transfer FRame
PROTOCOL_VERSION = 1

# Maximum frame payload size (bytes).
# Tuned to fit comfortably within audio symbol budget per frame.
MAX_PAYLOAD_SIZE = 2048

# Frame header size (without payload or CRC)
HEADER_SIZE = 4 + 1 + 4 + 1 + 4 + 4 + 2  # = 20 bytes
CRC_SIZE = 2
MAX_FRAME_SIZE = HEADER_SIZE + MAX_PAYLOAD_SIZE + CRC_SIZE


class FrameType(IntEnum):
    """Types of frames in the protocol."""
    SYNC = 0x01        # Synchronization preamble
    HANDSHAKE = 0x02   # Initial handshake (transfer parameters)
    METADATA = 0x03    # File metadata
    DATA = 0x04        # Data frame
    PARITY = 0x05      # FEC parity/redundancy frame
    END = 0x06         # End of transmission
    ACK = 0x07         # Acknowledgement (reserved for future bidirectional)
    ERROR = 0x08       # Error indication
    CALIBRATION = 0x09 # Calibration signal


@dataclass
class ProtocolConfig:
    """
    Configurable protocol parameters.
    These are negotiated during handshake or set by user.
    """
    # Modulation
    sample_rate: int = 48000
    symbol_rate: int = 250        # symbols per second
    bits_per_symbol: int = 2      # 4-FSK = 2 bits per symbol
    frequencies: list = field(default_factory=lambda: [1200, 1600, 2000, 2400])

    # FEC
    fec_overhead: float = 0.25    # 25% redundancy
    fec_enabled: bool = True

    # Encryption
    encryption_enabled: bool = False
    encryption_algorithm: str = "chacha20-poly1305"

    # Compression
    compression_enabled: bool = True
    compression_algorithm: str = "zstd"

    # Chunking
    chunk_size: int = 4096        # bytes per data chunk before framing

    # Sync
    sync_preamble_symbols: int = 64  # number of sync symbols
    sync_frequency: int = 1000       # Hz for sync tone

    def symbol_duration(self) -> float:
        """Duration of one symbol in seconds."""
        return 1.0 / self.symbol_rate

    def samples_per_symbol(self) -> int:
        """Number of audio samples per symbol."""
        return int(self.sample_rate / self.symbol_rate)

    def bits_per_frame(self) -> int:
        """Bits carried per data frame (payload only)."""
        return self.chunk_size * 8

    def symbols_per_frame(self) -> int:
        """Number of audio symbols needed to carry one data frame."""
        bits = self.chunk_size * 8
        return (bits + self.bits_per_symbol - 1) // self.bits_per_symbol


def calculate_crc(data: bytes) -> int:
    """Calculate CRC-16/CCITT over data."""
    return binascii.crc_hqx(data, 0xFFFF)


@dataclass
class Frame:
    """
    A single protocol frame.
    """
    frame_type: FrameType
    transfer_id: int = 0
    sequence_number: int = 0
    total_frames: int = 0
    payload: bytes = b""
    crc: int = 0

    def __post_init__(self):
        if self.transfer_id == 0:
            # Generate a random transfer ID
            self.transfer_id = uuid.uuid4().int & 0xFFFFFFFF

    def calculate_crc(self) -> int:
        """Calculate CRC over the serialized header+payload."""
        header_payload = self._serialize_no_crc()
        return calculate_crc(header_payload)

    def _serialize_no_crc(self) -> bytes:
        """Serialize everything except the CRC field."""
        header = struct.pack(
            ">4s B I B I I H",
            MAGIC,
            PROTOCOL_VERSION,
            self.transfer_id,
            self.frame_type,
            self.sequence_number,
            self.total_frames,
            len(self.payload),
        )
        return header + self.payload

    def serialize(self) -> bytes:
        """Serialize the full frame including CRC."""
        self.crc = self.calculate_crc()
        return self._serialize_no_crc() + struct.pack(">H", self.crc)


def deserialize_frame(data: bytes) -> Optional[Frame]:
    """
    Deserialize bytes into a Frame object.
    Returns None if data is too short or CRC is invalid.
    """
    if len(data) < HEADER_SIZE + CRC_SIZE:
        return None

    # Unpack header
    magic, version, transfer_id, frame_type, seq_num, total_frames, payload_len = struct.unpack(
        ">4s B I B I I H", data[:HEADER_SIZE]
    )

    # Validate magic
    if magic != MAGIC:
        return None

    # Validate version
    if version != PROTOCOL_VERSION:
        return None

    # Extract payload
    expected_len = HEADER_SIZE + payload_len + CRC_SIZE
    if len(data) < expected_len:
        return None

    payload = data[HEADER_SIZE:HEADER_SIZE + payload_len]

    # Verify CRC
    received_crc = struct.unpack(">H", data[expected_len - CRC_SIZE:expected_len])[0]
    frame = Frame(
        frame_type=FrameType(frame_type),
        transfer_id=transfer_id,
        sequence_number=seq_num,
        total_frames=total_frames,
        payload=payload,
    )
    expected_crc = frame.calculate_crc()

    if received_crc != expected_crc:
        return None

    return frame


def encode_metadata_payload(
    filename: str,
    filesize: int,
    mime_type: str,
    chunk_size: int,
    total_chunks: int,
    hash_algorithm: str,
    file_hash: str,
    compression_enabled: bool,
    encryption_enabled: bool,
) -> bytes:
    """
    Encode transfer metadata into a payload bytes.

    Metadata fields are serialized as length-prefixed UTF-8 strings
    for easy parsing, followed by boolean flags.
    """
    # String fields
    string_fields = [
        filename.encode("utf-8"),
        mime_type.encode("utf-8"),
        hash_algorithm.encode("utf-8"),
        file_hash.encode("utf-8"),
    ]

    # Pack metadata
    result = b""
    # File size (8 bytes)
    result += struct.pack(">Q", filesize)
    # Chunk size (4 bytes)
    result += struct.pack(">I", chunk_size)
    # Total chunks (4 bytes)
    result += struct.pack(">I", total_chunks)
    # Number of string fields (1 byte)
    result += struct.pack(">B", len(string_fields))
    # Length-prefixed string fields
    for f in string_fields:
        result += struct.pack(">H", len(f))
        result += f
    # Boolean flags (1 byte each)
    result += struct.pack(">B", 1 if compression_enabled else 0)
    result += struct.pack(">B", 1 if encryption_enabled else 0)

    return result


def decode_metadata_payload(data: bytes) -> dict:
    """
    Decode metadata payload bytes into a dictionary.
    """
    offset = 0

    filesize = struct.unpack(">Q", data[offset:offset + 8])[0]
    offset += 8

    chunk_size = struct.unpack(">I", data[offset:offset + 4])[0]
    offset += 4

    total_chunks = struct.unpack(">I", data[offset:offset + 4])[0]
    offset += 4

    num_fields = struct.unpack(">B", data[offset:offset + 1])[0]
    offset += 1

    field_names = ["filename", "mime_type", "hash_algorithm", "file_hash"]
    result = {
        "filesize": filesize,
        "chunk_size": chunk_size,
        "total_chunks": total_chunks,
    }

    for i in range(num_fields):
        field_len = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        field_value = data[offset:offset + field_len].decode("utf-8")
        offset += field_len
        if i < len(field_names):
            result[field_names[i]] = field_value

    # Remaining fields are compression/encryption flags
    if offset < len(data):
        result["compression_enabled"] = bool(data[offset])
        offset += 1
    if offset < len(data):
        result["encryption_enabled"] = bool(data[offset])

    return result
