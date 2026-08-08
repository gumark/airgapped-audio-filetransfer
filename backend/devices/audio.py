"""
Audio device manager for input/output device selection and streaming.

Provides:
- Device enumeration and selection
- Real-time audio input (microphone)
- Real-time audio output (speakers)
- Device information (sample rate, channels, etc.)
"""

import numpy as np
from typing import Optional, List, Callable, Tuple
from dataclasses import dataclass

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False


@dataclass
class AudioDevice:
    """Information about an audio device."""
    index: int
    name: str
    host_api: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: float
    is_input: bool
    is_output: bool


class AudioDeviceManager:
    """
    Manages audio input/output devices for the transfer system.
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 1):
        """
        Args:
            sample_rate: Desired sample rate in Hz
            channels: Number of audio channels (1=mono, 2=stereo)
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self._input_stream = None
        self._output_stream = None

    def is_available(self) -> bool:
        """Check if sounddevice is available."""
        return SOUNDDEVICE_AVAILABLE

    def list_devices(self) -> List[AudioDevice]:
        """
        List all available audio devices.

        Returns:
            List of AudioDevice objects
        """
        if not SOUNDDEVICE_AVAILABLE:
            return []

        devices = sd.query_devices()
        result = []

        for i, dev in enumerate(devices):
            result.append(AudioDevice(
                index=i,
                name=dev["name"],
                host_api=sd.query_hostapis()[dev["hostapi"]]["name"],
                max_input_channels=dev["max_input_channels"],
                max_output_channels=dev["max_output_channels"],
                default_sample_rate=dev["default_samplerate"],
                is_input=dev["max_input_channels"] > 0,
                is_output=dev["max_output_channels"] > 0,
            ))

        return result

    def list_input_devices(self) -> List[AudioDevice]:
        """List available input (microphone) devices."""
        return [d for d in self.list_devices() if d.is_input]

    def list_output_devices(self) -> List[AudioDevice]:
        """List available output (speaker) devices."""
        return [d for d in self.list_devices() if d.is_output]

    def get_default_input(self) -> Optional[AudioDevice]:
        """Get the default input device."""
        if not SOUNDDEVICE_AVAILABLE:
            return None
        devices = self.list_input_devices()
        default_idx = sd.default.device[0]
        for dev in devices:
            if dev.index == default_idx:
                return dev
        return devices[0] if devices else None

    def get_default_output(self) -> Optional[AudioDevice]:
        """Get the default output device."""
        if not SOUNDDEVICE_AVAILABLE:
            return None
        devices = self.list_output_devices()
        default_idx = sd.default.device[1]
        for dev in devices:
            if dev.index == default_idx:
                return dev
        return devices[0] if devices else None

    def play_audio(
        self,
        audio_data: np.ndarray,
        device: Optional[int] = None,
        blocking: bool = True,
    ) -> None:
        """
        Play audio data through a speaker.

        Args:
            audio_data: Audio samples (float32, mono or stereo)
            device: Device index (None for default)
            blocking: If True, wait until playback completes
        """
        if not SOUNDDEVICE_AVAILABLE:
            raise RuntimeError("sounddevice is not installed")

        sd.play(
            audio_data,
            samplerate=self.sample_rate,
            device=device,
            blocking=blocking,
        )

    def stop_audio(self) -> None:
        """Stop any active sounddevice playback."""
        if SOUNDDEVICE_AVAILABLE:
            sd.stop()

    def record_audio(
        self,
        duration: float,
        device: Optional[int] = None,
    ) -> np.ndarray:
        """
        Record audio from a microphone.

        Args:
            duration: Recording duration in seconds
            device: Device index (None for default)

        Returns:
            Recorded audio samples (float32)
        """
        if not SOUNDDEVICE_AVAILABLE:
            raise RuntimeError("sounddevice is not installed")

        recording = sd.rec(
            int(duration * self.sample_rate),
            samplerate=self.sample_rate,
            channels=self.channels,
            device=device,
            dtype="float32",
            blocking=True,
        )
        return recording.flatten()

    def start_input_stream(
        self,
        callback: Callable[[np.ndarray], None],
        device: Optional[int] = None,
        blocksize: int = 4096,
    ) -> None:
        """
        Start a real-time input stream.

        Args:
            callback: Function called with each audio block
            device: Device index (None for default)
            blocksize: Number of samples per callback
        """
        if not SOUNDDEVICE_AVAILABLE:
            raise RuntimeError("sounddevice is not installed")

        def audio_callback(indata, frames, time, status):
            if status:
                print(f"Input stream status: {status}")
            callback(indata[:, 0].copy())

        self._input_stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            device=device,
            blocksize=blocksize,
            dtype="float32",
            callback=audio_callback,
        )
        self._input_stream.start()

    def stop_input_stream(self) -> None:
        """Stop the real-time input stream."""
        if self._input_stream is not None:
            self._input_stream.stop()
            self._input_stream.close()
            self._input_stream = None

    def start_output_stream(
        self,
        device: Optional[int] = None,
        blocksize: int = 4096,
    ) -> "OutputStream":
        """
        Start a real-time output stream.

        Returns:
            OutputStream wrapper that can be written to
        """
        if not SOUNDDEVICE_AVAILABLE:
            raise RuntimeError("sounddevice is not installed")

        return OutputStream(
            sample_rate=self.sample_rate,
            channels=self.channels,
            device=device,
            blocksize=blocksize,
        )

    def measure_level(
        self,
        duration: float = 0.5,
        device: Optional[int] = None,
    ) -> float:
        """
        Measure the current audio input level.

        Returns:
            RMS level in dB
        """
        audio = self.record_audio(duration, device)
        rms = np.sqrt(np.mean(audio ** 2)) + 1e-10
        return float(20 * np.log10(rms))


class OutputStream:
    """Wrapper for a sounddevice output stream."""

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 1,
        device: Optional[int] = None,
        blocksize: int = 4096,
    ):
        self._stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=channels,
            device=device,
            blocksize=blocksize,
            dtype="float32",
        )
        self._stream.start()

    def write(self, data: np.ndarray) -> None:
        """Write audio data to the stream."""
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        self._stream.write(data)

    def stop(self) -> None:
        """Stop the stream."""
        self._stream.stop()
        self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop()
