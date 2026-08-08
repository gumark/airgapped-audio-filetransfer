"""
Spectrum analyzer for real-time audio visualization.

Provides FFT-based frequency spectrum analysis for both transmitter
and receiver visualization, using efficient windowed FFT.
"""

import numpy as np
from typing import Tuple


class SpectrumAnalyzer:
    """
    Real-time spectrum analyzer using windowed FFT.

    Provides frequency spectrum data for visualization in the GUI.
    Uses a sliding window approach for smooth real-time updates.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        fft_size: int = 4096,
        overlap: float = 0.5,
    ):
        """
        Args:
            sample_rate: Audio sample rate in Hz
            fft_size: FFT window size (power of 2 for efficiency)
            overlap: Fraction of overlap between windows (0.0 - 0.75)
        """
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.overlap = overlap
        self.hop_size = int(fft_size * (1 - overlap))

        # Pre-compute window function
        self.window = np.hanning(fft_size)

        # Frequency axis for plotting
        self.freqs = np.fft.rfftfreq(fft_size, 1.0 / sample_rate)

    def compute_spectrum(self, samples: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Compute the frequency spectrum of audio samples.

        Args:
            samples: Input audio samples (should be >= fft_size)

        Returns:
            Tuple of (frequencies, magnitudes_db)
            - frequencies: Array of frequency bins (Hz)
            - magnitudes_db: Array of magnitudes in dB
        """
        if len(samples) < self.fft_size:
            # Pad with zeros if too short
            samples = np.pad(samples, (0, self.fft_size - len(samples)))

        # Take the last fft_size samples
        chunk = samples[-self.fft_size:].astype(np.float64)

        # Apply window function
        windowed = chunk * self.window

        # Compute FFT
        fft_result = np.fft.rfft(windowed)

        # Compute magnitude in dB
        magnitude = np.abs(fft_result)
        magnitude_db = 20 * np.log10(magnitude + 1e-10)

        return self.freqs, magnitude_db

    def compute_realtime_spectrum(
        self, buffer: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Compute spectrum from a growing buffer (for real-time display).

        Uses the most recent fft_size samples.
        """
        if len(buffer) < self.fft_size:
            return self.freqs, np.zeros(len(self.freqs))

        return self.compute_spectrum(buffer[-self.fft_size * 2:])

    def detect_frequencies(
        self, samples: np.ndarray, target_freqs: list,
        threshold_db: float = -40.0
    ) -> list:
        """
        Detect which target frequencies are present in the signal.

        Args:
            samples: Input audio samples
            target_freqs: List of frequencies to detect (Hz)
            threshold_db: Minimum magnitude to consider a frequency present

        Returns:
            List of (frequency, magnitude_db) tuples for detected frequencies
        """
        freqs, mag_db = self.compute_spectrum(samples)
        detected = []

        for target in target_freqs:
            # Find the closest FFT bin
            idx = np.argmin(np.abs(freqs - target))
            if mag_db[idx] > threshold_db:
                detected.append((float(freqs[idx]), float(mag_db[idx])))

        return detected

    def compute_waveform_rms(self, samples: np.ndarray,
                             frame_size: int = 1024) -> np.ndarray:
        """
        Compute RMS amplitude envelope of the waveform.

        Used for waveform visualization and level metering.

        Returns:
            Array of RMS values (one per frame)
        """
        num_frames = len(samples) // frame_size
        if num_frames == 0:
            return np.array([0.0])

        rms_values = []
        for i in range(num_frames):
            start = i * frame_size
            end = start + frame_size
            frame = samples[start:end].astype(np.float64)
            rms = np.sqrt(np.mean(frame ** 2))
            rms_values.append(rms)

        return np.array(rms_values)

    def compute_level_db(self, samples: np.ndarray) -> float:
        """
        Compute the current audio level in dB.

        Returns:
            Level in dB (0 dB = full scale)
        """
        rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
        return float(20 * np.log10(rms + 1e-10))

    def get_frequency_bands(
        self, samples: np.ndarray, num_bands: int = 32
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Compute frequency spectrum grouped into logarithmic bands.

        Better for visualization than linear FFT bins.
        """
        freqs, mag_db = self.compute_spectrum(samples)

        # Create logarithmic band edges
        min_freq = 100  # Hz
        max_freq = self.sample_rate / 2
        band_edges = np.logspace(
            np.log10(min_freq), np.log10(max_freq), num_bands + 1
        )

        band_magnitudes = []
        for i in range(num_bands):
            mask = (freqs >= band_edges[i]) & (freqs < band_edges[i + 1])
            if np.any(mask):
                band_mag = np.max(mag_db[mask])
            else:
                band_mag = -60.0  # Silence floor
            band_magnitudes.append(band_mag)

        band_centers = np.sqrt(band_edges[:-1] * band_edges[1:])
        return band_centers, np.array(band_magnitudes)
