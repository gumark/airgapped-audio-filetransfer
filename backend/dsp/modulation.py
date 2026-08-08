"""
FSK (Frequency Shift Keying) Modulator.

Converts digital symbols into audio waveforms suitable for speaker output.

Modulation scheme: M-ary FSK where each symbol maps to a distinct frequency.
The number of frequencies determines bits_per_symbol = log2(len(frequencies)).

For 4-FSK: frequencies = [f0, f1, f2, f3]
  symbol 0 → f0, symbol 1 → f1, symbol 2 → f2, symbol 3 → f3
  Each symbol carries 2 bits.

Audio generation uses continuous-phase FSK to avoid phase discontinuities
which cause audible clicks and spectral spreading.
"""

import numpy as np
from typing import List, Optional


class FSKModulator:
    """
    M-ary FSK modulator with continuous-phase waveform generation.

    Configuration:
        sample_rate: Audio sample rate in Hz (e.g., 48000)
        frequencies: List of carrier frequencies for each symbol
        symbol_rate: Symbols per second (baud)
        amplitude: Output amplitude (0.0 - 1.0)
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        frequencies: Optional[List[int]] = None,
        symbol_rate: int = 250,
        amplitude: float = 0.8,
    ):
        self.sample_rate = sample_rate
        self.frequencies = frequencies or [1200, 1600, 2000, 2400]
        self.symbol_rate = symbol_rate
        self.amplitude = amplitude

        # Pre-calculate derived values
        self.bits_per_symbol = self._compute_bits_per_symbol()
        self.samples_per_symbol = int(sample_rate / symbol_rate)

    def _compute_bits_per_symbol(self) -> int:
        """Compute bits carried per symbol from number of frequencies."""
        n = len(self.frequencies)
        bits = int(np.log2(n))
        if 2 ** bits != n:
            raise ValueError(
                f"Number of frequencies ({n}) must be a power of 2 for "
                f"uniform bit mapping. Got {bits} bits."
            )
        return bits

    def symbols_to_bytes(self, data: bytes) -> List[int]:
        """
        Convert a byte sequence into a list of symbols.

        For 4-FSK (2 bits/symbol):
            byte 0b11010010 → symbols [3, 1, 0, 2]

        Bits are grouped from MSB to LSB.
        Each byte produces 8/bits_per_symbol symbols.
        """
        mask = (1 << self.bits_per_symbol) - 1
        symbols_per_byte = 8 // self.bits_per_symbol
        symbols = []
        for byte in data:
            for i in range(symbols_per_byte):
                shift = 8 - self.bits_per_symbol * (i + 1)
                symbol = (byte >> shift) & mask
                symbols.append(symbol)
        return symbols

    def bytes_to_symbols(self, data: bytes) -> List[int]:
        """Alias for symbols_to_bytes."""
        return self.symbols_to_bytes(data)

    def modulate_symbols(self, symbols: List[int]) -> np.ndarray:
        """
        Convert a list of symbols into an audio waveform.

        Uses continuous-phase FSK: the oscillator phase is maintained
        across symbol boundaries to produce a smooth waveform without
        clicks or discontinuities.

        Returns:
            numpy array of float32 audio samples (mono).
        """
        samples_per_sym = self.samples_per_symbol
        total_samples = len(symbols) * samples_per_sym
        waveform = np.zeros(total_samples, dtype=np.float64)

        phase = 0.0  # Current oscillator phase (radians)

        for i, symbol in enumerate(symbols):
            freq = self.frequencies[symbol]
            omega = 2.0 * np.pi * freq / self.sample_rate

            start = i * samples_per_sym
            end = start + samples_per_sym

            # Generate samples with continuous phase
            t = np.arange(samples_per_sym, dtype=np.float64)
            waveform[start:end] = self.amplitude * np.sin(phase + omega * t)

            # Update phase for next symbol (maintain continuity)
            phase = (phase + omega * samples_per_sym) % (2.0 * np.pi)

        return waveform.astype(np.float32)

    def modulate_bytes(self, data: bytes) -> np.ndarray:
        """
        Convenience: convert bytes directly to audio waveform.

        Returns:
            numpy array of float32 audio samples.
        """
        symbols = self.symbols_to_bytes(data)
        return self.modulate_symbols(symbols)

    def add_preamble(self, waveform: np.ndarray, num_symbols: int = 32) -> np.ndarray:
        """
        Add a synchronization preamble to the beginning of a waveform.

        The preamble alternates between the first two frequencies to create
        a recognizable pattern that the receiver can lock onto.
        """
        preamble_symbols = []
        for i in range(num_symbols):
            preamble_symbols.append(i % 2)  # Alternate between freq 0 and freq 1

        preamble_wave = self.modulate_symbols(preamble_symbols)
        return np.concatenate([preamble_wave, waveform])

    def add_sync_tone(self, waveform: np.ndarray, duration: float = 0.5,
                      frequency: int = 1000) -> np.ndarray:
        """
        Add a steady sync tone before the preamble.

        This gives the receiver time to detect that a transmission is starting
        and to calibrate its automatic gain control.
        """
        num_samples = int(self.sample_rate * duration)
        t = np.arange(num_samples, dtype=np.float64)
        tone = self.amplitude * 0.5 * np.sin(2.0 * np.pi * frequency * t)
        return np.concatenate([tone.astype(np.float32), waveform])
