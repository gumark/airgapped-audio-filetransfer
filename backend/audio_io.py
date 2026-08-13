"""Optional local audio device integration.

No transfer data is ever sent over these APIs: sounddevice talks only to the
host's local audio driver. Importing this module remains possible on machines
without PortAudio so protocol tests can run headlessly.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from backend.dsp.modulation import DemodulatedResult, ModemConfig, demodulate_frames, modulate_frame
from backend.protocol import Frame, FrameType

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover
    sd = None


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: float


def list_devices() -> list[DeviceInfo]:
    if sd is None:
        return []
    return [
        DeviceInfo(index, str(info["name"]), int(info["max_input_channels"]), int(info["max_output_channels"]), float(info["default_samplerate"]))
        for index, info in enumerate(sd.query_devices())
    ]


def play_calibration(config: ModemConfig, *, device: int | None = None) -> None:
    """Play a short known tone sweep for a human-aligned link test."""
    if sd is None:
        raise RuntimeError("sounddevice/PortAudio is not installed")
    samples = modulate_frame(Frame(0, FrameType.SYNC, 0, 1, b"CALIBRATION"), config)
    sd.play(samples.astype(np.float32), samplerate=config.sample_rate, device=device, blocking=True)


def record_calibration(config: ModemConfig, *, device: int | None = None, seconds: float = 2.0) -> np.ndarray:
    """Record only a bounded calibration window from the local microphone."""
    if sd is None:
        raise RuntimeError("sounddevice/PortAudio is not installed")
    recording = sd.rec(round(config.sample_rate * seconds), samplerate=config.sample_rate, channels=1, dtype="float32", device=device, blocking=True)
    return np.asarray(recording[:, 0], dtype=np.float64)


def play_frames(frames, config: ModemConfig, *, device: int | None = None, on_frame: Callable[[int], None] | None = None, stop_event: threading.Event | None = None) -> None:
    if sd is None:
        raise RuntimeError("sounddevice/PortAudio is not installed")
    stop_event = stop_event or threading.Event()
    with sd.OutputStream(samplerate=config.sample_rate, channels=1, dtype="float32", device=device, blocksize=0) as stream:
        for index, frame in enumerate(frames):
            if stop_event.is_set():
                break
            stream.write(modulate_frame(frame, config).astype(np.float32))
            if on_frame:
                on_frame(index + 1)


class MicrophoneFrameCapture:
    """Convert quiet-delimited microphone bursts into validated frames.

    The transmitter inserts a short gap after every frame. The capture worker
    uses that gap to release one frame at a time, keeping RAM bounded even for
    multi-gigabyte transfers. CRC-invalid bursts are reported as missing.
    """

    def __init__(self, config: ModemConfig, on_result: Callable[[DemodulatedResult], None], *, device: int | None = None) -> None:
        self.config = config
        self.on_result = on_result
        self.device = device
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=32)
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, name="audio-receiver", daemon=True)
        self._buffer = np.empty(0, dtype=np.float64)

    def start(self) -> None:
        if sd is None:
            raise RuntimeError("sounddevice/PortAudio is not installed")
        self._worker.start()
        self._stream = sd.InputStream(
            samplerate=self.config.sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
            blocksize=1024,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, timing, status) -> None:
        if not self._stop.is_set():
            try:
                self._queue.put_nowait(np.asarray(indata[:, 0], dtype=np.float64).copy())
            except queue.Full:
                pass

    def _run(self) -> None:
        quiet_blocks = 0
        while not self._stop.is_set():
            try:
                block = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if block is None:
                break
            self._buffer = np.concatenate((self._buffer, block))
            if float(np.sqrt(np.mean(block * block))) < 0.008:
                quiet_blocks += 1
            else:
                quiet_blocks = 0
            if quiet_blocks >= 2 and len(self._buffer) > self.config.samples_per_symbol * len((0, 1, 2, 3) * 6):
                result = demodulate_frames(self._buffer, self.config)
                if result.frames or result.stats.corrupted_frames:
                    self.on_result(result)
                self._buffer = np.empty(0, dtype=np.float64)
                quiet_blocks = 0

    def stop(self) -> None:
        self._stop.set()
        if hasattr(self, "_stream"):
            self._stream.stop()
            self._stream.close()
        self._queue.put_nowait(None)
        self._worker.join(timeout=2)
