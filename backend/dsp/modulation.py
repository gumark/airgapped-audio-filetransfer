"""FSK modem.

Four tones encode two bits per symbol. Each frame has a repeated known
preamble, allowing a receiver to find frames after silence, jitter, or a small
recording offset. This module only deals with samples and bytes; device I/O is
kept in ``backend.audio_io``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from backend.protocol.frames import Frame, decode_frame

PREAMBLE = (0, 1, 2, 3) * 6


@dataclass(frozen=True, slots=True)
class ModemConfig:
    sample_rate: int = 48_000
    symbol_rate: int = 300
    frequencies: tuple[float, float, float, float] = (1200.0, 1800.0, 2400.0, 3000.0)
    amplitude: float = 0.42
    # A deliberate quiet interval lets the streaming microphone worker emit
    # one frame at a time without buffering an entire multi-gigabyte transfer.
    gap_symbols: float = 32.0

    def __post_init__(self) -> None:
        if self.sample_rate < 8000 or self.symbol_rate <= 0:
            raise ValueError("invalid sample or symbol rate")
        if self.sample_rate / self.symbol_rate < 8:
            raise ValueError("at least eight samples per symbol are required")
        if len(self.frequencies) != 4 or len(set(self.frequencies)) != 4:
            raise ValueError("exactly four distinct FSK frequencies are required")
        if max(self.frequencies) >= self.sample_rate / 2:
            raise ValueError("FSK frequencies must be below Nyquist")

    @property
    def samples_per_symbol(self) -> int:
        return round(self.sample_rate / self.symbol_rate)


def bytes_to_symbols(value: bytes) -> list[int]:
    return [(byte >> shift) & 3 for byte in value for shift in (6, 4, 2, 0)]


def symbols_to_bytes(symbols: list[int]) -> bytes:
    if len(symbols) % 4:
        raise ValueError("symbol count must be divisible by four")
    out = bytearray()
    for index in range(0, len(symbols), 4):
        value = 0
        for symbol in symbols[index : index + 4]:
            if not 0 <= symbol <= 3:
                raise ValueError("invalid FSK symbol")
            value = (value << 2) | symbol
        out.append(value)
    return bytes(out)


def _tone(config: ModemConfig, frequency: float, length: int, phase: float = 0.0) -> np.ndarray:
    # A short raised-cosine envelope limits clicks and keeps adjacent frames
    # from producing broad-band artifacts.
    t = np.arange(length, dtype=np.float64) / config.sample_rate
    wave = np.sin(2 * math.pi * frequency * t + phase)
    ramp = min(length // 8, max(1, config.samples_per_symbol // 8))
    envelope = np.ones(length)
    if ramp:
        envelope[:ramp] = np.linspace(0, 1, ramp, endpoint=False)
        envelope[-ramp:] = np.linspace(1, 0, ramp, endpoint=False)
    return wave * envelope * config.amplitude


def modulate_bytes(value: bytes, config: ModemConfig = ModemConfig(), *, include_preamble: bool = True) -> np.ndarray:
    symbols = (list(PREAMBLE) if include_preamble else []) + bytes_to_symbols(value)
    sps = config.samples_per_symbol
    pieces = [_tone(config, config.frequencies[symbol], sps) for symbol in symbols]
    pieces.append(np.zeros(round(sps * config.gap_symbols), dtype=np.float64))
    return np.concatenate(pieces) if pieces else np.empty(0, dtype=np.float64)


def modulate_frame(frame: Frame, config: ModemConfig = ModemConfig()) -> np.ndarray:
    return modulate_bytes(frame.encode(), config)


def modulate_frames(frames: list[Frame], config: ModemConfig = ModemConfig()) -> np.ndarray:
    if not frames:
        return np.empty(0, dtype=np.float64)
    return np.concatenate([modulate_frame(frame, config) for frame in frames])


@dataclass(slots=True)
class DemodulationStats:
    frames_seen: int = 0
    frames_decoded: int = 0
    corrupted_frames: int = 0
    average_confidence: float = 0.0
    signal_level: float = 0.0


@dataclass(slots=True)
class DemodulatedResult:
    frames: list[Frame] = field(default_factory=list)
    stats: DemodulationStats = field(default_factory=DemodulationStats)


def _symbol_observations(samples: np.ndarray, config: ModemConfig, offset: int) -> list[tuple[int, float, float] | None]:
    sps = config.samples_per_symbol
    count = (len(samples) - offset) // sps
    if count <= 0:
        return []
    result: list[tuple[int, float, float] | None] = []
    # Correlation against sine and cosine is a compact Goertzel equivalent and
    # is stable when a speaker adds a little phase shift.
    window = np.arange(sps, dtype=np.float64)
    for index in range(count):
        segment = samples[offset + index * sps : offset + (index + 1) * sps]
        powers = []
        for frequency in config.frequencies:
            angle = 2 * math.pi * frequency * window / config.sample_rate
            projection = np.dot(segment, np.cos(angle)) ** 2 + np.dot(segment, np.sin(angle)) ** 2
            powers.append(float(projection))
        ordered = sorted(powers, reverse=True)
        level = math.sqrt(max(0.0, sum(segment * segment) / sps))
        if ordered[0] < 0.0008 or ordered[0] < ordered[1] * 1.12:
            result.append(None)
        else:
            result.append((int(np.argmax(powers)), ordered[0] / (ordered[1] + 1e-9), level))
    return result


def _matches_preamble(observations: list[tuple[int, float, float] | None], start: int) -> bool:
    if start + len(PREAMBLE) > len(observations):
        return False
    matches = 0
    for expected, observation in zip(PREAMBLE, observations[start : start + len(PREAMBLE)]):
        if observation is not None and observation[0] == expected and observation[1] >= 1.12:
            matches += 1
    return matches >= len(PREAMBLE) - 2


def demodulate_frames(samples: np.ndarray, config: ModemConfig = ModemConfig()) -> DemodulatedResult:
    """Recover CRC-valid frames from a recording containing one or more frames.

    A few sample offsets are tried to tolerate a recording that starts between
    symbols. Corrupted frames are discarded at this layer; the FEC layer sees
    the missing sequence and can reconstruct it.
    """
    samples = np.asarray(samples, dtype=np.float64).reshape(-1)
    best: DemodulatedResult | None = None
    # Searching all offsets is cheap relative to acoustic I/O and handles the
    # common case where the input callback does not begin on a symbol boundary.
    for offset in range(config.samples_per_symbol):
        observations = _symbol_observations(samples, config, offset)
        result = DemodulatedResult(stats=DemodulationStats(signal_level=float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0))
        index = 0
        confidence_sum = 0.0
        while index < len(observations):
            if not _matches_preamble(observations, index):
                index += 1
                continue
            result.stats.frames_seen += 1
            body_start = index + len(PREAMBLE)
            header_symbols = observations[body_start : body_start + 26 * 4]
            if any(observation is None for observation in header_symbols):
                result.stats.corrupted_frames += 1
                index = body_start + 1
                continue
            try:
                header = symbols_to_bytes([observation[0] for observation in header_symbols if observation is not None])
                payload_length = int.from_bytes(header[22:26], "big")
                body_bytes = 26 + payload_length + 4
                body_symbols = observations[body_start : body_start + body_bytes * 4]
                if len(body_symbols) < body_bytes * 4 or any(observation is None for observation in body_symbols):
                    raise ValueError("incomplete frame")
                raw = symbols_to_bytes([observation[0] for observation in body_symbols if observation is not None])
                frame = decode_frame(raw)
            except (ValueError, IndexError):
                result.stats.corrupted_frames += 1
                index = body_start + 1
                continue
            result.frames.append(frame)
            result.stats.frames_decoded += 1
            confidence_sum += sum(observation[1] for observation in body_symbols if observation is not None) / len(body_symbols)
            index = body_start + body_bytes * 4
        if result.stats.frames_decoded > (best.stats.frames_decoded if best else 0):
            result.stats.average_confidence = confidence_sum / max(1, result.stats.frames_decoded)
            best = result
    return best or DemodulatedResult()
