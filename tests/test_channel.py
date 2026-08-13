import numpy as np

from backend.dsp.channel import ChannelProfile, SimulatedAudioChannel
from backend.dsp.modulation import ModemConfig


def test_simulated_channel_supports_physical_impairments():
    config = ModemConfig()
    samples = np.sin(np.arange(config.sample_rate // 10) * 2 * np.pi * 1800 / config.sample_rate)
    result = SimulatedAudioChannel(
        ChannelProfile(noise_std=0.01, amplitude=0.8, frequency_shift_hz=4, drop_symbol_rate=0.01, clipping=0.7, timing_drift=0.002, echo_delay_ms=3, echo_gain=0.15, seed=8),
        config,
    ).apply(samples)
    assert result.dtype == np.float64
    assert len(result) >= len(samples) - 2
    assert np.isfinite(result).all()
    assert np.max(np.abs(result)) <= 0.7 + 1e-9
