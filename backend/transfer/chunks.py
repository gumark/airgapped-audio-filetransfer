"""Streaming chunk transforms used by transmitter and receiver."""
from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass

from backend.crypto import CryptoContext

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover
    zstd = None

_CHUNK_MAGIC = b"CH1"
_CHUNK_HEADER = struct.Struct(">3sBII")


@dataclass(frozen=True, slots=True)
class ChunkCodec:
    chunk_size: int
    compression: str = "none"

    def __post_init__(self) -> None:
        if not 1024 <= self.chunk_size <= 4 * 1024 * 1024:
            raise ValueError("chunk_size must be between 1 KiB and 4 MiB")
        if self.compression not in {"none", "zstd", "gzip"}:
            raise ValueError("unsupported compression")
        if self.compression == "zstd" and zstd is None:
            raise ValueError("zstandard is not installed")

    def _compress(self, source: bytes) -> tuple[bytes, bool]:
        if self.compression == "zstd":
            candidate = zstd.ZstdCompressor(level=3).compress(source)
        elif self.compression == "gzip":
            candidate = gzip.compress(source, compresslevel=6, mtime=0)
        else:
            candidate = source
        return (candidate, True) if len(candidate) < len(source) else (source, False)

    def _decompress(self, value: bytes, compressed: bool) -> bytes:
        if not compressed:
            return value
        if self.compression == "zstd":
            return zstd.ZstdDecompressor().decompress(value)
        if self.compression == "gzip":
            return gzip.decompress(value)
        raise ValueError("compressed chunk received with compression disabled")

    def encode(self, source: bytes, sequence: int, crypto: CryptoContext, transfer_id: int) -> bytes:
        if len(source) > self.chunk_size:
            raise ValueError("source chunk exceeds configured chunk size")
        transformed, compressed = self._compress(source)
        associated = transfer_id.to_bytes(8, "big") + sequence.to_bytes(4, "big")
        encrypted = crypto.seal(transformed, sequence, associated)
        return _CHUNK_HEADER.pack(_CHUNK_MAGIC, int(compressed), len(source), len(encrypted)) + encrypted

    def decode(self, payload: bytes, sequence: int, crypto: CryptoContext, transfer_id: int) -> bytes:
        if len(payload) < _CHUNK_HEADER.size:
            raise ValueError("chunk envelope is truncated")
        magic, compressed, source_len, encoded_len = _CHUNK_HEADER.unpack_from(payload)
        if magic != _CHUNK_MAGIC or compressed not in (0, 1) or encoded_len > len(payload) - _CHUNK_HEADER.size:
            raise ValueError("invalid chunk envelope")
        if source_len > self.chunk_size:
            raise ValueError("decoded chunk is too large")
        associated = transfer_id.to_bytes(8, "big") + sequence.to_bytes(4, "big")
        transformed = crypto.open(payload[_CHUNK_HEADER.size : _CHUNK_HEADER.size + encoded_len], sequence, associated)
        source = self._decompress(transformed, bool(compressed))
        if len(source) != source_len:
            raise ValueError("chunk source length mismatch")
        return source

    @property
    def max_payload(self) -> int:
        # ChaCha20-Poly1305 adds a 16-byte tag. The envelope is 12 bytes.
        return _CHUNK_HEADER.size + self.chunk_size + 16
