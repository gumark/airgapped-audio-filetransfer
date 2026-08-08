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


class ProtocolError(ValueError):
    """Raised when protocol configuration or packet data is invalid."""

# --- Protocol Constants ---

MAGIC = b"ATFR"  # Audio Transfer FRame
PROTOCOL_VERSION = 2

# Maximum frame payload size (bytes).
# Tuned to fit comfortably within audio symbol budget per frame.
MAX_PAYLOAD_SIZE = 2048

# Frame header size (without payload or CRC)
HEADER_SIZE = 4 + 1 + 4 + 1 + 4 + 4 + 2  # = 20 bytes
CRC_SIZE = 2
MAX_FRAME_SIZE = HEADER_SIZE + MAX_PAYLOAD_SIZE + CRC_SIZE

# FEC algorithms are part of the wire contract. Browser parity is only an
# integrity check and is intentionally not treated as Reed-Solomon correction.
FEC_NONE = 0
FEC_REED_SOLOMON = 1
FEC_XOR_PARITY = 2
FEC_ALGORITHM_NAMES = {
    FEC_NONE: "none",
    FEC_REED_SOLOMON: "reed-solomon",
    FEC_XOR_PARITY: "xor-parity",
}
FEC_ALGORITHM_IDS = {name: value for value, name in FEC_ALGORITHM_NAMES.items()}


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
    fec_algorithm: str = "reed-solomon"

    # Encryption
    encryption_enabled: bool = False
    encryption_algorithm: str = "chacha20-poly1305"

    # Compression
    compression_enabled: bool = True
    compression_algorithm: str = "zstd"

    # Chunking
    chunk_size: int = 4096        # bytes per data chunk before framing

    # Sync
    sync_preamble_symbols: int = 32  # number of sync symbols
    sync_frequency: int = 1000       # Hz for sync tone

    def __post_init__(self):
        # Disabled FEC has no algorithm on the wire. Normalize the common
        # ProtocolConfig(fec_enabled=False) form before validation.
        if not self.fec_enabled:
            self.fec_algorithm = "none"

    def validate(self) -> None:
        """Validate values that affect wire compatibility and DSP safety."""
        if self.sample_rate <= 0:
            raise ProtocolError("sample_rate must be positive")
        if not 50 <= self.symbol_rate <= 2000:
            raise ProtocolError("symbol_rate must be between 50 and 2000 baud")
        if self.sample_rate / self.symbol_rate < 2:
            raise ProtocolError("sample_rate must provide at least two samples per symbol")
        if not self.frequencies or len(self.frequencies) & (len(self.frequencies) - 1):
            raise ProtocolError("frequencies must contain a power-of-two number of entries")
        expected_bits = len(self.frequencies).bit_length() - 1
        if self.bits_per_symbol != expected_bits:
            raise ProtocolError("bits_per_symbol does not match frequencies")
        if any(f <= 0 or f >= self.sample_rate / 2 for f in self.frequencies):
            raise ProtocolError("frequencies must be between 0 and the Nyquist frequency")
        if len(set(self.frequencies)) != len(self.frequencies):
            raise ProtocolError("frequencies must be unique")
        # Goertzel evaluates one symbol at a time. Reject frequencies that
        # collapse onto the same detector bin at the configured symbol rate.
        samples_per_symbol = self.samples_per_symbol()
        detector_bins = [
            int(0.5 + samples_per_symbol * f / self.sample_rate)
            for f in self.frequencies
        ]
        if len(set(detector_bins)) != len(detector_bins):
            raise ProtocolError(
                "frequencies must map to unique symbol detector bins"
            )
        if self.sync_preamble_symbols < 1:
            raise ProtocolError("sync_preamble_symbols must be positive")
        if not 0 < self.sync_frequency < self.sample_rate / 2:
            raise ProtocolError("sync_frequency must be below the Nyquist frequency")
        if not 0 <= self.fec_overhead < 1:
            raise ProtocolError("fec_overhead must be in the range [0, 1)")
        if self.fec_algorithm not in FEC_ALGORITHM_IDS:
            raise ProtocolError(f"unsupported FEC algorithm: {self.fec_algorithm}")
        if self.fec_enabled and self.fec_algorithm == "none":
            raise ProtocolError("fec_algorithm cannot be none when FEC is enabled")
        if not self.fec_enabled and self.fec_algorithm not in {"none", "reed-solomon", "xor-parity"}:
            raise ProtocolError(f"unsupported FEC algorithm: {self.fec_algorithm}")
        if not 1 <= self.chunk_size <= 1_048_576:
            raise ProtocolError("chunk_size must be between 1 and 1048576")
        # TransferManager caps raw chunks at 128 bytes. Account for the
        # largest expected compression expansion and AEAD overhead so a
        # configuration cannot defer an oversized-frame failure until send.
        if self.fec_enabled and self.fec_algorithm == "reed-solomon":
            max_data = 255 - max(2, int(255 * self.fec_overhead))
            worst_case = min(self.chunk_size, 128) + (64 if self.compression_enabled else 0)
            if self.encryption_enabled:
                worst_case += 28  # 12-byte nonce + 16-byte authentication tag
            encoded_size = ((worst_case + max_data - 1) // max_data) * 255
            if encoded_size > MAX_PAYLOAD_SIZE:
                raise ProtocolError(
                    "FEC/encryption/compression settings exceed maximum frame payload"
                )

    def symbol_duration(self) -> float:
        """Duration of one symbol in seconds."""
        self.validate()
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


def encode_handshake_payload(config: ProtocolConfig) -> bytes:
    """Encode all settings required to validate the following audio stream."""
    config.validate()
    if len(config.frequencies) > 255:
        raise ProtocolError("handshake supports at most 255 frequencies")
    fec_algorithm = config.fec_algorithm if config.fec_enabled else "none"
    fec_overhead = config.fec_overhead if config.fec_enabled else 0.0
    header = struct.pack(
        ">IIBBBBBHH",
        config.sample_rate,
        config.symbol_rate,
        config.bits_per_symbol,
        len(config.frequencies),
        round(fec_overhead * 100),
        FEC_ALGORITHM_IDS[fec_algorithm],
        1 if config.fec_enabled else 0,
        config.sync_preamble_symbols,
        config.sync_frequency,
    )
    frequencies = b"".join(struct.pack(">H", frequency) for frequency in config.frequencies)
    return header + frequencies


def decode_handshake_payload(data: bytes) -> dict:
    """Decode and validate a handshake payload without changing local config."""
    header_size = struct.calcsize(">IIBBBBBHH")
    if len(data) < header_size:
        raise ProtocolError("handshake payload is truncated")
    (
        sample_rate,
        symbol_rate,
        bits_per_symbol,
        frequency_count,
        fec_overhead_percent,
        fec_algorithm_id,
        fec_enabled,
        sync_preamble_symbols,
        sync_frequency,
    ) = struct.unpack(">IIBBBBBHH", data[:header_size])
    expected_size = header_size + frequency_count * 2
    if len(data) != expected_size:
        raise ProtocolError("handshake payload has an invalid length")
    try:
        fec_algorithm = FEC_ALGORITHM_NAMES[fec_algorithm_id]
    except KeyError as exc:
        raise ProtocolError("handshake specifies an unknown FEC algorithm") from exc
    frequencies = [
        struct.unpack(">H", data[offset:offset + 2])[0]
        for offset in range(header_size, expected_size, 2)
    ]
    config = ProtocolConfig(
        sample_rate=sample_rate,
        symbol_rate=symbol_rate,
        bits_per_symbol=bits_per_symbol,
        frequencies=frequencies,
        fec_overhead=fec_overhead_percent / 100.0,
        fec_enabled=bool(fec_enabled),
        fec_algorithm=fec_algorithm if fec_enabled else "none",
        sync_preamble_symbols=sync_preamble_symbols,
        sync_frequency=sync_frequency,
    )
    config.validate()
    return {
        "sample_rate": sample_rate,
        "symbol_rate": symbol_rate,
        "bits_per_symbol": bits_per_symbol,
        "frequencies": frequencies,
        "fec_overhead": fec_overhead_percent / 100.0,
        "fec_algorithm": fec_algorithm,
        "fec_enabled": bool(fec_enabled),
        "sync_preamble_symbols": sync_preamble_symbols,
        "sync_frequency": sync_frequency,
    }


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
        if len(self.payload) > MAX_PAYLOAD_SIZE:
            raise ProtocolError(
                f"payload exceeds maximum size of {MAX_PAYLOAD_SIZE} bytes"
            )
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
    if payload_len > MAX_PAYLOAD_SIZE:
        return None
    try:
        parsed_frame_type = FrameType(frame_type)
    except ValueError:
        return None

    # Extract payload
    expected_len = HEADER_SIZE + payload_len + CRC_SIZE
    if len(data) < expected_len:
        return None

    payload = data[HEADER_SIZE:HEADER_SIZE + payload_len]

    # Verify CRC
    received_crc = struct.unpack(">H", data[expected_len - CRC_SIZE:expected_len])[0]
    frame = Frame(
        frame_type=parsed_frame_type,
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
    fec_overhead: float = 0.25,
    fec_enabled: bool = True,
    fec_algorithm: str = "reed-solomon",
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
    # Boolean flags, FEC overhead percentage, and FEC algorithm ID.
    if fec_algorithm not in FEC_ALGORITHM_IDS:
        raise ProtocolError(f"unsupported FEC algorithm: {fec_algorithm}")
    if not fec_enabled:
        fec_algorithm = "none"
    elif fec_algorithm == "none":
        raise ProtocolError("FEC enabled state and algorithm do not agree")
    result += struct.pack(">B", 1 if compression_enabled else 0)
    result += struct.pack(">B", 1 if encryption_enabled else 0)
    result += struct.pack(">B", round((fec_overhead if fec_enabled else 0.0) * 100))
    result += struct.pack(">B", 1 if fec_enabled else 0)
    result += struct.pack(">B", FEC_ALGORITHM_IDS[fec_algorithm])

    return result


def decode_metadata_payload(data: bytes) -> dict:
    """
    Decode metadata payload bytes into a dictionary.

    Raises ProtocolError for truncated or malformed metadata rather than
    leaking low-level struct/unicode exceptions to callers.
    """
    min_size = 8 + 4 + 4 + 1
    if len(data) < min_size:
        raise ProtocolError("metadata payload is truncated")

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

    if num_fields > len(field_names):
        raise ProtocolError("metadata contains too many string fields")

    for i in range(num_fields):
        if offset + 2 > len(data):
            raise ProtocolError("metadata field length is truncated")
        field_len = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        if offset + field_len > len(data):
            raise ProtocolError("metadata field is truncated")
        try:
            field_value = data[offset:offset + field_len].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("metadata contains invalid UTF-8") from exc
        offset += field_len
        result[field_names[i]] = field_value

    # Remaining fields are compression/encryption flags
    if offset < len(data):
        result["compression_enabled"] = bool(data[offset])
        offset += 1
    if offset < len(data):
        result["encryption_enabled"] = bool(data[offset])
        offset += 1
    if offset < len(data):
        result["fec_overhead"] = data[offset] / 100.0
        offset += 1
    if offset < len(data):
        result["fec_enabled"] = bool(data[offset])
        offset += 1
    if offset < len(data):
        try:
            result["fec_algorithm"] = FEC_ALGORITHM_NAMES[data[offset]]
        except KeyError as exc:
            raise ProtocolError("metadata specifies an unknown FEC algorithm") from exc
        offset += 1
    if offset != len(data):
        raise ProtocolError("metadata contains trailing bytes")

    return result
