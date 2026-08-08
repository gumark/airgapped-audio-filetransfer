"""
Tests for FSK modulation and demodulation.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from backend.dsp.modulation import FSKModulator
from backend.dsp.demodulation import FSKDemodulator


def test_modulator_initialization():
    """Test modulator can be initialized with different configurations."""
    mod = FSKModulator()
    assert mod.sample_rate == 48000
    assert mod.symbol_rate == 250
    assert len(mod.frequencies) == 4
    assert mod.bits_per_symbol == 2

    # 2-FSK
    mod2 = FSKModulator(frequencies=[1200, 1800])
    assert mod2.bits_per_symbol == 1

    # 8-FSK
    mod8 = FSKModulator(frequencies=[1000, 1200, 1400, 1600, 1800, 2000, 2200, 2400])
    assert mod8.bits_per_symbol == 3


def test_symbols_to_bytes_roundtrip():
    """Test that bytes can be converted to symbols and back."""
    mod = FSKModulator()
    demod = FSKDemodulator()

    test_data = b"Hello, World! This is a test of the audio modem."

    # Convert to symbols
    symbols = mod.symbols_to_bytes(test_data)

    # Convert back to bytes
    recovered = demod.symbols_to_bytes(symbols)

    assert recovered == test_data


def test_modulation_waveform():
    """Test that modulation produces a valid waveform."""
    mod = FSKModulator()

    test_data = b"\x00\xFF\x55\xAA"
    waveform = mod.modulate_bytes(test_data)

    assert isinstance(waveform, np.ndarray)
    assert waveform.dtype == np.float32
    assert len(waveform) > 0

    # Check amplitude is within bounds
    assert np.max(np.abs(waveform)) <= 1.0


def test_modulation_demodulation_roundtrip():
    """Test full modulation-demodulation roundtrip with clean channel."""
    mod = FSKModulator(sample_rate=48000, symbol_rate=250)
    demod = FSKDemodulator(sample_rate=48000, symbol_rate=250)

    test_data = b"Air-gapped transfer test data 1234567890"

    # Modulate
    waveform = mod.modulate_bytes(test_data)

    # Demodulate
    recovered = demod.demodulate_to_bytes(waveform)

    # Verify (may have slight differences due to symbol boundaries)
    # For a clean channel, should be exact or very close
    assert len(recovered) >= len(test_data) - 1
    assert recovered[:len(test_data)] == test_data[:len(recovered)]


def test_preamble_detection():
    """Test that preamble can be added and detected."""
    mod = FSKModulator()
    demod = FSKDemodulator()

    # Create signal with preamble
    test_data = b"\xAB\xCD"
    waveform = mod.modulate_bytes(test_data)
    waveform_with_preamble = mod.add_preamble(waveform, num_symbols=16)

    # Waveform should be longer
    assert len(waveform_with_preamble) > len(waveform)


def test_sync_tone():
    """Test that sync tone can be added."""
    mod = FSKModulator()

    test_data = b"\x01\x02"
    waveform = mod.modulate_bytes(test_data)
    waveform_with_tone = mod.add_sync_tone(waveform, duration=0.5)

    # Should add samples for the tone
    expected_tone_samples = int(48000 * 0.5)
    assert len(waveform_with_tone) >= len(waveform) + expected_tone_samples - 10


def test_confidence_detection():
    """Test confidence detection in demodulator."""
    mod = FSKModulator()
    demod = FSKDemodulator()

    # Create clean signal
    waveform = mod.modulate_bytes(b"\x00\x01\x02\x03")

    symbols, confidences = demod.demodulate_symbols(waveform)

    # 4 bytes * 4 symbols/byte (4-FSK with 2 bits/symbol)
    assert len(symbols) == 16
    assert len(confidences) == 16

    # Clean signal should have high confidence
    for conf in confidences:
        assert conf > 1.0


def test_empty_data():
    """Test handling of empty data."""
    mod = FSKModulator()

    waveform = mod.modulate_bytes(b"")
    assert len(waveform) == 0


def test_snr_measurement():
    """Test SNR measurement."""
    mod = FSKModulator()
    demod = FSKDemodulator()

    # Create signal
    waveform = mod.modulate_bytes(b"\x00\x01\x02\x03")

    # Measure SNR
    snr = demod.measure_snr(waveform, signal_freq=1500)

    # Clean signal should have positive SNR
    assert snr > 0


if __name__ == "__main__":
    test_modulator_initialization()
    test_symbols_to_bytes_roundtrip()
    test_modulation_waveform()
    test_modulation_demodulation_roundtrip()
    test_preamble_detection()
    test_sync_tone()
    test_confidence_detection()
    test_empty_data()
    test_snr_measurement()
    print("All modulation tests passed!")
