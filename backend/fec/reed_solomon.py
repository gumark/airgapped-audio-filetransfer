"""Small Reed-Solomon erasure coder over GF(256).

This is intentionally an erasure code: the frame CRC tells the receiver which
shards are missing, and parity reconstructs them without a retransmission path.
The implementation uses a Vandermonde parity matrix and Gaussian elimination.
"""
from __future__ import annotations

from dataclasses import dataclass

_EXP = [0] * 512
_LOG = [0] * 256
_value = 1
for _i in range(255):
    _EXP[_i] = _value
    _LOG[_value] = _i
    _value <<= 1
    if _value & 0x100:
        _value ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return 0 if not a or not b else _EXP[_LOG[a] + _LOG[b]]


def _inv(a: int) -> int:
    if not a:
        raise ZeroDivisionError("zero has no inverse in GF(256)")
    return _EXP[255 - _LOG[a]]


def _matrix_row(index: int, width: int, parity_width: int | None = None) -> list[int]:
    if index < width:
        return [1 if i == index else 0 for i in range(width)]
    # A short final file group is encoded with the full configured data width
    # and zero-padded shards. Preserve that original Vandermonde base while
    # solving only for the real data columns.
    parity_width = parity_width or width
    base = parity_width + index - parity_width + 1
    return [_EXP[(_LOG[base] * i) % 255] if i else 1 for i in range(width)]


def _mat_inv(matrix: list[list[int]]) -> list[list[int]]:
    n = len(matrix)
    augmented = [row[:] + [1 if r == c else 0 for c in range(n)] for r, row in enumerate(matrix)]
    for col in range(n):
        pivot = next((r for r in range(col, n) if augmented[r][col]), None)
        if pivot is None:
            raise ValueError("singular Reed-Solomon matrix")
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        scale = _inv(augmented[col][col])
        augmented[col] = [_mul(value, scale) for value in augmented[col]]
        for row in range(n):
            if row == col or not augmented[row][col]:
                continue
            factor = augmented[row][col]
            augmented[row] = [a ^ _mul(factor, b) for a, b in zip(augmented[row], augmented[col])]
    return [row[n:] for row in augmented]


def _linear_combine(coefficients: list[int], shards: list[bytes]) -> bytes:
    size = len(shards[0])
    out = bytearray(size)
    for coefficient, shard in zip(coefficients, shards):
        if len(shard) != size:
            raise ValueError("all shards must have equal length")
        if coefficient == 1:
            for i, value in enumerate(shard):
                out[i] ^= value
        elif coefficient:
            for i, value in enumerate(shard):
                out[i] ^= _mul(coefficient, value)
    return bytes(out)


@dataclass(frozen=True, slots=True)
class ReedSolomon:
    """Encode groups with ``data_shards`` data and ``parity_shards`` parity."""

    data_shards: int = 16
    parity_shards: int = 4

    def __post_init__(self) -> None:
        if not 1 <= self.data_shards <= 128:
            raise ValueError("data_shards must be between 1 and 128")
        if not 1 <= self.parity_shards <= 127:
            raise ValueError("parity_shards must be between 1 and 127")
        if self.data_shards + self.parity_shards > 255:
            raise ValueError("a GF(256) group cannot exceed 255 shards")

    def encode(self, data: list[bytes]) -> list[bytes]:
        """Return parity shards, padding the final data group with zero shards."""
        if not data or len(data) > self.data_shards:
            raise ValueError("invalid data shard count")
        size = max(len(shard) for shard in data)
        padded = [shard.ljust(size, b"\0") for shard in data]
        padded += [b"\0" * size] * (self.data_shards - len(padded))
        parity: list[bytes] = []
        for parity_index in range(self.parity_shards):
            row = _matrix_row(self.data_shards + parity_index, self.data_shards)
            parity.append(_linear_combine(row, padded))
        return parity

    def decode(self, shards: dict[int, bytes], shard_size: int, data_count: int | None = None) -> dict[int, bytes]:
        """Recover missing data shards, including a short final group."""
        width = data_count or self.data_shards
        available = [(index, shard) for index, shard in sorted(shards.items()) if 0 <= index < self.data_shards + self.parity_shards]
        if len(available) < width:
            raise ValueError("not enough shards to recover group")
        selected = available[:width]
        matrix = [_matrix_row(index, width, self.data_shards) for index, _ in selected]
        inverse = _mat_inv(matrix)
        source = [shard.ljust(shard_size, b"\0") for _, shard in selected]
        recovered_data = [_linear_combine(row, source) for row in inverse]
        return {index: recovered_data[index] for index in range(width) if index not in shards}
