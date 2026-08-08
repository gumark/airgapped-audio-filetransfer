"""
File transfer module.

Handles the complete file transfer pipeline:
1. File chunking and streaming
2. Frame assembly
3. FEC encoding
4. Encryption
5. Audio modulation and output
6. Audio input and demodulation
7. Frame disassembly and reassembly
8. File reconstruction and verification
"""

from .manager import TransferManager

__all__ = ["TransferManager"]
