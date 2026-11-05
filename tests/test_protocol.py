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


# --- multi-part messages -------------------------------------------------------

def test_pack_ranges_respects_budget_and_order():
    from lanboard.common.protocol import pack_ranges
    assert pack_ranges([], 10) == [(0, 0)]
    assert pack_ranges([3, 3, 3, 3], 8) == [(0, 2), (2, 4)]           # +1 per item for the comma
    assert pack_ranges([50, 1, 1], 10) == [(0, 1), (1, 3)]            # oversized item alone
    assert pack_ranges([1] * 5, 100, max_items=2) == [(0, 2), (2, 4), (4, 5)]


def test_encode_parts_small_payload_is_one_plain_frame():
    from lanboard.common.messages import MsgType
    from lanboard.common.protocol import encode_parts
    payload = {"seq": 1, "board": {"seq": 1, "objects": [{"id": "a"}]}, "hash": "h"}
    frames = encode_parts(MsgType.SNAPSHOT, payload)
    assert len(frames) == 1 and FrameDecoder().feed(frames[0]) == [(MsgType.SNAPSHOT, payload)]


def test_encode_parts_beyond_max_frame(monkeypatch):
    from lanboard.common import protocol
    from lanboard.common.messages import MsgType
    monkeypatch.setattr(protocol, "MAX_FRAME", 4096)
    objs = [{"id": f"o{i}", "t": "x" * 100} for i in range(200)]       # ~25 KB > MAX_FRAME
    payload = {"seq": 9, "board": {"seq": 9, "objects": objs}, "hash": "h"}
    frames = protocol.encode_parts(MsgType.SNAPSHOT, payload)
    assert len(frames) > 6 and all(len(f) <= 4096 for f in frames)
    dec, asm, done = FrameDecoder(), protocol.PartAssembler(), []
    for f in reversed(frames):                                         # any order works
        for t, p in dec.feed(f):
            out = asm.add(t, p)
            if out is not None:
                done.append(out)
    assert done == [payload] and asm.pending == 0


def test_part_assembler_ignores_bad_parts_and_bounds_memory():
    from lanboard.common.messages import MsgType
    from lanboard.common.protocol import PartAssembler
    asm = PartAssembler(max_pending=2)
    assert asm.add(MsgType.SNAPSHOT, {"part": 7, "parts": 2, "xid": 1}) is None
    assert asm.pending == 0                                            # out-of-range index
    for xid in range(5):
        asm.add(MsgType.SNAPSHOT, {"part": 0, "parts": 2, "xid": xid, "board": {"objects": []}})
    assert asm.pending == 2                                            # oldest transfers dropped
    asm.clear()
    assert asm.pending == 0
