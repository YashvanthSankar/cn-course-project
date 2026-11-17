"""Board model: determinism, no-op semantics, validation, hashing."""
import random

import pytest

from lanboard.common.model import Board, InvalidOp, validate_op


def stroke(oid, owner="u1", z=1):
    return {"id": oid, "type": "stroke", "owner": owner, "z": z,
            "style": {"color": "#000", "width": 3}, "geom": {"pts": [0, 0, 10, 10]}}


def add(oid, **kw):
    return {"k": "add", "obj": stroke(oid, **kw)}


def test_add_update_delete_restore():
    b = Board()
    assert b.apply(add("a"), 1) == {"a"}
    assert b.apply({"k": "update", "id": "a", "set": {"style.color": "#f00"}, "prev": {}}, 2) == {"a"}
    assert b.get("a")["style"]["color"] == "#f00"
    assert b.apply({"k": "delete", "ids": ["a"]}, 3) == {"a"}
    assert not b.is_live("a")
    assert b.apply({"k": "restore", "ids": ["a"]}, 4) == {"a"}
    assert b.is_live("a") and b.seq == 4


def test_noops():
    b = Board()
    b.apply(add("a"), 1)
    assert b.apply(add("a"), 2) == set()
    b.apply({"k": "delete", "ids": ["a"]}, 3)
    assert b.apply({"k": "update", "id": "a", "set": {"z": 5}, "prev": {}}, 4) == set()
    assert b.apply({"k": "delete", "ids": ["a", "ghost"]}, 5) == set()


def test_clear_normalised_and_undoable():
    b = Board()
    b.apply(add("a"), 1)
    b.apply(add("b"), 2)
    op = b.normalise({"k": "clear"}, "u1")
    assert op["k"] == "delete" and sorted(op["ids"]) == ["a", "b"]
    b.apply(op, 3)
    assert len(b) == 0
    b.apply({"k": "restore", "ids": op["ids"]}, 4)
    assert len(b) == 2


def test_normalise_forces_owner():
    op = Board().normalise(add("a", owner="mallory"), "u7")
    assert op["obj"]["owner"] == "u7"


def test_replace():
    b = Board()
    b.apply(add("a"), 1)
    b.apply({"k": "replace", "objects": [stroke("x")]}, 2)
    assert list(b.objects) == ["x"]


def test_paint_order():
    b = Board()
    b.apply(add("b", z=1), 1)
    b.apply(add("a", z=1), 2)
    b.apply(add("c", z=0), 3)
    assert [o["id"] for o in b.live()] == ["c", "a", "b"]


def test_snapshot_roundtrip_and_hash():
    b = Board()
    b.apply(add("a"), 1)
    b.apply({"k": "update", "id": "a", "set": {"off": [5, 5]}, "prev": {}}, 2)
    c = Board.from_snapshot(b.snapshot())
    assert c.hash() == b.hash() and c.seq == 2


def random_ops(rng, n):
    ids, ops = [], []
    for i in range(n):
        r = rng.random()
        if r < 0.4 or not ids:
            oid = f"u{rng.randint(1, 3)}-{i}"
            ids.append(oid)
            ops.append(add(oid))
        elif r < 0.6:
            ops.append({"k": "update", "id": rng.choice(ids), "set": {"style.color": rng.choice("rgb")}, "prev": {}})
        elif r < 0.75:
            ops.append({"k": "delete", "ids": [rng.choice(ids)]})
        elif r < 0.9:
            ops.append({"k": "restore", "ids": [rng.choice(ids)]})
        else:
            ops.append({"k": "batch", "ops": [{"k": "update", "id": rng.choice(ids), "set": {"z": rng.randint(0, 9)}, "prev": {}}]})
    return ops


@pytest.mark.parametrize("seed", range(20))
def test_determinism(seed):
    ops = random_ops(random.Random(seed), 300)
    a, b = Board(), Board()
    for seq, op in enumerate(ops, 1):
        a.apply(op, seq)
    mid = 150
    for seq, op in enumerate(ops[:mid], 1):
        b.apply(op, seq)
    b = Board.from_snapshot(b.snapshot())
    for seq, op in enumerate(ops[mid:], mid + 1):
        b.apply(op, seq)
    assert a.hash() == b.hash()


@pytest.mark.parametrize("bad", [
    None,
    {"k": "nope"},
    {"k": "add", "obj": {"id": "a", "type": "blob", "geom": {}}},
    {"k": "add", "obj": {"id": "a", "type": "stroke", "geom": {"pts": [1, 2, 3]}}},
    {"k": "add", "obj": {"id": "a", "type": "stroke", "geom": {"pts": [1, 2, "x", 4]}}},
    {"k": "add", "obj": {"id": "a b", "type": "rect", "geom": {"x": 0, "y": 0, "w": 1, "h": 1}}},
    {"k": "add", "obj": {"id": "a", "type": "rect", "geom": {"x": 1e12, "y": 0, "w": 1, "h": 1}}},
    {"k": "update", "id": "a", "set": {"owner": "me"}},
    {"k": "update", "id": "a", "set": {}},
    {"k": "delete", "ids": []},
    {"k": "batch", "ops": [{"k": "clear"}]},
    {"k": "batch", "ops": [{"k": "batch", "ops": [{"k": "delete", "ids": ["a"]}]}]},
])
def test_validation_rejects(bad):
    with pytest.raises(InvalidOp):
        validate_op(bad)


def test_validation_accepts():
    validate_op(add("a"))
    validate_op({"k": "update", "id": "a", "set": {"geom.text": "hi", "off": [1, 2]}, "prev": {}})
    validate_op({"k": "clear"})
    validate_op({"k": "batch", "ops": [add("a"), {"k": "delete", "ids": ["b"]}]})
