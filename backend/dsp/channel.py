"""Offline acoustic-channel model for modem testing.

This deliberately contains no device or network code. It is useful for tuning
profiles before putting two physical computers in the room.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .modulation import ModemConfig


@dataclass(frozen=True, slots=True)
class ChannelProfile:
    noise_std: float = 0.0
    amplitude: float = 1.0
    frequency_shift_hz: float = 0.0
    drop_symbol_rate: float = 0.0
    clipping: float | None = None
    timing_drift: float = 0.0
    echo_delay_ms: float = 0.0
    echo_gain: float = 0.0
    seed: int = 1


class SimulatedAudioChannel:
    """Apply independent physical impairments to a NumPy sample buffer."""

    def __init__(self, profile: ChannelProfile, config: ModemConfig = ModemConfig()) -> None:
        if not 0 <= profile.drop_symbol_rate <= 1:
            raise ValueError("drop_symbol_rate must be between 0 and 1")
        if not -0.2 <= profile.timing_drift <= 0.2:
            raise ValueError("timing_drift must be between -20% and 20%")
        self.profile = profile
        self.config = config
        self.rng = np.random.default_rng(profile.seed)

    def _frequency_shift(self, samples: np.ndarray) -> np.ndarray:
        offset = self.profile.frequency_shift_hz
        if not offset:
            return samples
        # FFT Hilbert transform creates an analytic signal without requiring
        # SciPy; rotating it shifts positive-frequency content while retaining
        # a real-valued microphone waveform.
        n = len(samples)
        spectrum = np.fft.fft(samples)
        hilbert = np.zeros(n)
        if n % 2 == 0:
            hilbert[0] = hilbert[n // 2] = 1
            hilbert[1 : n // 2] = 2
        else:
            hilbert[0] = 1
            hilbert[1 : (n + 1) // 2] = 2
        analytic = np.fft.ifft(spectrum * hilbert)
        time = np.arange(n) / self.config.sample_rate
        return np.real(analytic * np.exp(2j * np.pi * offset * time))

    def _timing(self, samples: np.ndarray) -> np.ndarray:
        if not self.profile.timing_drift:
            return samples
        scale = 1 + self.profile.timing_drift
        source_positions = np.arange(0, len(samples), scale)
        return np.interp(source_positions, np.arange(len(samples)), samples)

    def apply(self, samples: np.ndarray) -> np.ndarray:
        value = np.asarray(samples, dtype=np.float64).reshape(-1)
        value = self._timing(value)
        value = self._frequency_shift(value)
        value = value * self.profile.amplitude
        if self.profile.echo_delay_ms and self.profile.echo_gain:
            delay = max(1, round(self.profile.echo_delay_ms * self.config.sample_rate / 1000))
            echoed = np.zeros(len(value) + delay)
            echoed[: len(value)] += value
            echoed[delay:] += value * self.profile.echo_gain
            value = echoed
        if self.profile.drop_symbol_rate:
            width = self.config.samples_per_symbol
            for start in range(0, len(value), width):
                if self.rng.random() < self.profile.drop_symbol_rate:
                    value[start : start + width] = 0
        if self.profile.noise_std:
            value = value + self.rng.normal(0, self.profile.noise_std, len(value))
        if self.profile.clipping is not None:
            value = np.clip(value, -abs(self.profile.clipping), abs(self.profile.clipping))
        return value.astype(np.float64, copy=False)
