"""
Cryptographic module for authenticated encryption.

Provides end-to-end encryption using modern algorithms.
Keys are NEVER transmitted through the audio channel.
"""

from .encryption import CryptoEngine

__all__ = ["CryptoEngine"]
