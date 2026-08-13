"""File-level transfer orchestration over protocol frames.

The sender performs two bounded-memory passes over the file: one for the final
hash and one for transmission. The receiver buffers at most one FEC group and
writes verified plaintext chunks directly to a temporary output file.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import secrets
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator

from backend.crypto import CryptoContext, context_from_metadata
from backend.fec import ReedSolomon
from backend.protocol import Frame, FrameType
from .chunks import ChunkCodec

_SYNC = b"AFT-SYNC-v1-robust-fsk"
_HANDSHAKE = b"AFT-HANDSHAKE-v1-one-way"
_FEC_HEADER = struct.Struct(">4sIBBI")  # magic, group, parity index, data count, shard size


@dataclass(frozen=True, slots=True)
class TransferSettings:
    chunk_size: int = 16 * 1024
    fec_overhead: int = 25
    fec_group_size: int = 16
    compression: str = "zstd"
    encrypt: bool = True

    def __post_init__(self) -> None:
        if self.fec_overhead not in {10, 20, 25, 30, 40}:
            raise ValueError("fec_overhead must be one of 10, 20, 25, 30, or 40")
        if not 1024 <= self.chunk_size <= 4 * 1024 * 1024:
            raise ValueError("chunk_size must be between 1 KiB and 4 MiB")
        if not 1 <= self.fec_group_size <= 64:
            raise ValueError("fec_group_size must be between 1 and 64")


def _parity_count(data_shards: int, overhead_percent: int) -> int:
    return max(1, min(127, (data_shards * overhead_percent + (100 - overhead_percent) - 1) // (100 - overhead_percent)))


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _safe_filename(value: str) -> str:
    name = Path(value).name
    return name if name not in {"", ".", ".."} else "received.bin"


@dataclass(slots=True)
class PreparedTransfer:
    path: Path
    metadata: dict
    crypto: CryptoContext
    codec: ChunkCodec
    rs: ReedSolomon
    transfer_id: int
    data_frames: int
    parity_frames: int
    total_frames: int

    def frames(self) -> Iterator[Frame]:
        yield Frame(self.transfer_id, FrameType.SYNC, 0, self.total_frames, _SYNC)
        yield Frame(self.transfer_id, FrameType.HANDSHAKE, 1, self.total_frames, _HANDSHAKE)
        yield Frame(self.transfer_id, FrameType.METADATA, 2, self.total_frames, json.dumps(self.metadata, separators=(",", ":")).encode())
        with self.path.open("rb") as handle:
            groups = (self.data_frames + self.rs.data_shards - 1) // self.rs.data_shards
            for group in range(groups):
                group_payloads: list[bytes] = []
                group_count = min(self.rs.data_shards, self.data_frames - group * self.rs.data_shards)
                for local in range(group_count):
                    data_index = group * self.rs.data_shards + local
                    source = handle.read(self.codec.chunk_size)
                    encoded = self.codec.encode(source, data_index, self.crypto, self.transfer_id)
                    payload = encoded.ljust(self.codec.max_payload, b"\0")
                    group_payloads.append(payload)
                    yield Frame(self.transfer_id, FrameType.DATA, 3 + data_index, self.total_frames, payload)
                for parity_index, shard in enumerate(self.rs.encode(group_payloads)):
                    payload = _FEC_HEADER.pack(b"FEC1", group, parity_index, group_count, self.codec.max_payload) + shard
                    parity_sequence = 3 + self.data_frames + group * self.rs.parity_shards + parity_index
                    yield Frame(self.transfer_id, FrameType.PARITY, parity_sequence, self.total_frames, payload)
        yield Frame(self.transfer_id, FrameType.END, self.total_frames - 1, self.total_frames, b"END-v1")


def prepare_transfer(path: str | Path, settings: TransferSettings, *, password: str | None = None, key: bytes | None = None, transfer_id: int | None = None) -> PreparedTransfer:
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    size = source_path.stat().st_size
    data_frames = (size + settings.chunk_size - 1) // settings.chunk_size if size else 1
    parity_count = _parity_count(settings.fec_group_size, settings.fec_overhead)
    groups = (data_frames + settings.fec_group_size - 1) // settings.fec_group_size
    transfer_id = transfer_id if transfer_id is not None else secrets.randbits(64)
    if password is not None:
        crypto = CryptoContext(password=password)
    elif key is not None:
        crypto = CryptoContext(key=key)
    else:
        crypto = CryptoContext()
    codec = ChunkCodec(settings.chunk_size, settings.compression)
    rs = ReedSolomon(settings.fec_group_size, parity_count)
    info = crypto.info()
    metadata = {
        "protocol_version": 1,
        "transfer_id": f"{transfer_id:016X}",
        "filename": source_path.name,
        "filesize": size,
        "mime_type": mimetypes.guess_type(source_path.name)[0] or "application/octet-stream",
        "chunk_size": settings.chunk_size,
        "total_chunks": data_frames,
        "hash_algorithm": "sha256",
        "file_hash": _file_hash(source_path),
        "compression": settings.compression,
        "encryption": info.enabled,
        "encryption_mode": info.mode,
        "encryption_salt": info.salt,
        "nonce_prefix": info.nonce_prefix,
        "fec_overhead": settings.fec_overhead,
        "fec_data_shards": settings.fec_group_size,
        "fec_parity_shards": parity_count,
        "total_groups": groups,
    }
    parity_frames = groups * parity_count
    return PreparedTransfer(source_path, metadata, crypto, codec, rs, transfer_id, data_frames, parity_frames, 4 + data_frames + parity_frames)


@dataclass(slots=True)
class ReceiveResult:
    output_path: Path
    metadata: dict
    hash_verified: bool
    frames_recovered: int = 0
    frames_corrupted: int = 0


class StreamingReceiver:
    """Consume frames as they arrive without buffering the whole received file."""

    def __init__(self, output_dir: str | Path, *, password: str | None = None, key: bytes | None = None) -> None:
        self.output_dir = Path(output_dir)
        self.password, self.key = password, key
        self.metadata: dict | None = None
        self.transfer_id: int | None = None
        self.codec: ChunkCodec | None = None
        self.crypto: CryptoContext | None = None
        self.rs: ReedSolomon | None = None
        self._groups: dict[int, dict[int, bytes]] = {}
        self._group_counts: dict[int, int] = {}
        self._finished_groups: set[int] = set()
        self._temporary: BinaryIO | None = None
        self._temp_path: Path | None = None
        self._hash = hashlib.sha256()
        self.frames_recovered = 0
        self.frames_corrupted = 0
        self._next_group = 0

    def _start(self, frame: Frame) -> None:
        self.metadata = json.loads(frame.payload.decode())
        if self.metadata.get("transfer_id") != f"{frame.transfer_id:016X}":
            raise ValueError("metadata transfer id mismatch")
        self.transfer_id = frame.transfer_id
        self.codec = ChunkCodec(int(self.metadata["chunk_size"]), self.metadata.get("compression", "none"))
        self.crypto = context_from_metadata(self.metadata, password=self.password, key=self.key)
        self.rs = ReedSolomon(int(self.metadata["fec_data_shards"]), int(self.metadata["fec_parity_shards"]))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.NamedTemporaryFile(dir=self.output_dir, prefix=".audio-transfer-", delete=False)
        self._temp_path = Path(self._temporary.name)

    def accept(self, frame: Frame) -> None:
        if frame.frame_type is FrameType.METADATA:
            self._start(frame)
            return
        if self.metadata is None or frame.transfer_id != self.transfer_id:
            return
        if frame.frame_type is FrameType.DATA:
            index = frame.sequence - 3
            if self.rs and self.codec and 0 <= index < int(self.metadata["total_chunks"]) and len(frame.payload) == self.codec.max_payload:
                group, local = divmod(index, self.rs.data_shards)
                self._groups.setdefault(group, {})[local] = frame.payload
                self._maybe_finish(group)
            else:
                self.frames_corrupted += 1
        elif frame.frame_type is FrameType.PARITY:
            if len(frame.payload) < _FEC_HEADER.size or not self.rs or not self.codec:
                self.frames_corrupted += 1
                return
            magic, group, parity_index, group_count, shard_size = _FEC_HEADER.unpack_from(frame.payload)
            if magic != b"FEC1" or shard_size != self.codec.max_payload or len(frame.payload) != _FEC_HEADER.size + shard_size:
                self.frames_corrupted += 1
                return
            self._groups.setdefault(group, {})[self.rs.data_shards + parity_index] = frame.payload[_FEC_HEADER.size:]
            self._group_counts[group] = group_count
            self._maybe_finish(group)

    def _maybe_finish(self, group: int) -> None:
        if group != self._next_group or group in self._finished_groups or not self.rs or not self.metadata:
            return
        count = self._group_counts.get(group, min(self.rs.data_shards, int(self.metadata["total_chunks"]) - group * self.rs.data_shards))
        # Once all parity packets for a group have arrived, it is safe to
        # decode and release the group. At END, finalize() handles omissions.
        if sum(1 for index in self._groups.get(group, {}) if index >= self.rs.data_shards) >= self.rs.parity_shards:
            self._finish_group(group, count)

    def _finish_group(self, group: int, count: int) -> None:
        if group in self._finished_groups or not self.rs or not self.codec or self.transfer_id is None or self._temporary is None:
            return
        shards = self._groups.get(group, {})
        missing = [local for local in range(count) if local not in shards]
        if missing:
            recovered = self.rs.decode(shards, self.codec.max_payload, data_count=count)
            self.frames_recovered += sum(1 for local in missing if local in recovered)
            shards.update(recovered)
        for local in range(count):
            if local not in shards:
                raise ValueError(f"unrecoverable FEC group {group}")
            value = self.codec.decode(shards[local], group * self.rs.data_shards + local, self.crypto, self.transfer_id)
            self._temporary.write(value)
            self._hash.update(value)
        self._temporary.flush()
        self._finished_groups.add(group)
        self._next_group = group + 1
        self._groups.pop(group, None)
        self._group_counts.pop(group, None)

    def finalize(self) -> ReceiveResult:
        if self.metadata is None or self._temporary is None or self._temp_path is None or not self.rs:
            raise ValueError("no transfer metadata received")
        for group in range(int(self.metadata["total_groups"])):
            count = min(self.rs.data_shards, int(self.metadata["total_chunks"]) - group * self.rs.data_shards)
            self._finish_group(group, count)
        self._temporary.flush()
        os.fsync(self._temporary.fileno())
        self._temporary.close()
        actual = self._hash.hexdigest()
        if actual != self.metadata["file_hash"]:
            self._temp_path.unlink(missing_ok=True)
            raise ValueError("final SHA-256 hash verification failed")
        output = self.output_dir / _safe_filename(self.metadata["filename"])
        os.replace(self._temp_path, output)
        return ReceiveResult(output, self.metadata, True, self.frames_recovered, self.frames_corrupted)


def receive_frames(frames: Iterable[Frame], output_dir: str | Path, *, password: str | None = None, key: bytes | None = None) -> ReceiveResult:
    receiver = StreamingReceiver(output_dir, password=password, key=key)
    for frame in frames:
        receiver.accept(frame)
    return receiver.finalize()
