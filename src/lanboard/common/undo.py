"""Per-user undo/redo for a multi-user board.

Semantics (documented in docs/ARCHITECTURE.md section 5):

* Each user's stack holds only **their own** actions. Undo never touches
  anybody else's work.
* Undo is not "rewind". It emits a *new* op (the inverse) that goes through the
  server like any other op and gets its own seq, so every replica converges.
* Before an inverse is emitted it is **filtered against the current board**:
    - undo "add X"        -> delete X       (skipped if X was already erased by someone)
    - undo "delete X"     -> restore X      (skipped if X is already live again)
    - undo "update X.k"   -> set k back     (only for keys whose value is still *ours*:
                                             if another user changed k since, we leave it)
    - undo "batch"        -> inverse of each part, in reverse order, filtered
  An entry whose filtered inverse is empty is skipped and the next one is tried,
  so pressing undo always does something visible if anything is undoable.
* A new user action clears the redo stack (standard branching).
* ``clear`` is undoable: it is recorded in its normalised form
  ``delete{ids, clear:true}``, so its inverse restores exactly those ids.
* A ``replace`` (load/restore version) is not undoable; entries referencing
  objects that no longer exist simply filter out to nothing.
"""

from __future__ import annotations

import copy
from typing import Any

from .model import Board, get_key

MAX_UNDO = 500


def inverse(op: dict) -> dict | None:
    """Unfiltered inverse of an op, or None if the op kind is not undoable."""
    k = op.get("k")
    if k == "add":
        return {"k": "delete", "ids": [op["obj"]["id"]]}
    if k == "delete":
        return {"k": "restore", "ids": list(op["ids"])}
    if k == "restore":
        return {"k": "delete", "ids": list(op["ids"])}
    if k == "update":
        return {"k": "update", "id": op["id"], "set": copy.deepcopy(op.get("prev", {})),
                "prev": copy.deepcopy(op["set"])}
    if k == "batch":
        parts = [inverse(s) for s in reversed(op["ops"])]
        if any(p is None for p in parts):
            return None
        return {"k": "batch", "ops": parts}
    return None


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) < 1e-9
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return all(_same(x, y) for x, y in zip(a, b))
    return a == b


def applicable(op: dict | None, board: Board) -> dict | None:
    """Filter ``op`` so it only contains parts that still make sense on ``board``."""
    if op is None:
        return None
    k = op["k"]
    if k == "delete":
        ids = [i for i in op["ids"] if board.is_live(i)]
        return {"k": "delete", "ids": ids} if ids else None
    if k == "restore":
        ids = [i for i in op["ids"] if (o := board.get(i)) is not None and o["deleted"]]
        return {"k": "restore", "ids": ids} if ids else None
    if k == "update":
        o = board.get(op["id"])
        if o is None or o["deleted"]:
            return None
        keep = [key for key in op["set"] if _same(get_key(o, key), op.get("prev", {}).get(key))]
        if not keep:
            return None
        return {"k": "update", "id": op["id"],
                "set": {key: copy.deepcopy(op["set"][key]) for key in keep},
                "prev": {key: copy.deepcopy(op["prev"][key]) for key in keep}}
    if k == "batch":
        scratch = board.copy()
        parts = []
        for sub in op["ops"]:
            f = applicable(sub, scratch)
            if f is not None:
                parts.append(f)
                scratch.apply(f)
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else {"k": "batch", "ops": parts}
    return None


class UndoManager:
    """Undo/redo stacks for one local user.

    Usage by the client controller::

        um.record(op)                 # after the user performs an action
        inv = um.undo(view_board)     # -> op to submit, or None
        fwd = um.redo(view_board)     # -> op to submit, or None
    """

    def __init__(self, limit: int = MAX_UNDO) -> None:
        self.limit = limit
        self._undo: list[dict] = []
        self._redo: list[dict] = []

    def record(self, op: dict) -> None:
        """Remember a fresh user action (clears redo)."""
        if inverse(op) is None:
            return
        self._undo.append(copy.deepcopy(op))
        if len(self._undo) > self.limit:
            del self._undo[0]
        self._redo.clear()

    def undo(self, board: Board) -> dict | None:
        while self._undo:
            done = self._undo.pop()
            inv = applicable(inverse(done), board)
            if inv is not None:
                self._redo.append(inv)
                return inv
        return None

    def redo(self, board: Board) -> dict | None:
        while self._redo:
            undone = self._redo.pop()
            fwd = applicable(inverse(undone), board)
            if fwd is not None:
                self._undo.append(fwd)
                return fwd
        return None

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)
