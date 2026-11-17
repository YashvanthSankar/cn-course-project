"""Multi-user undo semantics (see common/undo.py docstring)."""
from lanboard.common.model import Board
from lanboard.common.undo import UndoManager


def stroke(oid, owner):
    return {"k": "add", "obj": {"id": oid, "type": "stroke", "owner": owner, "z": 1,
                                "style": {"color": "#000"}, "geom": {"pts": [0, 0, 1, 1]}}}


class Sim:
    """A board shared by users; every op is 'sent to the server' immediately."""

    def __init__(self):
        self.board = Board()
        self.seq = 0

    def do(self, op):
        self.seq += 1
        self.board.apply(op, self.seq)
        return op


def test_undo_only_own_work():
    s, alice, bob = Sim(), UndoManager(), UndoManager()
    alice.record(s.do(stroke("a1", "alice")))
    bob.record(s.do(stroke("b1", "bob")))
    s.do(alice.undo(s.board))
    assert not s.board.is_live("a1") and s.board.is_live("b1")


def test_redo():
    s, alice = Sim(), UndoManager()
    alice.record(s.do(stroke("a1", "alice")))
    s.do(alice.undo(s.board))
    s.do(alice.redo(s.board))
    assert s.board.is_live("a1")
    s.do(alice.undo(s.board))
    assert not s.board.is_live("a1")


def test_undo_skips_object_erased_by_someone_else():
    s, alice = Sim(), UndoManager()
    alice.record(s.do(stroke("a1", "alice")))
    alice.record(s.do(stroke("a2", "alice")))
    s.do({"k": "delete", "ids": ["a2"]})
    op = alice.undo(s.board)
    assert op == {"k": "delete", "ids": ["a1"]}


def test_undo_update_leaves_others_newer_change():
    s, alice = Sim(), UndoManager()
    s.do(stroke("x", "bob"))
    alice.record(s.do({"k": "update", "id": "x", "set": {"style.color": "red", "off": [5, 0]},
                       "prev": {"style.color": "#000", "off": [0, 0]}}))
    s.do({"k": "update", "id": "x", "set": {"style.color": "blue"}, "prev": {"style.color": "red"}})
    op = alice.undo(s.board)
    assert op["set"] == {"off": [0, 0]}
    s.do(op)
    assert s.board.get("x")["style"]["color"] == "blue"


def test_undo_clear_by_me_restores_everything():
    s, alice = Sim(), UndoManager()
    s.do(stroke("b1", "bob"))
    s.do(stroke("b2", "bob"))
    clear = s.do(s.board.normalise({"k": "clear"}, "alice"))
    alice.record(clear)
    s.do(alice.undo(s.board))
    assert len(s.board) == 2


def test_new_action_clears_redo():
    s, alice = Sim(), UndoManager()
    alice.record(s.do(stroke("a1", "alice")))
    s.do(alice.undo(s.board))
    alice.record(s.do(stroke("a2", "alice")))
    assert alice.redo(s.board) is None


def test_batch_undo_is_one_step():
    s, alice = Sim(), UndoManager()
    s.do(stroke("x", "bob"))
    s.do(stroke("y", "bob"))
    alice.record(s.do({"k": "batch", "ops": [
        {"k": "update", "id": "x", "set": {"off": [9, 9]}, "prev": {"off": [0, 0]}},
        {"k": "update", "id": "y", "set": {"off": [9, 9]}, "prev": {"off": [0, 0]}},
    ]}))
    s.do(alice.undo(s.board))
    assert s.board.get("x")["off"] == [0, 0] and s.board.get("y")["off"] == [0, 0]


def test_nothing_to_undo():
    assert UndoManager().undo(Board()) is None
