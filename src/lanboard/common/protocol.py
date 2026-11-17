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

Multi-part messages
-------------------
A whole board (SNAPSHOT) or the whole time-lapse (HISTORY) can be larger than
one frame. The sender then splits the payload's big lists into consecutive
slices (:func:`pack_ranges`) and sends ``parts`` frames of the same type, each
tagged ``{part, parts, xid}``. The receiver collects them with
:class:`PartAssembler` and only hands on the reassembled payload once every
part is there, so a half-received board is never applied. Small payloads are
sent as one ordinary frame without those fields.
"""

from __future__ import annotations

import itertools
import json
import struct
from typing import Any, Sequence

from . import config as C
from .config import MAX_FRAME
from .messages import MsgType

_HEADER = struct.Struct(">IB")
HEADER_SIZE = 4


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
                break
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
            del buf[:pos]
        self.frames_in += len(out)
        return out

    @property
    def buffered(self) -> int:
        """Bytes waiting for the rest of a frame."""
        return len(self._buf)


SPLIT_LISTS: dict[int, tuple[tuple[str, ...], ...]] = {
    MsgType.SNAPSHOT: (("board", "objects"),),
    MsgType.HISTORY: (("base", "objects"), ("ops",)),
}


def json_size(obj: Any) -> int:
    """Exact number of bytes ``obj`` takes inside an encoded frame."""
    return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def part_budget() -> int:
    """Payload bytes to aim for per part. Half a frame leaves room for the other fields."""
    return max(1, min(C.PART_BYTES, MAX_FRAME // 2))


def pack_ranges(sizes: Sequence[int], budget: int, max_items: int | None = None) -> list[tuple[int, int]]:
    """Split items into consecutive ``[start, end)`` ranges of at most ``budget`` bytes.

    Greedy first-fit in order (order matters: ops must stay in seq order). An
    item bigger than ``budget`` on its own gets a range of its own; it still
    fits in a frame because single ops/objects are capped well below
    ``MAX_FRAME``. ``max_items`` optionally caps the count per range as well.
    Always returns at least one (possibly empty) range.
    """
    ranges: list[tuple[int, int]] = []
    start, acc = 0, 0
    for i, n in enumerate(sizes):
        n += 1
        full = acc + n > budget or (max_items is not None and i - start >= max_items)
        if i > start and full:
            ranges.append((start, i))
            start, acc = i, 0
        acc += n
    if start < len(sizes) or not ranges:
        ranges.append((start, len(sizes)))
    return ranges


def _get_path(payload: dict, path: tuple[str, ...]) -> Any:
    for key in path:
        payload = payload.get(key) if isinstance(payload, dict) else None
    return payload


def _set_path(payload: dict, path: tuple[str, ...], value: Any) -> None:
    """Set a nested key, copying the dicts on the way so the original is untouched."""
    target = payload
    for key in path[:-1]:
        target[key] = dict(target.get(key) or {})
        target = target[key]
    target[path[-1]] = value


_xids = itertools.count(1)


def encode_parts(msg_type: int, payload: dict,
                 sizes: dict[tuple[str, ...], Sequence[int]] | None = None) -> list[bytes]:
    """Encode a SNAPSHOT / HISTORY payload as one frame, or as parts if it is big.

    If the whole payload fits in :func:`part_budget` it is a single ordinary
    frame. Otherwise each list named in ``SPLIT_LISTS`` is cut into slices of
    about ``part_budget()`` bytes and every slice becomes one part: a copy of
    the payload in which that list holds the slice and the other split lists
    are empty, tagged ``{part, parts, xid}``. ``sizes`` may supply the item
    sizes per list when the caller already knows them (saves an encode).
    Raises ``ProtocolError`` only if a single item cannot fit in a frame.
    """
    budget = part_budget()
    try:
        frame = encode(msg_type, payload)
        if len(frame) <= budget:
            return [frame]
    except ProtocolError:
        pass
    paths = SPLIT_LISTS[msg_type]
    chunks: list[tuple[tuple[str, ...], list]] = []
    for path in paths:
        items = _get_path(payload, path) or []
        known = (sizes or {}).get(path)
        item_sizes = known if known is not None and len(known) == len(items) \
            else [json_size(x) for x in items]
        for a, b in pack_ranges(item_sizes, budget):
            if a < b:
                chunks.append((path, items[a:b]))
    if len(chunks) <= 1:
        return [encode(msg_type, payload)]
    xid, n = next(_xids), len(chunks)
    frames = []
    for i, (path, items) in enumerate(chunks):
        part = dict(payload)
        for other in paths:
            _set_path(part, other, [])
        _set_path(part, path, items)
        part.update(part=i, parts=n, xid=xid)
        frames.append(encode(msg_type, part))
    return frames


class PartAssembler:
    """Collects the parts of multi-part SNAPSHOT / HISTORY messages.

    :meth:`add` returns the payload to process now: a single-frame message is
    returned unchanged, a part returns ``None`` until the last missing part of
    its transfer (same ``xid``) arrives, and then the merged payload: the
    split lists concatenated in part order, every other field from part 0.
    Parts may arrive in any order and more than once (the Chaos Lab reorders
    and duplicates frames), so they are stored by index. At most
    ``max_pending`` unfinished transfers are kept; the oldest is dropped
    (the client heals that with a RESYNC like any other lost data).
    """

    def __init__(self, max_pending: int = 4) -> None:
        self.max_pending = max_pending
        self._pending: dict[tuple[int, Any], dict[int, dict]] = {}

    def clear(self) -> None:
        """Forget unfinished transfers (call on every new connection: xids restart)."""
        self._pending.clear()

    @property
    def pending(self) -> int:
        return len(self._pending)

    def add(self, msg_type: int, payload: dict) -> dict | None:
        parts, part = payload.get("parts"), payload.get("part")
        if not isinstance(parts, int) or isinstance(parts, bool) or parts <= 1:
            return payload
        if not isinstance(part, int) or isinstance(part, bool) or not 0 <= part < parts:
            return None
        key = (int(msg_type), payload.get("xid"))
        got = self._pending.pop(key, None) or {}
        got[part] = payload
        if len(got) < parts:
            self._pending[key] = got
            while len(self._pending) > self.max_pending:
                self._pending.pop(next(iter(self._pending)))
            return None
        return self._merge(int(msg_type), [got[i] for i in range(parts)])

    @staticmethod
    def _merge(msg_type: int, chunks: list[dict]) -> dict:
        first = chunks[0]
        out = {k: v for k, v in first.items() if k not in ("part", "parts", "xid")}
        for path in SPLIT_LISTS.get(msg_type, ()):
            merged: list = []
            for c in chunks:
                merged.extend(_get_path(c, path) or [])
            _set_path(out, path, merged)
        return out
