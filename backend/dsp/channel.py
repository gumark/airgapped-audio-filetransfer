"""
Simulated audio channel for testing.

Introduces realistic impairments to test the modem's robustness:
- White noise
- Frequency shifts/drift
- Dropped symbols
- Random bit corruption
- Amplitude changes
- Clipping
- Timing drift
- Echoes/reverberation

This allows testing the full modem pipeline without real hardware.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ChannelParams:
    """Parameters for the simulated noisy channel."""
    # Noise
    noise_level_db: float = -30.0      # White noise level (dB relative to signal)
    noise_type: str = "white"           # "white", "pink", "brown"

    # Frequency
    frequency_shift_hz: float = 0.0     # Constant frequency offset
    frequency_drift_hz_per_sec: float = 0.0  # Slow frequency drift

    # Amplitude
    gain_db: float = 0.0                # Gain adjustment
    clipping_threshold: float = 1.0     # Clipping level (1.0 = no clipping)
    clipping_fraction: float = 0.0      # Fraction of samples to clip

    # Symbol-level
    symbol_drop_rate: float = 0.0       # Probability of dropping a symbol
    symbol_corrupt_rate: float = 0.0    # Probability of corrupting a symbol

    # Timing
    timing_drift_ppm: float = 0.0       # Parts per million timing drift

    # Echo/reverberation
    echo_delay_ms: float = 0.0          # Echo delay in milliseconds
    echo_attenuation_db: float = -20.0  # Echo level relative to direct signal

    # Bandwidth
    low_cutoff_hz: float = 0.0          # High-pass filter cutoff
    high_cutoff_hz: float = 24000.0     # Low-pass filter cutoff


class SimulatedChannel:
    """
    Simulates an audio channel with configurable impairments.

    Usage:
        channel = SimulatedChannel(ChannelParams(noise_level_db=-25))
        received = channel.transmit(modulated_audio)
    """

    def __init__(self, params: Optional[ChannelParams] = None, seed: int = 42):
        self.params = params or ChannelParams()
        self.rng = np.random.default_rng(seed)

    def transmit(self, signal: np.ndarray, sample_rate: int = 48000) -> np.ndarray:
        """
        Pass a signal through the simulated channel.

        Args:
            signal: Input audio waveform (float32)
            sample_rate: Sample rate in Hz

        Returns:
            Impaired output waveform
        """
        output = signal.copy().astype(np.float64)

        # 1. Apply gain
        if self.params.gain_db != 0:
            gain_linear = 10 ** (self.params.gain_db / 20)
            output *= gain_linear

        # 2. Frequency shift. Use an analytic-signal rotation so the
        # simulator shifts carriers instead of applying amplitude modulation.
        if self.params.frequency_shift_hz != 0:
            from scipy.signal import hilbert
            t = np.arange(len(output), dtype=np.float64) / sample_rate
            analytic = hilbert(output)
            output = np.real(
                analytic * np.exp(1j * 2 * np.pi * self.params.frequency_shift_hz * t)
            )

        # 3. Frequency drift. Integrate the changing offset into phase.
        if self.params.frequency_drift_hz_per_sec != 0:
            from scipy.signal import hilbert
            t = np.arange(len(output), dtype=np.float64) / sample_rate
            drift_phase = np.pi * self.params.frequency_drift_hz_per_sec * t * t
            analytic = hilbert(output)
            output = np.real(analytic * np.exp(1j * drift_phase))

        # 4. Add white noise
        if self.params.noise_level_db > -100:
            noise_amplitude = 10 ** (self.params.noise_level_db / 20)
            noise = self.rng.normal(0, noise_amplitude, len(output))
            output += noise

        # 5. Echo/reverberation
        if self.params.echo_delay_ms > 0 and self.params.echo_attenuation_db > -60:
            echo_samples = int(sample_rate * self.params.echo_delay_ms / 1000)
            echo_attenuation = 10 ** (self.params.echo_attenuation_db / 20)
            if echo_samples < len(output):
                echo = np.zeros_like(output)
                echo[echo_samples:] = output[:-echo_samples] * echo_attenuation
                output += echo

        # 6. Bandpass filtering (simplified)
        if self.params.low_cutoff_hz > 0 or self.params.high_cutoff_hz < sample_rate / 2:
            from scipy.signal import butter, sosfilt
            nyquist = sample_rate / 2
            low = max(self.params.low_cutoff_hz / nyquist, 0.001)
            high = min(self.params.high_cutoff_hz / nyquist, 0.999)
            sos = butter(4, [low, high], btype='band', output='sos')
            output = sosfilt(sos, output)

        # 7. Symbol-level operations (if we know symbol boundaries)
        # These are applied symbol-by-symbol for realism

        # 8. Clipping
        if self.params.clipping_fraction > 0:
            num_to_clip = int(len(output) * self.params.clipping_fraction)
            if num_to_clip > 0:
                # Find samples with highest amplitude and clip them
                abs_output = np.abs(output)
                clip_indices = np.argpartition(abs_output, -num_to_clip)[-num_to_clip:]
                output[clip_indices] = np.sign(output[clip_indices]) * self.params.clipping_threshold

        # 9. Global clipping
        output = np.clip(output, -self.params.clipping_threshold,
                        self.params.clipping_threshold)

        return output.astype(np.float32)

    def transmit_with_symbol_ops(
        self, signal: np.ndarray, samples_per_symbol: int,
        sample_rate: int = 48000,
    ) -> np.ndarray:
        """
        Transmit with symbol-level impairments applied.

        This applies per-symbol drops and corruptions in addition
        to continuous channel effects.
        """
        output = self.transmit(signal, sample_rate)

        # Apply symbol-level operations
        if self.params.symbol_drop_rate > 0 or self.params.symbol_corrupt_rate > 0:
            num_symbols = len(output) // samples_per_symbol
            for i in range(num_symbols):
                start = i * samples_per_symbol
                end = start + samples_per_symbol

                # Symbol drop
                if self.rng.random() < self.params.symbol_drop_rate:
                    output[start:end] = 0

                # Symbol corruption
                if self.rng.random() < self.params.symbol_corrupt_rate:
                    # Replace with random noise at signal level
                    signal_level = np.sqrt(np.mean(output[start:end] ** 2)) + 0.01
                    output[start:end] = self.rng.normal(
                        0, signal_level, samples_per_symbol
                    ).astype(np.float32)

        # Apply timing drift
        if self.params.timing_drift_ppm != 0:
            drift_factor = 1.0 + self.params.timing_drift_ppm * 1e-6
            target_length = int(len(output) * drift_factor)
            output = np.interp(
                np.linspace(0, len(output) - 1, target_length),
                np.arange(len(output)),
                output,
            ).astype(np.float32)

        return output


def create_test_channel(preset: str = "clean") -> SimulatedChannel:
    """
    Create a channel with common preset configurations.

    Presets:
        "clean":   Perfect channel (no noise)
        "good":    Mild noise, no corruption
        "moderate": Noticeable noise, occasional errors
        "bad":     Heavy noise, frequent errors
        "terrible": Extreme conditions, many dropped symbols
    """
    presets = {
        "clean": ChannelParams(),
        "good": ChannelParams(
            noise_level_db=-35,
            clipping_fraction=0.001,
        ),
        "moderate": ChannelParams(
            noise_level_db=-25,
            frequency_shift_hz=5,
            symbol_corrupt_rate=0.02,
            clipping_fraction=0.01,
        ),
        "bad": ChannelParams(
            noise_level_db=-15,
            frequency_shift_hz=10,
            symbol_drop_rate=0.05,
            symbol_corrupt_rate=0.08,
            clipping_fraction=0.05,
            echo_delay_ms=2,
            echo_attenuation_db=-15,
        ),
        "terrible": ChannelParams(
            noise_level_db=-10,
            frequency_shift_hz=20,
            frequency_drift_hz_per_sec=2,
            symbol_drop_rate=0.15,
            symbol_corrupt_rate=0.15,
            clipping_fraction=0.1,
            echo_delay_ms=5,
            echo_attenuation_db=-10,
            timing_drift_ppm=50,
        ),
    }

    if preset not in presets:
        raise ValueError(f"Unknown preset: {preset}. Available: {list(presets.keys())}")

    return SimulatedChannel(presets[preset])
