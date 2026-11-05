"""Board model shared by server and client.

The board is a dictionary of objects keyed by a globally unique id. Every change
is an *op* (a small JSON dict). The server assigns each accepted op a global
sequence number ``seq`` and every replica applies ops strictly in ``seq`` order
with :meth:`Board.apply`. ``apply`` is deterministic and total: an op that no
longer makes sense (e.g. updating an object somebody already erased) is a
no-op, never an error. Same ops in the same order => identical boards. That
is our consistency guarantee, checked at runtime with :meth:`Board.hash`.

Objects (plain dicts so they serialise directly to JSON)::

    {
      "id": "u3-17",          # "<user_id>-<counter>", unique without a round trip
      "type": "stroke",       # see OBJECT_TYPES
      "owner": "u3",          # set by the server to the submitting user
      "z": 12,                # stacking order; ties broken by id
      "style": {"color": "#e6194b", "width": 4, "fill": null, "opacity": 1.0, "font_size": 18},
      "geom": {...},          # type specific, see GEOM_KEYS
      "off": [0, 0],          # translation applied on top of geom (cheap "move")
      "deleted": false,       # tombstone; kept so undo/restore stays well defined
      "rev": 57               # seq of the last op that changed this object
    }

Ops (``op["k"]``):

    add      {k, obj}
    update   {k, id, set:{key: value}, prev:{key: value}}
             keys: "off", "z", "votes", "style.<name>", "geom.<name>"
    delete   {k, ids:[...], clear?:true}
    restore  {k, ids:[...]}
    clear    {k}                    normalised by the server into delete{ids, clear:true}
    replace  {k, objects:[...]}     load a saved board / restore a version
    batch    {k, ops:[...]}         several ops as one atomic, single-undo step
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Iterable

from . import config as C

OBJECT_TYPES = frozenset({"stroke", "line", "arrow", "rect", "ellipse", "text", "sticky", "image"})
OP_KINDS = frozenset({"add", "update", "delete", "restore", "clear", "replace", "batch"})

# geometry keys each type must carry
GEOM_KEYS: dict[str, tuple[str, ...]] = {
    "stroke": ("pts",),
    "line": ("x1", "y1", "x2", "y2"),
    "arrow": ("x1", "y1", "x2", "y2"),
    "rect": ("x", "y", "w", "h"),
    "ellipse": ("x", "y", "w", "h"),
    "text": ("x", "y", "text"),
    "sticky": ("x", "y", "w", "h", "text"),
    "image": ("x", "y", "w", "h", "blob"),
}
TEXT_GEOM = frozenset({"text", "blob"})          # string-valued geom fields

_UPDATE_KEY = re.compile(r"^(off|z|votes|(style|geom)\.[a-z_][a-z0-9_]{0,23})$")
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class InvalidOp(ValueError):
    """The op is structurally invalid (server rejects it; never applied)."""


# ---------------------------------------------------------------------------
# validation (server side, before an op is accepted into the log)
# ---------------------------------------------------------------------------

def _num(v: Any, what: str) -> None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or abs(v) > C.MAX_COORD:
        raise InvalidOp(f"{what} must be a number within bounds")


def validate_object(obj: Any) -> None:
    if not isinstance(obj, dict):
        raise InvalidOp("obj must be an object")
    oid, typ = obj.get("id"), obj.get("type")
    if not isinstance(oid, str) or not _ID.match(oid):
        raise InvalidOp("bad object id")
    if typ not in OBJECT_TYPES:
        raise InvalidOp(f"unknown object type {typ!r}")
    style = obj.get("style", {})
    if not isinstance(style, dict) or len(json.dumps(style)) > 512:
        raise InvalidOp("bad style")
    geom = obj.get("geom")
    if not isinstance(geom, dict):
        raise InvalidOp("geom must be an object")
    for key in GEOM_KEYS[typ]:
        if key not in geom:
            raise InvalidOp(f"{typ} geom needs {key!r}")
    for key, val in geom.items():
        if key == "pts":
            if (not isinstance(val, list) or len(val) % 2 or len(val) < 2
                    or len(val) > C.MAX_STROKE_POINTS):
                raise InvalidOp("pts must be a flat even-length list")
            for n in val:
                _num(n, "point")
        elif key in TEXT_GEOM:
            if not isinstance(val, str) or len(val) > C.MAX_TEXT_LEN:
                raise InvalidOp(f"{key} must be a string <= {C.MAX_TEXT_LEN}")
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            _num(val, key)
        elif val is not None and not isinstance(val, (str, bool)):
            raise InvalidOp(f"bad geom value for {key}")
    off = obj.get("off", [0, 0])
    if not (isinstance(off, list) and len(off) == 2):
        raise InvalidOp("off must be [dx, dy]")
    for n in off:
        _num(n, "offset")
    if not isinstance(obj.get("z", 0), int):
        raise InvalidOp("z must be an int")


def validate_op(op: Any, depth: int = 0) -> None:
    """Raise :class:`InvalidOp` unless ``op`` is well formed."""
    if not isinstance(op, dict) or op.get("k") not in OP_KINDS:
        raise InvalidOp("unknown op kind")
    k = op["k"]
    if k == "add":
        validate_object(op.get("obj"))
    elif k == "update":
        if not isinstance(op.get("id"), str):
            raise InvalidOp("update needs id")
        for field in ("set", "prev"):
            d = op.get(field, {})
            if not isinstance(d, dict):
                raise InvalidOp(f"{field} must be an object")
            for key in d:
                if not _UPDATE_KEY.match(key):
                    raise InvalidOp(f"key {key!r} not updatable")
        if not op.get("set"):
            raise InvalidOp("update.set is empty")
        if len(json.dumps(op)) > 256 * 1024:
            raise InvalidOp("update too large")
    elif k in ("delete", "restore"):
        ids = op.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise InvalidOp(f"{k} needs a non-empty ids list")
        if len(ids) > C.MAX_OBJECTS:
            raise InvalidOp("too many ids")
    elif k == "replace":
        objs = op.get("objects")
        if not isinstance(objs, list) or len(objs) > C.MAX_OBJECTS:
            raise InvalidOp("replace needs objects list")
        for o in objs:
            validate_object(o)
    elif k == "batch":
        ops = op.get("ops")
        if depth > 0:
            raise InvalidOp("nested batch")
        if not isinstance(ops, list) or not ops or len(ops) > C.MAX_BATCH_OPS:
            raise InvalidOp("batch needs 1..MAX_BATCH_OPS ops")
        for sub in ops:
            if sub.get("k") in ("replace", "clear"):
                raise InvalidOp(f"{sub.get('k')} not allowed in batch")
            validate_op(sub, depth + 1)


def op_object_ids(op: dict) -> set[str]:
    """Ids an op touches (used for permission checks and UI highlights)."""
    k = op.get("k")
    if k == "add":
        return {op["obj"]["id"]}
    if k == "update":
        return {op["id"]}
    if k in ("delete", "restore"):
        return set(op["ids"])
    if k == "batch":
        out: set[str] = set()
        for sub in op["ops"]:
            out |= op_object_ids(sub)
        return out
    return set()


# ---------------------------------------------------------------------------
# helpers for dotted keys ("style.color", "geom.text", "off", "z")
# ---------------------------------------------------------------------------

def get_key(obj: dict, key: str) -> Any:
    if "." in key:
        group, name = key.split(".", 1)
        return obj.get(group, {}).get(name)
    return obj.get(key)


def set_key(obj: dict, key: str, value: Any) -> None:
    if "." in key:
        group, name = key.split(".", 1)
        obj.setdefault(group, {})[name] = copy.deepcopy(value)
    else:
        obj[key] = copy.deepcopy(value)


def normalise_object(obj: dict, owner: str | None = None, rev: int = 0) -> dict:
    o = copy.deepcopy(obj)
    o.setdefault("style", {})
    o.setdefault("off", [0, 0])
    o.setdefault("z", 0)
    o["deleted"] = bool(o.get("deleted", False))
    if owner is not None:
        o["owner"] = owner
    o.setdefault("owner", "")
    o["rev"] = rev
    return o


# ---------------------------------------------------------------------------
# the board
# ---------------------------------------------------------------------------

class Board:
    """An ordered set of whiteboard objects plus the seq of the last applied op."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.seq = 0
        self._hash_cache: tuple[int, str] | None = None

    # -- queries -------------------------------------------------------------
    def get(self, oid: str) -> dict | None:
        return self.objects.get(oid)

    def is_live(self, oid: str) -> bool:
        o = self.objects.get(oid)
        return o is not None and not o["deleted"]

    def live(self) -> Iterable[dict]:
        """Live objects in paint order (z, then id)."""
        return sorted((o for o in self.objects.values() if not o["deleted"]),
                      key=lambda o: (o.get("z", 0), o["id"]))

    def live_ids(self) -> list[str]:
        return [o["id"] for o in self.objects.values() if not o["deleted"]]

    def max_z(self) -> int:
        return max((o.get("z", 0) for o in self.objects.values()), default=0)

    def __len__(self) -> int:
        return sum(1 for o in self.objects.values() if not o["deleted"])

    # -- normalisation (server, before assigning seq) ------------------------
    def normalise(self, op: dict, user_id: str) -> dict:
        """Return the op that will actually be logged.

        * ``clear`` becomes ``delete`` of every currently live id (so it can be undone)
        * ``add`` gets ``owner`` forced to the submitting user
        """
        k = op["k"]
        if k == "clear":
            return {"k": "delete", "ids": self.live_ids(), "clear": True}
        if k == "add":
            op = copy.deepcopy(op)
            op["obj"]["owner"] = user_id
            return op
        if k == "batch":
            return {"k": "batch", "ops": [self.normalise(s, user_id) for s in op["ops"]]}
        return op

    # -- mutation --------------------------------------------------------------
    def apply(self, op: dict, seq: int | None = None) -> set[str]:
        """Apply one op. Returns ids whose rendering changed (empty set = no-op).

        ``seq`` is the server sequence number; replicas pass the seq from the
        OP message. Local/optimistic application passes ``None``.
        """
        rev = seq if seq is not None else self.seq
        changed = self._apply(op, rev)
        if seq is not None:
            self.seq = seq
        if changed:
            self._hash_cache = None
        return changed

    def _apply(self, op: dict, rev: int) -> set[str]:
        k = op.get("k")
        if k == "add":
            obj = op["obj"]
            if obj["id"] in self.objects:
                return set()                                 # duplicate add -> no-op
            self.objects[obj["id"]] = normalise_object(obj, rev=rev)
            return {obj["id"]}
        if k == "update":
            o = self.objects.get(op["id"])
            if o is None or o["deleted"]:
                return set()                                 # erased meanwhile -> no-op
            for key, value in op["set"].items():
                set_key(o, key, value)
            o["rev"] = rev
            return {o["id"]}
        if k == "delete" or k == "clear":
            ids = op.get("ids") if k == "delete" else self.live_ids()
            changed = set()
            for oid in ids:
                o = self.objects.get(oid)
                if o is not None and not o["deleted"]:
                    o["deleted"] = True
                    o["rev"] = rev
                    changed.add(oid)
            return changed
        if k == "restore":
            changed = set()
            for oid in op["ids"]:
                o = self.objects.get(oid)
                if o is not None and o["deleted"]:
                    o["deleted"] = False
                    o["rev"] = rev
                    changed.add(oid)
            return changed
        if k == "replace":
            changed = set(self.objects)
            self.objects = {}
            for obj in op["objects"]:
                self.objects[obj["id"]] = normalise_object(obj, rev=rev)
            return changed | set(self.objects)
        if k == "batch":
            changed = set()
            for sub in op["ops"]:
                changed |= self._apply(sub, rev)
            return changed
        return set()

    # -- (de)serialisation -----------------------------------------------------
    def snapshot(self) -> dict:
        return {"seq": self.seq, "objects": copy.deepcopy(list(self.objects.values()))}

    @classmethod
    def from_snapshot(cls, snap: dict) -> "Board":
        b = cls()
        b.seq = int(snap.get("seq", 0))
        for o in snap.get("objects", []):
            b.objects[o["id"]] = normalise_object(o, rev=o.get("rev", 0))
        return b

    def copy(self) -> "Board":
        return Board.from_snapshot(self.snapshot())

    # -- consistency -----------------------------------------------------------
    def hash(self) -> str:
        """Order-independent content hash, identical on every converged replica."""
        if self._hash_cache and self._hash_cache[0] == self.seq:
            return self._hash_cache[1]
        h = hashlib.sha256()
        for oid in sorted(self.objects):
            o = self.objects[oid]
            canon = {k: o.get(k) for k in ("id", "type", "owner", "z", "style", "geom", "off", "deleted")}
            h.update(json.dumps(canon, sort_keys=True, separators=(",", ":")).encode())
            h.update(b"\n")
        digest = h.hexdigest()[:16]
        self._hash_cache = (self.seq, digest)
        return digest
