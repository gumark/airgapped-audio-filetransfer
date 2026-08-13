from backend.fec import ReedSolomon


def test_reed_solomon_recovers_multiple_erasures():
    coder = ReedSolomon(data_shards=8, parity_shards=4)
    original = [bytes(((i * 17 + j) % 256 for j in range(257))) for i in range(8)]
    parity = coder.encode(original)
    available = {i: shard for i, shard in enumerate(original + parity) if i not in {1, 4, 8}}
    recovered = coder.decode(available, 257)
    assert recovered[1] == original[1]
    assert recovered[4] == original[4]
    assert 8 not in recovered
