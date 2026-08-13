import pytest

from backend.protocol.frames import Frame, FrameType, decode_frame


def test_frame_round_trip_and_crc():
    frame = Frame(0x1234, FrameType.DATA, 7, 12, b"binary\x00payload")
    assert decode_frame(frame.encode()) == frame
    damaged = bytearray(frame.encode())
    damaged[-5] ^= 0x80
    with pytest.raises(ValueError, match="CRC"):
        decode_frame(bytes(damaged))


def test_frame_rejects_trailing_bytes():
    frame = Frame(1, FrameType.END, 3, 4, b"done")
    with pytest.raises(ValueError, match="length"):
        decode_frame(frame.encode() + b"extra")
