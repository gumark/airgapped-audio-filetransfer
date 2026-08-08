"""
Frame synchronization detector.

Detects the sync preamble and calibration signals in an incoming audio stream,
allowing the receiver to align with the transmitter's symbol timing.
"""

import numpy as np
from typing import Optional, Tuple


class SyncDetector:
    """
    Detects synchronization patterns in incoming audio.

    The sync strategy uses a two-stage approach:
    1. Tone detection: Detect a steady calibration tone to know transmission is starting
    2. Preamble detection: Detect an alternating frequency pattern to lock symbol timing
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        sync_frequency: int = 1000,
        freq_a: int = 1200,
        freq_b: int = 1400,
        symbol_rate: int = 250,
        tone_duration_min: float = 0.3,
    ):
        """
        Args:
            sample_rate: Audio sample rate in Hz
            sync_frequency: Frequency of the calibration/tone signal
            freq_a: First alternating frequency in preamble
            freq_b: Second alternating frequency in preamble
            symbol_rate: Symbol rate for timing alignment
            tone_duration_min: Minimum tone duration to consider valid (seconds)
        """
        self.sample_rate = sample_rate
        self.sync_frequency = sync_frequency
        self.freq_a = freq_a
        self.freq_b = freq_b
        self.symbol_rate = symbol_rate
        self.tone_duration_min = tone_duration_min
        self.samples_per_symbol = int(sample_rate / symbol_rate)

    def _goertzel(self, samples: np.ndarray, freq: float) -> float:
        """Compute Goertzel power for a single frequency."""
        N = len(samples)
        if N == 0:
            return 0.0
        k = int(0.5 + (N * freq) / self.sample_rate)
        omega = (2.0 * np.pi * k) / N
        cosine = np.cos(omega)
        coeff = 2.0 * cosine

        s_prev = 0.0
        s_prev2 = 0.0
        for sample in samples:
            s = float(sample) + coeff * s_prev - s_prev2
            s_prev2 = s_prev
            s_prev = s

        power = s_prev2 * s_prev2 + s_prev * s_prev - coeff * s_prev * s_prev2
        return power

    def detect_tone(
        self, waveform: np.ndarray, window_size: int = 4096, hop: int = 1024
    ) -> Optional[int]:
        """
        Detect a steady sync tone in the waveform.

        Scans through the waveform looking for a sustained period where
        the sync frequency dominates.

        Returns:
            Sample index where the tone was first detected, or None
        """
        sync_power_threshold = None
        powers = []

        for start in range(0, len(waveform) - window_size, hop):
            chunk = waveform[start:start + window_size]
            power = self._goertzel(chunk, self.sync_frequency)
            powers.append((start, power))

        if not powers:
            return None

        # Find the power threshold (noise floor + margin)
        all_powers = [p for _, p in powers]
        noise_floor = np.median(all_powers)
        sync_threshold = noise_floor * 5  # Signal should be much stronger

        # Find the first window where power exceeds threshold
        for start, power in powers:
            if power > sync_threshold:
                return start

        return None

    def detect_preamble(
        self, waveform: np.ndarray, start_from: int = 0,
        expected_symbols: int = 32
    ) -> Optional[int]:
        """
        Detect an alternating frequency preamble pattern.

        The preamble alternates between freq_a and freq_b for
        expected_symbols periods. This lets the receiver lock onto
        the symbol timing.

        Returns:
            Sample index where the data begins (after preamble), or None
        """
        sps = self.samples_per_symbol
        detected_pattern = 0

        for i in range(expected_symbols * 2):  # Check more than needed
            sym_start = start_from + i * sps
            sym_end = sym_start + sps
            if sym_end > len(waveform):
                return None

            chunk = waveform[sym_start:sym_end]
            if len(chunk) == 0:
                return None

            windowed = chunk * np.hamming(len(chunk))
            power_a = self._goertzel(windowed, self.freq_a)
            power_b = self._goertzel(windowed, self.freq_b)

            expected_freq = self.freq_a if (i % 2 == 0) else self.freq_b
            detected_freq = self.freq_a if power_a > power_b else self.freq_b

            if detected_freq == expected_freq:
                detected_pattern += 1
            else:
                # Allow some errors but reset if too many
                if detected_pattern < expected_symbols // 2:
                    detected_pattern = 0
                    break

        if detected_pattern >= expected_symbols // 2:
            # Return the sample after the preamble
            preamble_end = start_from + expected_symbols * sps
            return preamble_end

        return None

    def find_frame_start(
        self, waveform: np.ndarray
    ) -> Optional[Tuple[int, int]]:
        """
        Find where a frame starts in the waveform.

        First looks for a sync tone, then a preamble, then returns
        the sample index where data symbols begin.

        Returns:
            Tuple of (data_start_sample, samples_per_symbol) or None
        """
        # Stage 1: Look for sync tone
        tone_start = self.detect_tone(waveform)

        # Stage 2: Look for preamble (try from start and from tone)
        preamble_starts = [0]
        if tone_start is not None:
            preamble_starts.append(tone_start)

        for start in preamble_starts:
            result = self.detect_preamble(waveform, start)
            if result is not None:
                return result, self.samples_per_symbol

        return None
