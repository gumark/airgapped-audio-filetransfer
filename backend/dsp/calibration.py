"""
Calibration engine for measuring and characterizing the audio channel.

Before transferring a file, both transmitter and receiver run a calibration
sequence to determine:
- Signal-to-noise ratio (SNR)
- Frequency detection confidence
- Symbol error rate
- Microphone level and clipping
- Ambient noise floor

Based on these measurements, the system selects an appropriate transfer
profile (speed vs reliability).
"""

import time
import numpy as np
from dataclasses import dataclass
from typing import Optional, Tuple, List
from enum import Enum


class ChannelQuality(Enum):
    """Channel quality classification."""
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    UNUSABLE = "unusable"


@dataclass
class CalibrationResult:
    """Results from a calibration measurement."""
    snr_db: float              # Signal-to-noise ratio in dB
    noise_floor_db: float      # Ambient noise level in dB
    clipping: bool             # Whether input is clipping
    clipping_fraction: float   # Fraction of samples clipping
    frequency_confidence: float  # How reliably we detect frequencies (0-1)
    symbol_error_rate: float   # Estimated symbol error rate
    estimated_quality: ChannelQuality
    recommended_profile: str   # "speed", "balanced", "reliability"
    recommended_symbol_rate: int
    recommended_fec_overhead: float
    detected_frequency_shift: float  # Hz offset detected
    measurement_duration: float  # seconds


class CalibrationEngine:
    """
    Generates calibration signals and analyzes received audio
    to characterize the audio channel quality.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        frequencies: Optional[List[int]] = None,
        symbol_rate: int = 250,
    ):
        self.sample_rate = sample_rate
        self.frequencies = frequencies or [1200, 1400, 1600, 1800]
        self.symbol_rate = symbol_rate
        self.samples_per_symbol = int(sample_rate / symbol_rate)

    def generate_calibration_signal(
        self, duration: float = 3.0
    ) -> Tuple[np.ndarray, List[int]]:
        """
        Generate a calibration signal.

        The signal consists of:
        1. Steady tone for level measurement (0.5s)
        2. Known symbol sequence for detection testing (2.0s)
        3. Silence gap for noise floor measurement (0.5s)

        Returns:
            Tuple of (waveform, expected_symbols)
        """
        total_samples = int(self.sample_rate * duration)
        waveform = np.zeros(total_samples, dtype=np.float64)
        expected_symbols = []

        # Section 1: Steady tone at middle frequency (0.5s)
        tone_samples = int(self.sample_rate * 0.5)
        t = np.arange(tone_samples, dtype=np.float64)
        mid_freq = self.frequencies[len(self.frequencies) // 2]
        waveform[:tone_samples] = 0.7 * np.sin(
            2.0 * np.pi * mid_freq * t / self.sample_rate
        )

        # Section 2: Known symbol sequence (2.0s)
        sym_start = tone_samples
        num_calibration_symbols = int(2.0 * self.symbol_rate)
        # Create a pattern that cycles through all frequencies
        for i in range(num_calibration_symbols):
            symbol = i % len(self.frequencies)
            expected_symbols.append(symbol)

            freq = self.frequencies[symbol]
            sym_t = np.arange(self.samples_per_symbol, dtype=np.float64)
            omega = 2.0 * np.pi * freq / self.sample_rate
            start_sample = sym_start + i * self.samples_per_symbol
            end_sample = start_sample + self.samples_per_symbol

            if end_sample <= total_samples:
                waveform[start_sample:end_sample] = (
                    0.7 * np.sin(omega * sym_t)
                )

        # Section 3: Silence for noise floor (0.5s)
        # Already zeros from initialization

        return waveform.astype(np.float32), expected_symbols

    def analyze_calibration_signal(
        self,
        received: np.ndarray,
        expected_symbols: List[int],
        start_offset: int = 0,
    ) -> CalibrationResult:
        """
        Analyze a received calibration signal.

        Compares the received audio against the expected pattern
        and measures channel quality metrics.
        """
        start_time = time.time()

        # --- Noise floor measurement ---
        # Use the last 0.5s of silence for noise floor
        noise_samples = int(self.sample_rate * 0.5)
        noise_section = received[-noise_samples:].astype(np.float64)
        noise_floor_rms = np.sqrt(np.mean(noise_section ** 2)) + 1e-10
        noise_floor_db = 20 * np.log10(noise_floor_rms)

        # --- Signal level measurement ---
        tone_samples = int(self.sample_rate * 0.5)
        tone_section = received[:tone_samples].astype(np.float64)
        signal_rms = np.sqrt(np.mean(tone_section ** 2)) + 1e-10
        signal_db = 20 * np.log10(signal_rms)

        # --- SNR ---
        snr_db = signal_db - noise_floor_db

        # --- Clipping detection ---
        clipping_threshold = 0.95
        total_samples = len(received)
        clipped_count = np.sum(np.abs(received) >= clipping_threshold)
        clipping_fraction = float(clipped_count / total_samples)
        clipping = clipping_fraction > 0.001

        # --- Symbol detection and error rate ---
        sym_start = tone_samples
        detected_symbols = []
        correct = 0
        total = 0

        from .demodulation import goertzel_magnitudes_vectorized

        for i in range(min(len(expected_symbols),
                         (len(received) - sym_start) // self.samples_per_symbol)):
            s_start = sym_start + i * self.samples_per_symbol
            s_end = s_start + self.samples_per_symbol
            chunk = received[s_start:s_end].astype(np.float64)

            if len(chunk) == 0:
                break

            windowed = chunk * np.hamming(len(chunk))
            powers = goertzel_magnitudes_vectorized(
                windowed, self.frequencies, self.sample_rate
            )
            detected = int(np.argmax(powers))
            detected_symbols.append(detected)

            if detected == expected_symbols[i]:
                correct += 1
            total += 1

        symbol_error_rate = 1.0 - (correct / max(total, 1))

        # --- Frequency confidence ---
        # Average ratio of strongest to second-strongest
        confidences = []
        for i in range(min(len(expected_symbols), total)):
            s_start = sym_start + i * self.samples_per_symbol
            s_end = s_start + self.samples_per_symbol
            chunk = received[s_start:s_end].astype(np.float64)

            if len(chunk) == 0:
                break

            windowed = chunk * np.hamming(len(chunk))
            powers = goertzel_magnitudes_vectorized(
                windowed, self.frequencies, self.sample_rate
            )
            sorted_powers = sorted(powers, reverse=True)
            if sorted_powers[1] > 0:
                conf = sorted_powers[0] / sorted_powers[1]
                confidences.append(min(conf / 10.0, 1.0))  # Normalize to 0-1

        frequency_confidence = float(np.mean(confidences)) if confidences else 0.0

        # --- Frequency shift detection ---
        # Check if detected frequencies are consistently offset
        freq_shift = 0.0  # Placeholder - would need more sophisticated analysis

        # --- Quality classification ---
        if snr_db > 30 and symbol_error_rate < 0.01:
            quality = ChannelQuality.EXCELLENT
            profile = "speed"
            rec_symbol_rate = 500
            rec_fec = 0.10
        elif snr_db > 20 and symbol_error_rate < 0.05:
            quality = ChannelQuality.GOOD
            profile = "balanced"
            rec_symbol_rate = 350
            rec_fec = 0.20
        elif snr_db > 10 and symbol_error_rate < 0.15:
            quality = ChannelQuality.FAIR
            profile = "balanced"
            rec_symbol_rate = 250
            rec_fec = 0.30
        elif snr_db > 5 and symbol_error_rate < 0.30:
            quality = ChannelQuality.POOR
            profile = "reliability"
            rec_symbol_rate = 150
            rec_fec = 0.40
        else:
            quality = ChannelQuality.UNUSABLE
            profile = "reliability"
            rec_symbol_rate = 100
            rec_fec = 0.50

        measurement_duration = time.time() - start_time

        return CalibrationResult(
            snr_db=round(snr_db, 1),
            noise_floor_db=round(noise_floor_db, 1),
            clipping=clipping,
            clipping_fraction=round(clipping_fraction, 4),
            frequency_confidence=round(frequency_confidence, 3),
            symbol_error_rate=round(symbol_error_rate, 4),
            estimated_quality=quality,
            recommended_profile=profile,
            recommended_symbol_rate=rec_symbol_rate,
            recommended_fec_overhead=rec_fec,
            detected_frequency_shift=round(freq_shift, 2),
            measurement_duration=round(measurement_duration, 2),
        )
