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
from pathlib import Path
from typing import Optional, Callable, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

from ..protocol.packet import (
    Frame, FrameType, ProtocolConfig,
    deserialize_frame,
    encode_metadata_payload, decode_metadata_payload,
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
    protocol_version: int = PROTOCOL_VERSION


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
        self._frame_buffer: List[bytes] = []
        self._received_frames: dict = {}
        self._audio_buffer: bytearray = bytearray()

        # Callbacks
        self.on_progress: Optional[Callable[[TransferProgress], None]] = None
        self.on_complete: Optional[Callable[[TransferInfo], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None

    def configure(self, config: ProtocolConfig) -> None:
        """Configure the transfer with protocol parameters."""
        self.config = config

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
        nsym = ReedSolomonFEC.overhead_to_nsym(config.fec_overhead)
        self.fec = ReedSolomonFEC(nsym=nsym)

        # Encryption (optional)
        if config.encryption_enabled:
            # Key must be provided externally
            pass

    def load_file(self, file_path: str) -> TransferInfo:
        """
        Load a file for transmission.

        For large files, we read in chunks rather than loading entirely.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Get file info
        file_size = path.stat().st_size
        mime_type = self._guess_mime(path.suffix)
        file_hash = CryptoEngine.compute_file_hash_streaming(file_path)

        # Calculate chunking
        chunk_size = self.config.chunk_size
        total_chunks = (file_size + chunk_size - 1) // chunk_size

        self.transfer_info = TransferInfo(
            filename=path.name,
            filesize=file_size,
            mime_type=mime_type,
            chunk_size=chunk_size,
            total_chunks=total_chunks,
            file_hash=file_hash,
            compression_enabled=self.config.compression_enabled,
            encryption_enabled=self.config.encryption_enabled,
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

        # 1. Sync frame
        sync_frame = Frame(frame_type=FrameType.SYNC)
        self.transfer_info.transfer_id = sync_frame.transfer_id
        frames.append(sync_frame.serialize())

        # 2. Handshake frame (protocol config)
        handshake_payload = struct.pack(
            ">B I B B B",
            self.config.sample_rate // 1000,  # Sample rate in kHz
            self.config.symbol_rate,
            self.config.bits_per_symbol,
            len(self.config.frequencies),
            int(self.config.fec_overhead * 100),  # FEC as percentage
        )
        handshake_frame = Frame(
            frame_type=FrameType.HANDSHAKE,
            transfer_id=self.transfer_info.transfer_id,
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
        )
        metadata_frame = Frame(
            frame_type=FrameType.METADATA,
            transfer_id=self.transfer_info.transfer_id,
            payload=metadata_payload,
        )
        frames.append(metadata_frame.serialize())

        # 4. Data frames
        with open(self.transfer_info.file_path if hasattr(self.transfer_info, 'file_path') else "", "rb") as f:
            # Actually, we need to read from the file
            # For now, use loaded data
            pass

        # 5. End frame
        end_frame = Frame(
            frame_type=FrameType.END,
            transfer_id=self.transfer_info.transfer_id,
            total_frames=len(frames) + 1,
        )
        frames.append(end_frame.serialize())

        self.progress.total_frames = len(frames)
        return frames

    def modulate_frames(self, frames: List[bytes]) -> np.ndarray:
        """
        Convert frames to audio waveform.
        """
        all_audio = []

        for frame_data in frames:
            # Add sync preamble
            frame_audio = self.modulator.add_sync_tone(
                self.modulator.add_preamble(
                    self.modulator.modulate_bytes(frame_data)
                )
            )
            all_audio.append(frame_audio)

        return np.concatenate(all_audio)

    def start_transmission(self) -> np.ndarray:
        """
        Start file transmission and return the audio waveform.

        Returns:
            Complete audio waveform for the entire file transfer.
        """
        self.state = TransferState.TRANSMITTING
        self.progress.state = TransferState.TRANSMITTING

        # Load file data
        if not self._file_data and hasattr(self, '_file_path'):
            with open(self._file_path, "rb") as f:
                self._file_data = f.read()

        # Prepare frames
        frames = self.prepare_transmission()

        # Add data frames
        data_frames = self._create_data_frames()
        frames = frames[:-1] + data_frames + [frames[-1]]  # Insert before END

        # Modulate to audio
        audio = self.modulate_frames(frames)

        self.state = TransferState.COMPLETE
        return audio

    def _create_data_frames(self) -> List[bytes]:
        """Create data frames from file chunks."""
        frames = []
        seq_num = 0

        for i in range(self.transfer_info.total_chunks):
            start = i * self.config.chunk_size
            end = min(start + self.config.chunk_size, self.transfer_info.filesize)
            chunk = self._file_data[start:end]

            # Optional compression
            if self.config.compression_enabled:
                import zstandard as zstd
                compressor = zstd.ZstdCompressor()
                chunk = compressor.compress(chunk)

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
                total_frames=self.transfer_info.total_chunks,
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
        self._audio_buffer.extend(audio_chunk.tobytes())

        # Try to detect frame synchronization
        audio_array = np.frombuffer(bytes(self._audio_buffer), dtype=np.float32)

        result = self.sync_detector.find_frame_start(audio_array)
        if result is None:
            return None

        data_start, samples_per_sym = result

        # Demodulate frames
        frame_data = self.demodulator.demodulate_to_bytes(audio_array, data_start)

        # Try to deserialize frame
        frame = deserialize_frame(frame_data)
        if frame is None:
            return None

        # Process frame based on type
        return self._process_frame(frame)

    def _process_frame(self, frame: Frame) -> dict:
        """Process a received frame."""
        result = {"type": frame.frame_type.name, "processed": True}

        if frame.frame_type == FrameType.HANDSHAKE:
            # Parse handshake
            result["parsed"] = True

        elif frame.frame_type == FrameType.METADATA:
            # Parse metadata
            metadata = decode_metadata_payload(frame.payload)
            self.transfer_info = TransferInfo(**{
                k: v for k, v in metadata.items()
                if hasattr(TransferInfo, k)
            })
            result["metadata"] = metadata

        elif frame.frame_type == FrameType.DATA:
            # Store data frame
            self._received_frames[frame.sequence_number] = frame.payload
            self.progress.frames_received += 1

        elif frame.frame_type == FrameType.END:
            result["complete"] = True

        return result

    def get_received_file(self) -> Tuple[bytes, bool]:
        """
        Reassemble and verify the received file.

        Returns:
            Tuple of (file_data, verified)
        """
        # Reassemble frames in order
        data = bytearray()
        for i in range(len(self._received_frames)):
            if i in self._received_frames:
                chunk = self._received_frames[i]

                # FEC decode
                if self.config.fec_enabled:
                    try:
                        chunk, _ = self.fec.decode(chunk)
                    except ValueError:
                        pass  # FEC couldn't recover

                # Decryption
                if self.crypto:
                    try:
                        chunk = self.crypto.decrypt_chunk(chunk, i)
                    except ValueError:
                        pass  # Decryption failed

                # Decompression
                if self.config.compression_enabled:
                    import zstandard as zstd
                    try:
                        decompressor = zstd.ZstdDecompressor()
                        chunk = decompressor.decompress(chunk)
                    except Exception:
                        pass

                data.extend(chunk)

        # Verify hash
        computed_hash = hashlib.sha256(data).hexdigest()
        verified = computed_hash == self.transfer_info.file_hash

        return bytes(data), verified
