"""LANBoard wire framing.

TCP is a byte stream with no message boundaries, so every message is framed:

    +----------------------+----------+------------------------------+
    | length N (uint32 BE) | type u8  | payload: N-1 bytes UTF-8 JSON |
    +----------------------+----------+------------------------------+

``length`` counts the type byte plus the payload. Frames larger than
``MAX_FRAME`` are rejected (a defence against garbage/hostile peers).

The decoder is incremental: feed it whatever bytes ``recv()`` returned and it
yields every complete frame, keeping partial data buffered for the next call.
The same decoder is used by the asyncio server and the Qt client.
"""

from __future__ import annotations

import json
import struct
from typing import Any

from .config import MAX_FRAME

_HEADER = struct.Struct(">IB")    # length (u32) + type (u8)
HEADER_SIZE = 4                   # length prefix size; the type byte is counted in length


class ProtocolError(Exception):
    """Raised when the peer sends bytes that cannot be a valid frame."""


def encode(msg_type: int, payload: dict[str, Any] | None = None) -> bytes:
    """Serialise one message into a framed byte string ready for ``send``."""
    body = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    length = len(body) + 1
    if length > MAX_FRAME:
        raise ProtocolError(f"frame too large: {length} bytes")
    return _HEADER.pack(length, int(msg_type)) + body


class FrameDecoder:
    """Incremental frame decoder.

    >>> d = FrameDecoder()
    >>> data = encode(1, {"a": 1})
    >>> d.feed(data[:3]), d.feed(data[3:])
    ([], [(1, {'a': 1})])
    """

    __slots__ = ("_buf", "max_frame", "bytes_in", "frames_in")

    def __init__(self, max_frame: int = MAX_FRAME) -> None:
        self._buf = bytearray()
        self.max_frame = max_frame
        self.bytes_in = 0
        self.frames_in = 0

    def feed(self, data: bytes) -> list[tuple[int, dict[str, Any]]]:
        """Add received bytes; return all frames that are now complete."""
        self._buf += data
        self.bytes_in += len(data)
        out: list[tuple[int, dict[str, Any]]] = []
        buf = self._buf
        pos = 0
        while len(buf) - pos >= HEADER_SIZE:
            (length,) = struct.unpack_from(">I", buf, pos)
            if length < 1 or length > self.max_frame:
                raise ProtocolError(f"bad frame length {length}")
            end = pos + HEADER_SIZE + length
            if len(buf) < end:
                break                                   # wait for the rest of this frame
            msg_type = buf[pos + HEADER_SIZE]
            body = bytes(buf[pos + HEADER_SIZE + 1:end])
            try:
                payload = json.loads(body) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProtocolError(f"invalid JSON payload: {exc}") from exc
            if not isinstance(payload, dict):
                raise ProtocolError("payload must be a JSON object")
            out.append((msg_type, payload))
            pos = end
        if pos:
            del buf[:pos]                               # drop consumed bytes in one go
        self.frames_in += len(out)
        return out

    @property
    def buffered(self) -> int:
        """Bytes waiting for the rest of a frame."""
        return len(self._buf)
