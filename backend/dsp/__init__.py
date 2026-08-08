"""
DSP layer for the air-gapped audio transfer system.

Handles modulation, demodulation, synchronization, and signal analysis.
This module is designed to be replaceable without affecting the GUI or protocol layer.
"""

from .modulation import FSKModulator
from .demodulation import FSKDemodulator
from .synchronization import SyncDetector
from .spectrum import SpectrumAnalyzer
from .calibration import CalibrationEngine
from .channel import SimulatedChannel, ChannelParams

__all__ = [
    "FSKModulator",
    "FSKDemodulator",
    "SyncDetector",
    "SpectrumAnalyzer",
    "CalibrationEngine",
    "SimulatedChannel",
    "ChannelParams",
]
