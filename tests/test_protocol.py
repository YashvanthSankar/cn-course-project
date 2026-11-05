"""Framing edge cases: TCP may split or merge frames arbitrarily."""
import struct

import pytest

from lanboard.common.protocol import FrameDecoder, ProtocolError, encode


def frames(*msgs):
    return b"".join(encode(t, p) for t, p in msgs)


def test_roundtrip():
    assert FrameDecoder().feed(encode(5, {"x": [1, 2], "s": "héllo"})) == [(5, {"x": [1, 2], "s": "héllo"})]


def test_byte_by_byte():
    data = frames((1, {"a": 1}), (2, {"b": "two"}))
    d, out = FrameDecoder(), []
    for i in range(len(data)):
        out += d.feed(data[i:i + 1])
    assert out == [(1, {"a": 1}), (2, {"b": "two"})]
    assert d.buffered == 0


def test_many_frames_one_read():
    msgs = [(0x21, {"seq": i}) for i in range(100)]
    assert FrameDecoder().feed(frames(*msgs)) == msgs


def test_frame_split_across_three_reads():
    data = encode(3, {"text": "x" * 1000})
    d = FrameDecoder()
    assert d.feed(data[:2]) == []
    assert d.feed(data[2:500]) == []
    assert d.feed(data[500:]) == [(3, {"text": "x" * 1000})]


def test_empty_payload():
    assert FrameDecoder().feed(encode(8)) == [(8, {})]


def test_oversize_rejected():
    with pytest.raises(ProtocolError):
        FrameDecoder(max_frame=100).feed(struct.pack(">I", 101) + b"\x01")


def test_zero_length_rejected():
    with pytest.raises(ProtocolError):
        FrameDecoder().feed(struct.pack(">I", 0))


def test_bad_json_rejected():
    body = b"{not json"
    with pytest.raises(ProtocolError):
        FrameDecoder().feed(struct.pack(">IB", len(body) + 1, 1) + body)


def test_non_object_rejected():
    body = b"[1,2]"
    with pytest.raises(ProtocolError):
        FrameDecoder().feed(struct.pack(">IB", len(body) + 1, 1) + body)
