"""
Transfer Manager - orchestrates the complete file transfer pipeline.

This module ties together all the components:
- Protocol (packet structure)
- DSP (modulation/demodulation)
- FEC (error correction)
- Crypto (encryption)
- Audio I/O

It provides high-level transmit() and receive() operations.
"""

import os
import time
import struct
import hashlib
import zlib
import math
import numpy as np
from pathlib import Path
from typing import Optional, Callable, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

from ..protocol.packet import (
    Frame, FrameType, ProtocolConfig, ProtocolError,
    deserialize_frame,
    encode_metadata_payload, decode_metadata_payload,
    encode_handshake_payload, decode_handshake_payload,
    MAGIC, PROTOCOL_VERSION,
)
from ..dsp.modulation import FSKModulator
from ..dsp.demodulation import FSKDemodulator
from ..dsp.synchronization import SyncDetector
from ..dsp.spectrum import SpectrumAnalyzer
from ..fec.reed_solomon import ReedSolomonFEC
from ..crypto.encryption import CryptoEngine


class TransferState(Enum):
    """State of a file transfer."""
    IDLE = "idle"
    PREPARING = "preparing"
    TRANSMITTING = "transmitting"
    RECEIVING = "receiving"
    COMPLETE = "complete"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class TransferInfo:
    """Information about a file transfer."""
    transfer_id: int = 0
    filename: str = ""
    filesize: int = 0
    mime_type: str = ""
    chunk_size: int = 4096
    total_chunks: int = 0
    hash_algorithm: str = "sha256"
    file_hash: str = ""
    compression_enabled: bool = True
    encryption_enabled: bool = False
    fec_overhead: float = 0.25
    fec_enabled: bool = True
    fec_algorithm: str = "reed-solomon"
    protocol_version: int = PROTOCOL_VERSION
    source_path: Optional[str] = None


@dataclass
class TransferProgress:
    """Progress information for a transfer."""
    state: TransferState = TransferState.IDLE
    frames_sent: int = 0
    frames_received: int = 0
    total_frames: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    total_bytes: int = 0
    speed_bps: float = 0.0  # bytes per second
    elapsed_time: float = 0.0
    estimated_remaining: float = 0.0
    frames_corrupted: int = 0
    frames_recovered: int = 0
    symbol_errors: int = 0
    current_symbol: int = 0
    signal_level: float = 0.0
    snr_db: float = 0.0
    fec_overhead: float = 0.25
    transfer_info: Optional[TransferInfo] = None


class TransferManager:
    """
    High-level transfer manager for transmitter and receiver.

    Usage (Transmitter):
        manager = TransferManager(mode="transmitter")
        manager.configure(config)
        manager.load_file("path/to/file")
        audio_data = manager.start_transmission()

    Usage (Receiver):
        manager = TransferManager(mode="receiver")
        manager.configure(config)
        result = manager.process_audio(audio_chunk)
        if result.complete:
            save_file(result.data, output_path)
    """

    def __init__(self, mode: str = "transmitter"):
        """
        Args:
            mode: "transmitter" or "receiver"
        """
        if mode not in ("transmitter", "receiver"):
            raise ValueError("Mode must be 'transmitter' or 'receiver'")

        self.mode = mode
        self.config = ProtocolConfig()
        self.state = TransferState.IDLE
        self.progress = TransferProgress()
        self.transfer_info = TransferInfo()

        # Components (initialized when config is set)
        self.modulator: Optional[FSKModulator] = None
        self.demodulator: Optional[FSKDemodulator] = None
        self.sync_detector: Optional[SyncDetector] = None
        self.spectrum: Optional[SpectrumAnalyzer] = None
        self.fec: Optional[ReedSolomonFEC] = None
        self.crypto: Optional[CryptoEngine] = None

        # Data buffers
        self._file_data: bytes = b""
        self._file_path: Optional[str] = None
        self._frame_buffer: List[bytes] = []
        self._received_frames: dict = {}
        self._audio_buffer: bytearray = bytearray()
        self._audio_offset_bytes = 0
        self._stream_started = False
        self._pending_frame_samples: Optional[int] = None
        self._transfer_id: Optional[int] = None
        self._expected_total_frames: Optional[int] = None
        self._cancel_requested = False
        self._max_audio_buffer_samples = 60 * self.config.sample_rate

        # Callbacks
        self.on_progress: Optional[Callable[[TransferProgress], None]] = None
        self.on_complete: Optional[Callable[[TransferInfo], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None

    def configure(self, config: ProtocolConfig) -> None:
        """Configure the transfer with protocol parameters."""
        config.validate()
        self.config = config
        self.state = TransferState.IDLE
        self._file_data = b""
        self._frame_buffer.clear()
        self._received_frames.clear()
        self._audio_buffer.clear()
        self._audio_offset_bytes = 0
        self._stream_started = False
        self._pending_frame_samples = None
        self._transfer_id = None
        self._expected_total_frames = None
        self._cancel_requested = False
        self.transfer_info = TransferInfo()
        self.progress = TransferProgress()
        self._max_audio_buffer_samples = 60 * config.sample_rate

        # Initialize DSP components
        self.modulator = FSKModulator(
            sample_rate=config.sample_rate,
            frequencies=config.frequencies,
            symbol_rate=config.symbol_rate,
            amplitude=0.8,
        )

        self.demodulator = FSKDemodulator(
            sample_rate=config.sample_rate,
            frequencies=config.frequencies,
            symbol_rate=config.symbol_rate,
        )

        self.sync_detector = SyncDetector(
            sample_rate=config.sample_rate,
            freq_a=config.frequencies[0],
            freq_b=config.frequencies[1] if len(config.frequencies) > 1 else config.frequencies[0],
            symbol_rate=config.symbol_rate,
        )

        self.spectrum = SpectrumAnalyzer(
            sample_rate=config.sample_rate,
            fft_size=4096,
        )

        # Initialize FEC
        if config.fec_enabled and config.fec_algorithm != "reed-solomon":
            raise ProtocolError(
                "Python TransferManager supports only Reed-Solomon FEC"
            )
        nsym = ReedSolomonFEC.overhead_to_nsym(config.fec_overhead)
        self.fec = ReedSolomonFEC(nsym=nsym)

        # Encryption is intentionally injected separately so keys never enter
        # protocol metadata. Never retain a key across configuration changes.
        self.crypto = None
        self._compressor = None
        self._decompressor = None
        if config.compression_enabled:
            import zstandard as zstd
            self._compressor = zstd.ZstdCompressor()
            self._decompressor = zstd.ZstdDecompressor()

    def load_file(self, file_path: str) -> TransferInfo:
        """
        Load a file for transmission.

        For large files, we read in chunks rather than loading entirely.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if not path.is_file():
            raise ValueError(f"Not a regular file: {file_path}")
        self._file_path = str(path)
        self.state = TransferState.IDLE
        self.progress = TransferProgress()
        self._cancel_requested = False
        # A transfer ID is allocated when the file is selected so API clients
        # can receive the real ID before audio preparation begins.
        self._file_data = b""
        self._received_frames.clear()
        self._frame_buffer.clear()
        transfer_id = Frame(frame_type=FrameType.SYNC).transfer_id

        # Get file info
        file_size = path.stat().st_size
        mime_type = self._guess_mime(path.suffix)
        file_hash = CryptoEngine.compute_file_hash_streaming(file_path)

        # Calculate chunking
        # Keep encoded data frames below the protocol payload limit. The
        # default UI chunk size is larger than one RS frame can carry.
        chunk_size = min(self.config.chunk_size, 128)
        total_chunks = max(1, (file_size + chunk_size - 1) // chunk_size)

        self.transfer_info = TransferInfo(
            transfer_id=transfer_id,
            filename=path.name,
            filesize=file_size,
            mime_type=mime_type,
            chunk_size=chunk_size,
            total_chunks=total_chunks,
            file_hash=file_hash,
            compression_enabled=self.config.compression_enabled,
            encryption_enabled=self.config.encryption_enabled,
            fec_algorithm=self.config.fec_algorithm if self.config.fec_enabled else "none",
            source_path=str(path),
        )

        self.progress.total_bytes = file_size
        self.progress.transfer_info = self.transfer_info

        return self.transfer_info

    def _guess_mime(self, extension: str) -> str:
        """Guess MIME type from file extension."""
        mime_map = {
            ".txt": "text/plain",
            ".pdf": "application/pdf",
            ".zip": "application/zip",
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".mp4": "video/mp4",
            ".mp3": "audio/mpeg",
        }
        return mime_map.get(extension.lower(), "application/octet-stream")

    def prepare_transmission(self) -> List[bytes]:
        """
        Prepare the complete transmission as a list of frames.

        Returns:
            List of serialized frames ready for audio modulation.
        """
        self.state = TransferState.PREPARING
        frames = []

        # Frame count is part of the stream contract and is repeated in every
        # frame header so receivers can reject mixed or truncated streams.
        total_frames = self.transfer_info.total_chunks + 4

        # 1. Sync frame
        transfer_id = self.transfer_info.transfer_id or Frame(frame_type=FrameType.SYNC).transfer_id
        sync_frame = Frame(
            frame_type=FrameType.SYNC,
            transfer_id=transfer_id,
            total_frames=total_frames,
        )
        self.transfer_info.transfer_id = transfer_id
        frames.append(sync_frame.serialize())

        # 2. Handshake frame (protocol config)
        handshake_payload = encode_handshake_payload(self.config)
        handshake_frame = Frame(
            frame_type=FrameType.HANDSHAKE,
            transfer_id=self.transfer_info.transfer_id,
            total_frames=total_frames,
            payload=handshake_payload,
        )
        frames.append(handshake_frame.serialize())

        # 3. Metadata frame
        metadata_payload = encode_metadata_payload(
            filename=self.transfer_info.filename,
            filesize=self.transfer_info.filesize,
            mime_type=self.transfer_info.mime_type,
            chunk_size=self.transfer_info.chunk_size,
            total_chunks=self.transfer_info.total_chunks,
            hash_algorithm=self.transfer_info.hash_algorithm,
            file_hash=self.transfer_info.file_hash,
            compression_enabled=self.transfer_info.compression_enabled,
            encryption_enabled=self.transfer_info.encryption_enabled,
            fec_overhead=self.config.fec_overhead if self.config.fec_enabled else 0.0,
            fec_enabled=self.config.fec_enabled,
            fec_algorithm=self.transfer_info.fec_algorithm,
        )
        metadata_frame = Frame(
            frame_type=FrameType.METADATA,
            transfer_id=self.transfer_info.transfer_id,
            total_frames=total_frames,
            payload=metadata_payload,
        )
        frames.append(metadata_frame.serialize())

        # Data frames are added by _create_data_frames() so this method is
        # also useful on its own and never opens an implicit empty path.
        frames.extend(self._create_data_frames(total_frames))

        # 4. End frame. total_frames includes SYNC, HANDSHAKE, METADATA,
        # every DATA frame, and END itself.
        end_frame = Frame(
            frame_type=FrameType.END,
            transfer_id=self.transfer_info.transfer_id,
            sequence_number=0,
            total_frames=total_frames,
        )
        frames.append(end_frame.serialize())

        self.progress.total_frames = len(frames)
        return frames

    def modulate_frames(self, frames: List[bytes]) -> np.ndarray:
        """
        Convert frames to audio waveform.
        """
        all_audio = []

        # One synchronization prefix per transfer, followed by contiguous
        # frame bytes. This is shared with the browser implementation.
        prefix = self.modulator.add_sync_tone(
            self.modulator.add_preamble(
                np.zeros(0, dtype=np.float32),
                num_symbols=self.config.sync_preamble_symbols,
            ),
            frequency=self.config.sync_frequency,
        )
        all_audio.append(prefix)
        for frame_data in frames:
            all_audio.append(self.modulator.modulate_bytes(frame_data))

        return np.concatenate(all_audio)

    def start_transmission(self) -> np.ndarray:
        """
        Start file transmission and return the audio waveform.

        Returns:
            Complete audio waveform for the entire file transfer.
        """
        if self.mode != "transmitter":
            raise RuntimeError("Only a transmitter can start transmission")
        if self._cancel_requested:
            raise RuntimeError("Transfer was cancelled")
        if self._file_path is None and not self._file_data:
            raise RuntimeError("No file has been loaded")
        if self.config.encryption_enabled and self.crypto is None:
            raise RuntimeError("Encryption is enabled but no crypto key is configured")

        self.state = TransferState.TRANSMITTING
        self.progress.state = TransferState.TRANSMITTING

        if not self._file_data and self._file_path:
            with open(self._file_path, "rb") as f:
                self._file_data = f.read()

        # prepare_transmission creates the complete frame sequence.
        frames = self.prepare_transmission()
        if self._cancel_requested:
            self.state = TransferState.CANCELLED
            self.progress.state = TransferState.CANCELLED
            raise RuntimeError("Transfer was cancelled")
        audio = self.modulate_frames(frames)

        self.progress.frames_sent = len(frames)
        self.progress.bytes_sent = self.transfer_info.filesize
        self.state = TransferState.COMPLETE
        return audio

    def _create_data_frames(self, total_frames: Optional[int] = None) -> List[bytes]:
        """Create data frames from file chunks."""
        frames = []
        seq_num = 0
        total_frames = total_frames or self.transfer_info.total_chunks + 4

        if self.config.encryption_enabled and self.crypto is None:
            raise RuntimeError("Encryption is enabled but no crypto key is configured")

        for i in range(self.transfer_info.total_chunks):
            start = i * self.transfer_info.chunk_size
            end = min(start + self.transfer_info.chunk_size, self.transfer_info.filesize)
            chunk = self._file_data[start:end]

            # Optional compression
            if self.config.compression_enabled and chunk:
                chunk = self._compressor.compress(chunk)

            # Optional encryption
            if self.crypto:
                chunk = self.crypto.encrypt_chunk(chunk, i)

            # FEC encode
            if self.config.fec_enabled:
                chunk = self.fec.encode(chunk)

            # Create frame
            frame = Frame(
                frame_type=FrameType.DATA,
                transfer_id=self.transfer_info.transfer_id,
                sequence_number=seq_num,
                total_frames=total_frames,
                payload=chunk,
            )
            frames.append(frame.serialize())
            seq_num += 1

        return frames

    def process_audio_chunk(self, audio_chunk: np.ndarray) -> Optional[dict]:
        """
        Process incoming audio (receiver mode).

        Returns:
            Progress info dict, or None if processing continues.
        """
        if self.mode != "receiver":
            raise RuntimeError("Only a receiver can process audio")
        if self.state in (TransferState.COMPLETE, TransferState.CANCELLED, TransferState.ERROR):
            return None
        if self.demodulator is None:
            raise RuntimeError("Transfer manager is not configured")

        self.state = TransferState.RECEIVING
        self.progress.state = TransferState.RECEIVING
        audio_array = np.asarray(audio_chunk, dtype=np.float32)
        if audio_array.ndim != 1:
            raise ValueError("audio_chunk must be a one-dimensional array")
        if self._cancel_requested:
            self.state = TransferState.CANCELLED
            self.progress.state = TransferState.CANCELLED
            return {"type": "CANCELLED", "processed": False}
        self._audio_buffer.extend(audio_array.tobytes())
        available_buffer_samples = (len(self._audio_buffer) - self._audio_offset_bytes) // 4
        if available_buffer_samples > self._max_audio_buffer_samples:
            self.state = TransferState.ERROR
            self.progress.state = TransferState.ERROR
            raise ProtocolError("audio buffer exceeded the maximum allowed size")
        results = []
        prefix_samples = int(self.config.sample_rate * 0.5) + self.config.sync_preamble_symbols * self.config.samples_per_symbol()
        header_samples = math.ceil(20 * 8 / self.config.bits_per_symbol) * self.config.samples_per_symbol()

        while True:
            available_bytes = len(self._audio_buffer) - self._audio_offset_bytes
            available_samples = available_bytes // 4
            audio_array = np.frombuffer(
                self._audio_buffer,
                dtype=np.float32,
                count=available_samples,
                offset=self._audio_offset_bytes,
            )
            required_prefix = 0 if self._stream_started else prefix_samples
            if available_samples < required_prefix + header_samples:
                break

            data_start = required_prefix
            if self._pending_frame_samples is None:
                header_audio = audio_array[data_start:data_start + header_samples]
                header_data = self.demodulator.demodulate_to_bytes(header_audio)[:20]
                if len(header_data) < 20 or header_data[:4] != MAGIC:
                    # A live stream may begin mid-frame. Drop one symbol and
                    # continue searching rather than permanently wedging.
                    self._audio_offset_bytes += self.config.samples_per_symbol() * 4
                    continue

                payload_len = int.from_bytes(header_data[18:20], "big")
                if payload_len > 2048:
                    self._audio_offset_bytes += self.config.samples_per_symbol() * 4
                    continue
                frame_samples = math.ceil(
                    (20 + payload_len + 2) * 8 / self.config.bits_per_symbol
                ) * self.config.samples_per_symbol()
                self._pending_frame_samples = required_prefix + frame_samples

            total_samples = self._pending_frame_samples
            if available_samples < total_samples:
                break

            frame_audio = audio_array[data_start:total_samples]
            frame_data = self.demodulator.demodulate_to_bytes(frame_audio)
            frame = deserialize_frame(frame_data)
            if frame is None:
                self._pending_frame_samples = None
                self._audio_offset_bytes += self.config.samples_per_symbol() * 4
                continue

            consumed_samples = total_samples
            self._audio_offset_bytes += consumed_samples * 4
            self._stream_started = True
            self._pending_frame_samples = None
            try:
                processed = self._process_frame(frame)
            except Exception:
                self.state = TransferState.ERROR
                self.progress.state = TransferState.ERROR
                raise
            results.append(processed)
            if processed.get("complete"):
                self.state = TransferState.COMPLETE
                self.progress.state = TransferState.COMPLETE

        # Compact only after all numpy views have gone out of scope. This
        # avoids shifting the buffer on every frame while bounding retained
        # memory for long-running receivers.
        audio_array = None
        header_audio = None
        frame_audio = None
        if self._audio_offset_bytes > 1_048_576 and self._audio_offset_bytes * 2 > len(self._audio_buffer):
            del self._audio_buffer[:self._audio_offset_bytes]
            self._audio_offset_bytes = 0

        if results:
            return results[-1]
        return None

    def _process_frame(self, frame: Frame) -> dict:
        """Process a received frame."""
        if frame.frame_type == FrameType.SYNC:
            if self._transfer_id is not None and frame.transfer_id != self._transfer_id:
                raise ProtocolError("transfer ID changed mid-stream")
            if frame.total_frames < 4:
                raise ProtocolError("SYNC declares an invalid total frame count")
            self._transfer_id = frame.transfer_id
            self._expected_total_frames = frame.total_frames
        elif self._expected_total_frames is not None and frame.total_frames != self._expected_total_frames:
            raise ProtocolError("frame total count changed mid-stream")
        elif self._transfer_id is None:
            raise ProtocolError("received frame before synchronization")
        elif frame.transfer_id != self._transfer_id:
            raise ProtocolError("frame belongs to a different transfer")

        result = {"type": frame.frame_type.name, "processed": True}

        if frame.frame_type == FrameType.HANDSHAKE:
            handshake = decode_handshake_payload(frame.payload)
            expected = {
                "sample_rate": self.config.sample_rate,
                "symbol_rate": self.config.symbol_rate,
                "bits_per_symbol": self.config.bits_per_symbol,
                "frequencies": self.config.frequencies,
                "sync_preamble_symbols": self.config.sync_preamble_symbols,
                "sync_frequency": self.config.sync_frequency,
                "fec_overhead": self.config.fec_overhead if self.config.fec_enabled else 0.0,
                "fec_algorithm": self.config.fec_algorithm if self.config.fec_enabled else "none",
                "fec_enabled": self.config.fec_enabled,
            }
            for name, value in expected.items():
                if handshake[name] != value:
                    raise ProtocolError(
                        f"handshake {name} does not match receiver configuration"
                    )
            result["parsed"] = handshake


        elif frame.frame_type == FrameType.METADATA:
            # Parse metadata
            metadata = decode_metadata_payload(frame.payload)
            required_metadata = {
                "filesize", "chunk_size", "total_chunks", "filename",
                "mime_type", "hash_algorithm", "file_hash",
                "compression_enabled", "encryption_enabled", "fec_enabled",
                "fec_overhead", "fec_algorithm",
            }
            if not required_metadata.issubset(metadata):
                raise ProtocolError("metadata is incomplete")
            if metadata["hash_algorithm"].lower() not in {"sha256", "sha-256"}:
                raise ProtocolError("only SHA-256 file hashes are supported")
            if metadata["fec_algorithm"] not in {"none", "reed-solomon"}:
                raise ProtocolError(
                    f"unsupported FEC algorithm for Python receiver: {metadata['fec_algorithm']}"
                )
            if metadata["fec_enabled"] != (metadata["fec_algorithm"] != "none"):
                raise ProtocolError("metadata FEC state and algorithm do not agree")
            self.transfer_info = TransferInfo(
                transfer_id=self._transfer_id,
                filename=metadata["filename"],
                filesize=metadata["filesize"],
                mime_type=metadata["mime_type"],
                chunk_size=metadata["chunk_size"],
                total_chunks=metadata["total_chunks"],
                hash_algorithm=metadata["hash_algorithm"],
                file_hash=metadata["file_hash"],
                compression_enabled=metadata["compression_enabled"],
                encryption_enabled=metadata["encryption_enabled"],
                fec_overhead=metadata["fec_overhead"],
                fec_enabled=metadata["fec_enabled"],
                fec_algorithm=metadata["fec_algorithm"],
            )
            if self.transfer_info.chunk_size < 1 or self.transfer_info.chunk_size > 1_048_576:
                raise ProtocolError("metadata chunk size is invalid")
            expected_chunks = max(
                1,
                math.ceil(self.transfer_info.filesize / self.transfer_info.chunk_size),
            )
            if self.transfer_info.total_chunks != expected_chunks:
                raise ProtocolError("metadata chunk count is inconsistent")
            if "fec_enabled" in metadata:
                self.config.fec_enabled = metadata["fec_enabled"]
            self.config.fec_algorithm = metadata["fec_algorithm"]
            self.config.compression_enabled = self.transfer_info.compression_enabled
            self.config.encryption_enabled = self.transfer_info.encryption_enabled
            if self.config.encryption_enabled and self.crypto is None:
                raise ProtocolError("encrypted transfer requires a receiver key")
            if self.transfer_info.compression_enabled and self._decompressor is None:
                import zstandard as zstd
                self._decompressor = zstd.ZstdDecompressor()
            if "fec_overhead" in metadata:
                self.config.fec_overhead = metadata["fec_overhead"]
                if self.config.fec_enabled:
                    if self.config.fec_algorithm != "reed-solomon":
                        raise ProtocolError(
                            "Python receiver cannot decode non-Reed-Solomon FEC"
                        )
                    self.fec = ReedSolomonFEC(
                        nsym=ReedSolomonFEC.overhead_to_nsym(self.config.fec_overhead)
                    )
            if self.transfer_info.total_chunks < 1:
                raise ProtocolError("metadata declares no data chunks")
            result["metadata"] = metadata

        elif frame.frame_type == FrameType.DATA:
            # Store data frame, rejecting duplicates and impossible sequence
            # numbers instead of allowing mixed/stale streams.
            if self.transfer_info.total_chunks and not 0 <= frame.sequence_number < self.transfer_info.total_chunks:
                raise ProtocolError("data frame sequence is out of range")
            if frame.sequence_number in self._received_frames:
                raise ProtocolError("duplicate data frame")
            self._received_frames[frame.sequence_number] = frame.payload
            self.progress.frames_received += 1

        elif frame.frame_type == FrameType.END:
            if self._expected_total_frames is not None and frame.total_frames != self._expected_total_frames:
                raise ProtocolError("END total frame count is invalid")
            if self.transfer_info.total_chunks < 1 or len(self._received_frames) != self.transfer_info.total_chunks:
                raise ProtocolError("END received before all data frames")
            result["complete"] = True

        return result

    def cancel(self) -> None:
        """Request cancellation of the active transfer."""
        self._cancel_requested = True
        self.state = TransferState.CANCELLED
        self.progress.state = TransferState.CANCELLED

    def get_received_file(self) -> Tuple[bytes, bool]:
        """
        Reassemble and verify the received file.

        Returns:
            Tuple of (file_data, verified)
        """
        # Reassemble frames in order. Missing frames are an integrity failure,
        # not something to silently skip.
        expected = self.transfer_info.total_chunks
        missing = [i for i in range(expected) if i not in self._received_frames]
        if missing:
            return b"", False

        data = bytearray()
        for i in range(expected):
            chunk = self._received_frames[i]

            if self.config.fec_enabled:
                try:
                    chunk, stats = self.fec.decode(chunk)
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"FEC decode failed for chunk {i}") from exc
                if stats.get("uncorrectable_errors"):
                    raise ValueError(f"uncorrectable FEC error in chunk {i}")

            if self.crypto:
                try:
                    chunk = self.crypto.decrypt_chunk(chunk, i)
                except ValueError as exc:
                    raise ValueError(f"decryption failed for chunk {i}") from exc

            if self.config.compression_enabled and chunk:
                try:
                    chunk = self._decompressor.decompress(chunk)
                except Exception as exc:
                    raise ValueError(f"decompression failed for chunk {i}") from exc

            data.extend(chunk)

        data = bytes(data[:self.transfer_info.filesize])
        hash_algorithm = self.transfer_info.hash_algorithm.lower()
        if hash_algorithm in {"sha256", "sha-256"}:
            computed_hash = hashlib.sha256(data).hexdigest()
        else:
            raise ValueError(f"unsupported hash algorithm: {self.transfer_info.hash_algorithm}")
        verified = (
            len(data) == self.transfer_info.filesize
            and computed_hash == self.transfer_info.file_hash
        )
        return data, verified
