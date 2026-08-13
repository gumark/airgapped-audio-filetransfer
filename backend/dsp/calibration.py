"""Calibration helpers kept separate from transfer policy and device I/O."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from .modulation import ModemConfig


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    signal_detected: bool
    snr_db: float
    noise_floor_db: float
    clipping: bool
    frequency_confidence: float
    microphone_level: float
    reliability: str
    recommended_profile: str


def analyze_signal(samples: np.ndarray, config: ModemConfig = ModemConfig()) -> CalibrationReport:
    values = np.asarray(samples, dtype=np.float64).reshape(-1)
    if not len(values):
        return CalibrationReport(False, 0.0, -120.0, False, 0.0, 0.0, "NONE", "MAXIMUM_RELIABILITY")
    level = float(np.sqrt(np.mean(values * values)))
    clipping = bool(np.any(np.abs(values) >= 0.98))
    # Compare energy in the configured audio band with the residual energy.
    spectrum = np.abs(np.fft.rfft(values)) ** 2
    freqs = np.fft.rfftfreq(len(values), 1 / config.sample_rate)
    band = np.zeros_like(spectrum, dtype=bool)
    for frequency in config.frequencies:
        band |= np.abs(freqs - frequency) < config.symbol_rate
    signal_energy = float(np.mean(spectrum[band])) if np.any(band) else 0.0
    noise_energy = float(np.mean(spectrum[~band])) if np.any(~band) else 1e-12
    snr = 10 * math.log10(max(signal_energy, 1e-12) / max(noise_energy, 1e-12))
    confidence = max(0.0, min(1.0, (snr + 5) / 35))
    if snr >= 22 and not clipping:
        reliability, profile = "HIGH", "MAXIMUM_SPEED"
    elif snr >= 12 and not clipping:
        reliability, profile = "MEDIUM", "BALANCED"
    else:
        reliability, profile = "LOW", "MAXIMUM_RELIABILITY"
    return CalibrationReport(
        signal_detected=snr > 3 and level > 0.002,
        snr_db=round(snr, 2),
        noise_floor_db=round(20 * math.log10(max(noise_energy**0.5, 1e-12)), 2),
        clipping=clipping,
        frequency_confidence=round(confidence, 3),
        microphone_level=round(min(1.0, level * 2), 3),
        reliability=reliability,
        recommended_profile=profile,
    )


def profile_config(profile: str) -> ModemConfig:
    profiles = {
        "MAXIMUM_RELIABILITY": ModemConfig(symbol_rate=180, frequencies=(1000, 1500, 2000, 2500)),
        "BALANCED": ModemConfig(symbol_rate=300),
        "MAXIMUM_SPEED": ModemConfig(symbol_rate=500, frequencies=(1200, 2000, 2800, 3600)),
    }
    try:
        return profiles[profile.upper()]
    except KeyError as exc:
        raise ValueError(f"unknown profile {profile}") from exc
