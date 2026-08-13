from pathlib import Path

from backend.transfer.session import TransferSettings, prepare_transfer, receive_frames


def test_streaming_file_round_trip_with_erased_frames(tmp_path: Path):
    source = tmp_path / "arbitrary.bin"
    source.write_bytes(bytes((index * 31 + 7) % 256 for index in range(73_000)))
    settings = TransferSettings(chunk_size=4096, fec_overhead=40, fec_group_size=8, compression="gzip")
    prepared = prepare_transfer(source, settings, password="shared out-of-band password", transfer_id=0xAABBCCDD)
    frames = list(prepared.frames())
    # Lose two data frames in each early group. They are not retransmitted;
    # parity must repair the erasures.
    dropped = {frame.sequence for frame in frames if frame.frame_type.name == "DATA" and (frame.sequence - 3) % 8 in {1, 5}}
    received = receive_frames((frame for frame in frames if frame.sequence not in dropped), tmp_path / "received", password="shared out-of-band password")
    assert received.hash_verified
    assert received.frames_recovered >= 2
    assert received.output_path.read_bytes() == source.read_bytes()
