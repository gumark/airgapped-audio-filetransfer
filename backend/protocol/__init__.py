"""
Protocol layer for the air-gapped audio file transfer system.

Defines packet structure, frame types, constants, and serialization.
"""

from .packet import (
    FrameType,
    ProtocolConfig,
    Frame,
    deserialize_frame,
    calculate_crc,
    MAGIC,
    PROTOCOL_VERSION,
)

__all__ = [
    "FrameType",
    "ProtocolConfig",
    "Frame",
    "deserialize_frame",
    "calculate_crc",
    "MAGIC",
    "PROTOCOL_VERSION",
]
