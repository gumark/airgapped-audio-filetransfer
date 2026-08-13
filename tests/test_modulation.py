import numpy as np

from backend.dsp.channel import ChannelProfile, SimulatedAudioChannel
from backend.dsp.modulation import ModemConfig, demodulate_frames, modulate_frames
from backend.protocol import Frame, FrameType


def test_fsk_round_trip_with_noise_and_level_change():
    config = ModemConfig(symbol_rate=300)
    frames = [
        Frame(42, FrameType.SYNC, 0, 2, b"sync"),
        Frame(42, FrameType.DATA, 1, 2, bytes(range(256)) * 2),
    ]
    samples = modulate_frames(frames, config)
    channel = SimulatedAudioChannel(ChannelProfile(noise_std=0.008, amplitude=0.88, seed=4), config).apply(samples)
    result = demodulate_frames(channel, config)
    assert [frame.payload for frame in result.frames] == [frame.payload for frame in frames]
    assert result.stats.frames_decoded == 2
