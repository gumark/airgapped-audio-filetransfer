"""
FSK (Frequency Shift Keying) Demodulator.

Converts audio waveforms captured by a microphone back into digital symbols.

Uses the Goertzel algorithm for efficient single-frequency detection,
which is much faster than computing a full FFT when we only need to
detect a small number of known frequencies.
"""

import numpy as np
from typing import List, Optional, Tuple


def goertzel_magnitude(samples: np.ndarray, target_freq: float,
                       sample_rate: int) -> float:
    """
    Compute the squared magnitude of the DFT at a single target frequency
    using the Goertzel algorithm.

    This is O(N) per frequency instead of O(N log N) for a full FFT,
    making it ideal for detecting a small number of known FSK frequencies.

    Args:
        samples: Input audio samples (1D array)
        target_freq: Frequency to detect (Hz)
        sample_rate: Audio sample rate (Hz)

    Returns:
        Squared magnitude of the DFT at target_freq
    """
    N = len(samples)
    if N == 0:
        return 0.0
    k = int(0.5 + (N * target_freq) / sample_rate)
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


def goertzel_magnitudes_vectorized(
    samples: np.ndarray,
    target_freqs: List[float],
    sample_rate: int,
) -> List[float]:
    """
    Compute Goertzel magnitudes for multiple target frequencies.

    For small numbers of frequencies, this is more efficient than FFT.
    """
    return [goertzel_magnitude(samples, f, sample_rate) for f in target_freqs]


class FSKDemodulator:
    """
    M-ary FSK demodulator.

    Uses the Goertzel algorithm for efficient frequency detection.
    Demodulates an audio waveform into a stream of symbols, and optionally
    into bytes.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        frequencies: Optional[List[int]] = None,
        symbol_rate: int = 250,
        confidence_threshold: float = 1.5,
    ):
        """
        Args:
            sample_rate: Audio sample rate in Hz
            frequencies: List of carrier frequencies matching the modulator
            symbol_rate: Expected symbol rate (baud)
            confidence_threshold: Minimum ratio of strongest to second-strongest
                frequency power for a symbol to be considered "confident".
                Higher values = more strict detection.
        """
        self.sample_rate = sample_rate
        self.frequencies = frequencies or [1200, 1600, 2000, 2400]
        self.symbol_rate = symbol_rate
        self.confidence_threshold = confidence_threshold

        self.bits_per_symbol = int(np.log2(len(self.frequencies)))
        self.samples_per_symbol = int(sample_rate / symbol_rate)

    def detect_symbol(self, samples: np.ndarray) -> Tuple[int, float, List[float]]:
        """
        Detect a single symbol from audio samples.

        Args:
            samples: Audio samples for exactly one symbol period

        Returns:
            Tuple of (detected_symbol, confidence, all_powers)
            - detected_symbol: Index of the detected frequency
            - confidence: Ratio of best to second-best power (>1 = confident)
            - all_powers: List of power values for each frequency
        """
        powers = goertzel_magnitudes_vectorized(
            samples, self.frequencies, self.sample_rate
        )

        sorted_powers = sorted(powers, reverse=True)
        best = sorted_powers[0]
        second = sorted_powers[1] if len(sorted_powers) > 1 else 1e-10

        confidence = best / max(second, 1e-10)
        detected = int(np.argmax(powers))

        return detected, confidence, powers

    def demodulate_symbols(
        self, waveform: np.ndarray, offset: int = 0
    ) -> Tuple[List[int], List[float]]:
        """
        Demodulate an audio waveform into symbols.

        Args:
            waveform: Input audio samples (mono, float32)
            offset: Sample offset to start demodulation (for skipping preamble)

        Returns:
            Tuple of (symbols, confidences)
        """
        symbols = []
        confidences = []

        samples_per_sym = self.samples_per_symbol
        if offset < 0 or offset > len(waveform):
            raise ValueError("offset must be within the waveform")
        num_symbols = (len(waveform) - offset) // samples_per_sym

        for i in range(num_symbols):
            start = offset + i * samples_per_sym
            end = start + samples_per_sym
            chunk = waveform[start:end]

            # Apply a window function to reduce spectral leakage
            windowed = chunk * np.hamming(len(chunk))

            symbol, confidence, _ = self.detect_symbol(windowed)
            symbols.append(symbol)
            confidences.append(confidence)

        return symbols, confidences

    def demodulate_to_bytes(self, waveform: np.ndarray, offset: int = 0) -> bytes:
        """
        Demodulate an audio waveform directly to bytes.

        Args:
            waveform: Input audio samples
            offset: Sample offset to start demodulation

        Returns:
            Demodulated bytes
        """
        symbols, _ = self.demodulate_symbols(waveform, offset)
        return self.symbols_to_bytes(symbols)

    def symbols_to_bytes(self, symbols: List[int]) -> bytes:
        """
        Convert a list of symbols back to bytes.

        For 4-FSK (2 bits/symbol):
            symbols [3, 1, 0, 2] → byte 0b11010010

        Bits are assembled from MSB to LSB.
        """
        result = bytearray()
        current_byte = 0
        bits_in_byte = 0

        for symbol in symbols:
            current_byte = (current_byte << self.bits_per_symbol) | symbol
            bits_in_byte += self.bits_per_symbol

            if bits_in_byte >= 8:
                result.append(current_byte >> (bits_in_byte - 8))
                bits_in_byte -= 8
                current_byte = current_byte & ((1 << bits_in_byte) - 1)

        # Handle any remaining bits (pad with zeros)
        if bits_in_byte > 0:
            current_byte <<= (8 - bits_in_byte)
            result.append(current_byte)

        return bytes(result)

    def measure_snr(self, waveform: np.ndarray, signal_freq: float = 1500,
                    noise_band: Tuple[float, float] = (200, 800)) -> float:
        """
        Estimate Signal-to-Noise Ratio in dB.

        Measures power at the signal frequency vs power in a noise band.
        """
        # Signal power at expected frequency
        sig_power = goertzel_magnitude(
            waveform, signal_freq, self.sample_rate
        )

        # Noise power (average of several frequencies in noise band)
        noise_freqs = np.linspace(noise_band[0], noise_band[1], 10)
        noise_powers = [goertzel_magnitude(waveform, f, self.sample_rate)
                        for f in noise_freqs]
        avg_noise = np.mean(noise_powers) + 1e-10

        snr = 10 * np.log10(sig_power / avg_noise)
        return float(snr)

    def detect_clipping(self, waveform: np.ndarray,
                        threshold: float = 0.95) -> float:
        """
        Detect if the waveform is clipping.

        Returns fraction of samples at or above threshold.
        """
        if len(waveform) == 0:
            return 0.0
        clipped = np.sum(np.abs(waveform) >= threshold)
        return float(clipped / len(waveform))
