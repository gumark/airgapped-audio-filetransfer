"""
Tests for FSK demodulation.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from backend.dsp.modulation import FSKModulator
from backend.dsp.demodulation import FSKDemodulator, goertzel_magnitude


def test_goertzel_detection():
    """Test Goertzel algorithm detects known frequencies."""
    sample_rate = 48000
    duration = 0.1
    freq = 1500

    t = np.arange(int(sample_rate * duration), dtype=np.float64)
    signal = 0.8 * np.sin(2 * np.pi * freq * t / sample_rate)

    power = goertzel_magnitude(signal, freq, sample_rate)
    assert power > 0

    # Should be much stronger at 1500 Hz than at other frequencies
    power_other = goertzel_magnitude(signal, 3000, sample_rate)
    assert power > power_other * 10


def test_demodulator_initialization():
    """Test demodulator initialization."""
    demod = FSKDemodulator()

    assert demod.sample_rate == 48000
    assert demod.symbol_rate == 250
    assert demod.bits_per_symbol == 2
    assert demod.samples_per_symbol == 192


def test_single_symbol_detection():
    """Test detection of a single symbol."""
    mod = FSKModulator()
    demod = FSKDemodulator()

    # Create a single symbol
    waveform = mod.modulate_symbols([0])  # Symbol 0

    # Detect
    symbol, confidence, powers = demod.detect_symbol(waveform)

    assert symbol == 0
    assert confidence > 1.0


def test_multiple_symbols():
    """Test detection of multiple symbols."""
    mod = FSKModulator()
    demod = FSKDemodulator()

    # Create known symbol sequence
    symbols = [0, 1, 2, 3, 0, 1, 2, 3]
    waveform = mod.modulate_symbols(symbols)

    # Demodulate
    detected_symbols, confidences = demod.demodulate_symbols(waveform)

    assert detected_symbols == symbols


def test_bytes_roundtrip():
    """Test bytes to symbols to bytes roundtrip."""
    mod = FSKModulator()
    demod = FSKDemodulator()

    test_bytes = b"\x00\x01\x02\x03\xFF\xAA\x55"
    symbols = mod.symbols_to_bytes(test_bytes)
    recovered = demod.symbols_to_bytes(symbols)

    assert recovered == test_bytes


def test_clipping_detection():
    """Test clipping detection."""
    demod = FSKDemodulator()

    # Clean signal
    clean = np.sin(np.linspace(0, 10, 1000)) * 0.5
    assert demod.detect_clipping(clean) == 0.0

    # Clipped signal
    clipped = np.sin(np.linspace(0, 10, 1000))
    clipped = np.clip(clipped, -0.95, 0.95)
    assert demod.detect_clipping(clipped) > 0.0


if __name__ == "__main__":
    test_goertzel_detection()
    test_demodulator_initialization()
    test_single_symbol_detection()
    test_multiple_symbols()
    test_bytes_roundtrip()
    test_clipping_detection()
    print("All demodulation tests passed!")
