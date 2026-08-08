"""
Reed-Solomon Forward Error Correction implementation.

Wraps the reedsolo library to provide FEC encoding/decoding with
configurable redundancy levels. The FEC overhead determines how many
extra parity symbols are added to protect against errors.

Reed-Solomon can correct up to t errors where:
    t = nsym // 2

Where nsym is the number of error correction symbols (parity bytes).

For example:
    nsym=10  → can correct up to 5 byte errors
    nsym=20  → can correct up to 10 byte errors
    nsym=50  → can correct up to 25 byte errors

The reedsolo library uses GF(2^8) which means each symbol is one byte.
"""

import reedsolo
import numpy as np
from typing import List, Tuple, Optional


class ReedSolomonFEC:
    """
    Reed-Solomon Forward Error Correction encoder/decoder.

    Provides chunked encoding so that very large data can be processed
    without loading everything into memory at once.
    """

    def __init__(
        self,
        nsym: int = 10,
        fcr: int = 1,           # First consecutive root
        prim: int = 0x11D,      # Primitive polynomial for GF(2^8)
    ):
        """
        Args:
            nsym: Number of error correction symbols (parity bytes).
                  Higher = more redundancy = can correct more errors.
            fcr: First consecutive root of the generator polynomial
            prim: Primitive polynomial for GF(2^8)
        """
        self.nsym = nsym
        self.rs = reedsolo.RSCodec(
            nsym=nsym,
            fcr=fcr,
            prim=prim,
        )

    @staticmethod
    def overhead_to_nsym(overhead_fraction: float, block_size: int = 255) -> int:
        """
        Convert an overhead percentage to the number of ECC symbols.

        Args:
            overhead_fraction: FEC overhead as fraction (e.g., 0.25 = 25%)
            block_size: RS block size (max 255 for GF(2^8))

        Returns:
            Number of ECC symbols needed
        """
        # nsym should be at least 2 (for 1 error correction)
        # and at most block_size - 1
        nsym = max(2, int(block_size * overhead_fraction))
        nsym = min(nsym, block_size - 1)
        return nsym

    @staticmethod
    def nsym_to_overhead(nsym: int, data_size: int) -> float:
        """
        Convert nsym to overhead percentage.

        Args:
            nsym: Number of ECC symbols
            data_size: Size of data block

        Returns:
            Overhead as fraction (e.g., 0.25 = 25%)
        """
        return nsym / data_size if data_size > 0 else 0.0

    def encode(self, data: bytes) -> bytes:
        """
        Encode data with Reed-Solomon parity bytes.

        Automatically chunks data into blocks that fit within the RS block size.
        Each block is independently encoded.

        Args:
            data: Input data bytes

        Returns:
            Encoded data with parity bytes appended
        """
        max_data_per_block = 255 - self.nsym
        
        # If data fits in one block, encode directly
        if len(data) <= max_data_per_block:
            return bytes(self.rs.encode(bytearray(data)))
        
        # Otherwise, encode in chunks
        all_encoded = []
        for i in range(0, len(data), max_data_per_block):
            chunk = data[i:i + max_data_per_block]
            encoded_chunk = bytes(self.rs.encode(bytearray(chunk)))
            all_encoded.append(encoded_chunk)
        
        return b"".join(all_encoded)

    def decode(self, data: bytes) -> Tuple[bytes, dict]:
        """
        Decode Reed-Solomon encoded data, correcting errors.

        Args:
            data: Encoded data (with parity bytes)

        Returns:
            Tuple of (decoded_data, stats)
            stats contains: corrected_count, uncorrectable_errors
        """
        stats = {"corrected_count": 0, "uncorrectable_errors": False, "blocks_decoded": 0}
        
        block_size = 255  # Total RS block size (data + parity)
        
        all_decoded = []
        
        # Decode in blocks
        for i in range(0, len(data), block_size):
            block = data[i:i + block_size]
            if len(block) < block_size:
                # Last block might be shorter, skip or handle specially
                # For now, try to decode what we have
                try:
                    decoded = self.rs.decode(bytearray(block))
                    if isinstance(decoded, tuple):
                        all_decoded.append(bytes(decoded[0]))
                    else:
                        all_decoded.append(bytes(decoded))
                    stats["blocks_decoded"] += 1
                except reedsolo.ReedSolomonError:
                    stats["uncorrectable_errors"] = True
                continue
            
            try:
                decoded = self.rs.decode(bytearray(block))
                if isinstance(decoded, tuple):
                    all_decoded.append(bytes(decoded[0]))
                else:
                    all_decoded.append(bytes(decoded))
                stats["blocks_decoded"] += 1
            except reedsolo.ReedSolomonError as e:
                stats["uncorrectable_errors"] = True
                raise ValueError(f"FEC decode failed: {e}") from e
        
        stats["corrected_count"] = self.nsym // 2  # Maximum that could be corrected
        return b"".join(all_decoded), stats

    def encode_chunks(
        self, data: bytes, chunk_size: int = 223
    ) -> List[bytes]:
        """
        Encode data in chunks for large files.

        Each chunk is independently encoded, allowing partial recovery
        if some chunks are lost.

        Args:
            data: Input data bytes
            chunk_size: Size of each data chunk (before adding parity)
                       Must be <= 255 - nsym for RS coding

        Returns:
            List of encoded chunks
        """
        max_data = 255 - self.nsym
        if chunk_size > max_data:
            chunk_size = max_data

        chunks = []
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i + chunk_size]
            encoded = self.encode(chunk)
            chunks.append(encoded)

        return chunks

    def decode_chunks(self, chunks: List[bytes]) -> Tuple[bytes, dict]:
        """
        Decode multiple RS-encoded chunks.

        Args:
            chunks: List of encoded chunks

        Returns:
            Tuple of (decoded_data, stats)
        """
        all_data = bytearray()
        total_stats = {"total_chunks": len(chunks), "recovered_chunks": 0,
                       "failed_chunks": 0}

        for chunk in chunks:
            try:
                decoded, stats = self.decode(chunk)
                all_data.extend(decoded)
                if not stats["uncorrectable_errors"]:
                    total_stats["recovered_chunks"] += 1
                else:
                    total_stats["failed_chunks"] += 1
            except ValueError:
                total_stats["failed_chunks"] += 1

        return bytes(all_data), total_stats

    def get_redundancy_info(self, data_size: int) -> dict:
        """
        Get information about the FEC configuration.

        Returns:
            Dictionary with redundancy statistics
        """
        encoded_size = data_size + self.nsym
        overhead_pct = (self.nsym / data_size * 100) if data_size > 0 else 0
        correctable = self.nsym // 2

        return {
            "nsym": self.nsym,
            "data_size": data_size,
            "encoded_size": encoded_size,
            "overhead_percent": round(overhead_pct, 1),
            "correctable_errors": correctable,
            "redundancy_ratio": round(encoded_size / data_size, 2) if data_size > 0 else 0,
        }
