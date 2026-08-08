"""
Forward Error Correction (FEC) module.

Implements Reed-Solomon coding for error detection and correction
over the audio channel. This allows the receiver to recover from
corrupted or missing data without retransmission.
"""

from .reed_solomon import ReedSolomonFEC

__all__ = ["ReedSolomonFEC"]
