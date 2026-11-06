"""Offscreen smoke tests for the client UI (main window and panels).

The network layer, controller and canvas are replaced by small fakes that expose
the same signals and methods as docs/CLIENT_API.md, so these tests check the UI
on its own and run without a server.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QObject, QSettings, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

from lanboard.common.messages import MsgType  # noqa: E402


# --------------------------------------------------------------------------- fakes
class FakeNet(QObject):
    state_changed = Signal(str)
    joined = Signal(dict)
    message = Signal(int, dict)
    error = Signal(str, str)
    password_required = Signal(str, bool)
    rtt_changed = Signal(float)
    frame_logged = Signal(dict)
    stats = Signal(dict)

    def __init__(self):
        super().__init__()
        self.batching_enabled = True
        self.cuts = 0
        self.chaos = SimpleNamespace(enabled=False, latency_ms=0, jitter_ms=0, drop_pct=0, dup_pct=0,
                                     cut_link=self.cut_link)
        self.user_id, self.username, self.room_code = "u1", "alice", "K7Q-2MX"
        self.resume_token, self.host, self.port = None, "127.0.0.1", 5050
        self.passwords: list[str] = []
        self.closed = False

    def connect_to(self, *a, **k): pass
    def provide_password(self, pw): self.passwords.append(pw)
    def send(self, t, payload): return True
    def is_joined(self): return True
    def close(self): self.closed = True
    def cut_link(self): self.cuts += 1


class FakeController(QObject):
    reset = Signal()
    objects_changed = Signal(object)
    participants_changed = Signal()
    chat_added = Signal(dict)
    chat_reset = Signal(list)
    room_changed = Signal(dict)
    me_changed = Signal(dict)
    cursor_moved = Signal(str, float, float)
    live_stroke = Signal(str, dict)
    live_stroke_end = Signal(str, str)
    laser = Signal(str, list)
    viewport = Signal(str, dict)
    signal_received = Signal(str, str)
    sync_status = Signal(dict)
    undo_state = Signal(bool, bool)
    history_ready = Signal(dict)
    saves_ready = Signal(list)
    blob_ready = Signal(str)
    blob_progress = Signal(str, int, int)
    notice = Signal(str, str)
    connection_state = Signal(str)

    def __init__(self, net):
        super().__init__()
        self.net = net
        self.me = {"user_id": "u1", "username": "alice", "color": "#e6194b", "role": "owner",
                   "online": True, "hand": False}
        self.room = {"code": "K7Q-2MX", "name": "CN Lab", "locked": False, "protected": False,
                     "editors_can_clear": False, "owner_id": "u1", "presenter": None}
        self.participants = {"u1": dict(self.me)}
        self.chat: list[dict] = []
        self.calls: list[tuple] = []

    def color_of(self, uid): return self.participants.get(uid, {}).get("color", "#888")
    def name_of(self, uid): return self.participants.get(uid, {}).get("username", uid)
    def can_edit(self): return self.me["role"] in ("owner", "editor")
    def is_owner(self): return self.me["role"] == "owner"
    def can_clear(self): return self.is_owner()

    def __getattr__(self, name):
        # every other controller method (undo, send_chat, submit, set_role...) just records its call
        if name.startswith("_"):
            raise AttributeError(name)

        def rec(*a, **k):
            self.calls.append((name, a, k))
        return rec


class FakeCanvas(QWidget):
    tool_changed = Signal(str)
    zoom_changed = Signal(float)
    hover_info = Signal(str)
    follow_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        if name.startswith("_") or name in ("calls",):
            raise AttributeError(name)

        def rec(*a):
            self.calls.append((name, a))
        return rec

    def last(self, name):
        for n, a in reversed(self.calls):
            if n == name:
                return a
        return None


class FakeDiscovery(QObject):
    servers_changed = Signal(list)

    def __init__(self):
        super().__init__()
        self.started = self.stopped = False
        self.probes = 0

    def start(self): self.started = True
    def stop(self): self.stopped = True
    def probe(self): self.probes += 1


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("LANBoardTests")
    app.setApplicationName("LANBoardTests")
    from lanboard.client.ui import theme
    theme.apply_theme(app, "light")
    return app


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def env(qapp, settings):
    from lanboard.client.ui.main_window import MainWindow
    net = FakeNet()
    ctl = FakeController(net)
    canvas = FakeCanvas()
    win = MainWindow(ctl, canvas=canvas, settings=settings)
    win.show()
    qapp.processEvents()
    yield SimpleNamespace(net=net, ctl=ctl, canvas=canvas, win=win, app=qapp)
    win._ending = True
    win.close()
    win.deleteLater()
    qapp.processEvents()


# --------------------------------------------------------------------------- tests
def test_main_window_builds(env):
    w = env.win
    assert w.windowTitle() == "LANBoard — CN Lab (K7Q-2MX)"
    assert w.tabs.count() == 4
    assert w.topbar.room_code.text() == "K7Q-2MX"
    assert w.act_lock.isEnabled() and w.act_settings.isEnabled()      # owner
    env.ctl.undo_state.emit(True, False)
    assert w.act_undo.isEnabled() and not w.act_redo.isEnabled()
    env.ctl.connection_state.emit("reconnecting")
    assert "reconnecting" in w.banner.text() and w.banner.isVisible()
    env.ctl.connection_state.emit("joined")
    assert not w.banner.isVisible()
    env.ctl.notice.emit("info", "Saved “demo”")
    assert any("demo" in t for t in w.toasts.texts())
    env.canvas.zoom_changed.emit(1.5)
    assert w.status_labels["zoom"].text() == "150%"
    env.canvas.hover_info.emit("stroke by bob")
    assert w.hover_label.text() == "stroke by bob"


def test_viewer_loses_drawing_tools(env):
    env.ctl.me["role"] = "viewer"
    env.ctl.me_changed.emit(env.ctl.me)
    assert not env.win.rail.tool_buttons["pen"].isEnabled()
    assert env.win.rail.tool_buttons["pan"].isEnabled()
    assert env.canvas.last("set_tool") == ("pan",)
    assert not env.win.act_lock.isEnabled()


def test_tool_buttons_call_canvas(env):
    rail, bar = env.win.rail, env.win.style_bar
    for name in ("rect", "eraser", "laser", "select"):
        rail.tool_buttons[name].click()
        assert env.canvas.last("set_tool") == (name,)
    env.canvas.tool_changed.emit("text")                 # canvas switched by itself
    assert rail.tool_buttons["text"].isChecked()
    bar.swatches[2].click()
    assert env.canvas.last("set_color") == (bar.swatches[2].hex,)
    bar.width_slider.setValue(12)
    assert env.canvas.last("set_width") == (12,)
    bar.fill_btn.click()
    assert env.canvas.last("set_fill") == (bar.swatches[2].hex,)
    bar.smart_btn.click()
    assert env.canvas.last("set_smart_ink") == (True,)


def test_style_bar_is_contextual(env):
    rail, bar = env.win.rail, env.win.style_bar
    rail.tool_buttons["rect"].click()
    env.canvas.tool_changed.emit("rect")
    assert bar.isVisible() and not bar.fill_btn.isHidden() and bar.smart_btn.isHidden()
    rail.tool_buttons["pen"].click()
    env.canvas.tool_changed.emit("pen")
    assert not bar.smart_btn.isHidden() and bar.fill_btn.isHidden()
    rail.tool_buttons["eraser"].click()
    env.canvas.tool_changed.emit("eraser")
    assert bar.color_group.isHidden() and not bar.width_group.isHidden()
    rail.tool_buttons["pan"].click()                       # nothing to style: the bar hides
    env.canvas.tool_changed.emit("pan")
    assert not bar.isVisible()
    # floating controls stay inside the stage
    stage = env.win.stage.rect()
    for w in (rail, env.win.zoom_controls):
        assert stage.contains(w.geometry().center())


def test_chat_message_appears(env):
    w = env.win
    w.tabs.setCurrentIndex(w.TAB_PEOPLE)                 # chat hidden -> unread badge
    ts = time.time()
    env.ctl.chat_added.emit({"id": "c1", "user_id": "u2", "username": "bob", "color": "#3cb44b",
                             "ts": ts, "text": "hello <b>world</b>", "kind": "user"})
    env.ctl.chat_added.emit({"id": "c1", "user_id": "u2", "username": "bob", "color": "#3cb44b",
                             "ts": ts, "text": "hello <b>world</b>", "kind": "user"})   # duplicate
    env.ctl.chat_added.emit({"id": "c2", "user_id": "", "username": "", "ts": ts,
                             "text": "carol joined", "kind": "system"})
    text = w.chat.plain_text()
    assert f"[{time.strftime('%H:%M', time.localtime(ts))}] bob: hello <b>world</b>" in text
    assert text.count("hello") == 1 and "carol joined" in text
    assert w.tabs.badge(w.TAB_CHAT) == 2
    w.tabs.setCurrentIndex(w.TAB_CHAT)
    env.app.processEvents()
    assert w.tabs.badge(w.TAB_CHAT) == 0
    w.chat.view.viewport().repaint()                      # bubbles paint without error
    w.chat.input.setText("  hi all ")
    w.chat.send()
    assert ("send_chat", ("hi all",), {}) in env.ctl.calls
    env.ctl.chat_reset.emit([{"id": "x", "username": "dan", "ts": ts, "text": "fresh", "kind": "user"}])
    assert "fresh" in w.chat.plain_text() and "carol" not in w.chat.plain_text()


def test_participants_render(env):
    ctl = env.ctl
    ctl.participants = {
        "u1": dict(ctl.me),
        "u2": {"user_id": "u2", "username": "bob", "color": "#3cb44b", "role": "editor", "online": True, "hand": True},
        "u3": {"user_id": "u3", "username": "carol", "color": "#4363d8", "role": "viewer", "online": False,
               "hand": False},
    }
    ctl.participants_changed.emit()
    rows = env.win.participants.row_data()
    assert [r["username"] for r in rows] == ["alice", "bob", "carol"]
    assert rows[0]["me"] and rows[1]["hand"] and not rows[2]["online"]
    assert env.win.tabs.badge(env.win.TAB_PEOPLE) == 1
    assert [p["username"] for p in env.win.topbar.avatars.people] == ["alice", "bob"]   # online only
    env.win.participants.follow("u2")
    assert env.canvas.last("set_follow") == ("u2",)
    env.win.participants.set_spotlight("u3")
    assert env.canvas.last("set_spotlight") == ("u3",)
    env.win.participants.list.viewport().repaint()        # delegate paints without error


def test_sync_badge_changes(env):
    badge = env.win.topbar.badge
    env.ctl.sync_status.emit({"state": "synced", "seq": 42, "hash": "abcd1234ef", "pending": 0})
    assert badge.text() == "In sync · seq 42" and badge.property("state") == "synced"
    assert badge._pulse.state() != badge._pulse.State.Running          # calm when in sync
    env.ctl.sync_status.emit({"state": "pending", "seq": 42, "pending": 3})
    assert badge.text() == "Syncing · 3 pending"
    assert badge._pulse.state() == badge._pulse.State.Running          # pulses while not in sync
    env.ctl.sync_status.emit({"state": "mismatch", "seq": 43, "pending": 0})
    assert badge.text() == "Mismatch · healing" and badge.property("state") == "mismatch"
    env.ctl.sync_status.emit({"state": "offline", "seq": 43, "pending": 2})
    assert badge.text().startswith("Offline · edits queued")
    assert env.win.status_labels["pending"].text() == "2 pending"


def test_inspector_ring_buffer_is_fast(qapp):
    from lanboard.client.ui.inspector import InspectorPanel
    net = FakeNet()
    panel = InspectorPanel(net)
    panel.hide_switch.setChecked(False)
    t0 = time.perf_counter()
    for i in range(5000):
        t = MsgType.OP if i % 3 else MsgType.CURSOR
        net.frame_logged.emit({"t": 1000.0 + i / 100, "dir": "in" if i % 2 else "out", "type": int(t),
                               "name": t.name, "size": 40 + i % 50, "payload": {"seq": i},
                               "chaos": "dup" if i % 97 == 0 else None})
        if i % 700 == 0:
            panel.flush()                                  # timer-driven batches
    panel.flush()
    elapsed = time.perf_counter() - t0
    assert panel.model.rowCount() == 2000
    assert panel.model.frame(1999)["payload"]["seq"] == 4999
    assert panel.model.frame(0)["payload"]["seq"] == 3000
    assert elapsed < 2.0, elapsed
    # filters
    panel.hide_switch.setChecked(True)                     # hides CURSOR frames
    assert all(panel.model.frame(r)["type"] == int(MsgType.OP) for r in range(panel.model.rowCount()))
    panel.type_combo.setCurrentIndex(panel.type_combo.findData(int(MsgType.CURSOR)))
    assert panel.model.rowCount() == 0
    panel.type_combo.setCurrentIndex(0)
    panel.table.selectRow(0)
    assert '"seq"' in panel.detail.toPlainText()
    panel.pause_btn.setChecked(True)
    net.frame_logged.emit({"t": 1, "dir": "in", "type": 0x21, "name": "OP", "size": 1, "payload": {}, "chaos": None})
    assert not panel.pending
    panel.clear()
    assert panel.model.rowCount() == 0


def test_chaos_sliders_set_attributes(qapp):
    from lanboard.client.ui.chaos_panel import ChaosPanel
    net = FakeNet()
    panel = ChaosPanel(net)
    panel.enable.setChecked(True)
    assert net.chaos.enabled is True
    for attr, value in (("latency_ms", 250), ("jitter_ms", 80), ("drop_pct", 10), ("dup_pct", 5)):
        panel.sliders[attr].setValue(value)
        assert getattr(net.chaos, attr) == value
    assert panel.value_labels["latency_ms"].text() == "250 ms"
    panel.cut_btn.click()
    assert net.cuts == 1
    panel.reset()
    assert net.chaos.latency_ms == 0 and net.chaos.drop_pct == 0


def test_hud_and_batching_toggle(qapp):
    from lanboard.client.ui.hud import HudPanel
    net = FakeNet()
    ctl = FakeController(net)
    hud = HudPanel(net, ctl)
    net.stats.emit({"msgs_in": 30, "msgs_out": 12, "bytes_in": 5000, "bytes_out": 900, "rtt_ms": 3.4,
                    "rtt_hist": [2.0, 3.0, 3.4], "total_in": 100000, "total_out": 20000})
    ctl.sync_status.emit({"state": "pending", "pending": 4, "seq": 1})
    assert hud.metric_labels["rtt"].text() == "3.4 ms"
    assert hud.metric_labels["pending"].text() == "4"
    hud.batch_switch.setChecked(False)
    assert net.batching_enabled is False
    hud.resize(360, 400)
    hud.show()
    hud.repaint()


def _history_payload(n_ops: int = 450) -> dict:
    def stroke(i):
        return {"id": f"u1-{i}", "type": "stroke", "owner": "u1", "z": i,
                "style": {"color": "#000", "width": 2}, "geom": {"pts": [i, i, i + 1, i + 1]}}
    ops = []
    for i in range(1, n_ops + 1):
        if i == 300:
            op = {"k": "delete", "ids": ["u1-5", "u1-6"]}
        else:
            op = {"k": "add", "obj": stroke(i)}
        ops.append({"seq": 100 + i, "user_id": "u1", "op_id": f"o{i}", "ts": 1_700_000_000 + i, "op": op})
    return {"base_seq": 100, "base": {"objects": [stroke(0)]}, "ops": ops}


def test_history_builds_preview_boards(env, monkeypatch):
    from lanboard.client.ui.history import HistoryTimeline
    from lanboard.common.model import Board
    payload = _history_payload()
    tl = HistoryTimeline(payload)
    assert len(tl) == 450 and tl.seq_at(0) == 100 and tl.seq_at(450) == 550
    assert len(tl.board_at(0)) == 1
    end = tl.board_at(450)
    assert len(end) == 1 + 449 - 2 and end.seq == 550
    assert {200, 400} <= set(tl.checkpoints)
    # random access (backwards, across checkpoints) matches a straight replay
    ref = Board.from_snapshot({"seq": 100, "objects": payload["base"]["objects"]})
    for e in payload["ops"][:310]:
        ref.apply(e["op"], e["seq"])
    assert tl.board_at(310).hash() == ref.hash()
    assert tl.board_at(17).seq == 117

    # the panel drives the canvas preview
    panel = env.win.history
    panel.request()
    assert ("request_history", (), {}) in env.ctl.calls
    env.ctl.history_ready.emit(payload)
    assert panel.slider.maximum() == 450
    panel.slider.setValue(120)
    board = env.canvas.last("show_preview_board")[0]
    assert isinstance(board, Board) and board.seq == 220 and len(board) == 121
    assert env.win.preview_banner.isVisible()
    panel.speed_seg.set_index(2)
    panel.play()
    panel._tick()
    assert panel.slider.value() == 136
    panel.stop()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    panel.restore_here()
    submits = [c for c in env.ctl.calls if c[0] == "submit"]
    assert submits and submits[-1][1][0]["k"] == "replace" and submits[-1][2] == {"undoable": False}
    assert len(submits[-1][1][0]["objects"]) == 137
    assert env.canvas.last("show_preview_board") == (None,)
    assert not env.win.preview_banner.isVisible()


def test_connect_dialog_builds_and_validates(qapp, settings):
    from lanboard.client.ui.connect_dialog import ROOM_CODE_RE, ConnectDialog, generate_room_code
    disc = FakeDiscovery()
    dlg = ConnectDialog(discovery=disc, settings=settings)
    assert disc.started
    disc.servers_changed.emit([{"server_id": "s1", "name": "Lab PC", "host": "192.168.1.20", "port": 5055,
                                "proto": 1, "last_seen": 0,
                                "rooms": [{"code": "ABC-123", "name": "Physics", "users": 3,
                                           "protected": True, "locked": False}]}])
    top = dlg.tree.topLevelItem(0)
    assert top.text(0) == "Lab PC" and top.childCount() == 1
    dlg.tree.viewport().repaint()                         # server cards paint without error
    dlg._on_tree_click(top.child(0))
    assert dlg.host.text() == "192.168.1.20" and dlg.port.value() == 5055 and dlg.room.text() == "ABC-123"

    dlg.username.setText("")
    assert "name" in dlg.validate()
    assert dlg.username.property("invalid") is True
    dlg.username.setText("alice")
    dlg.room.setText("bad code!")
    assert "Room codes" in dlg.validate()
    dlg.room.setText("ABC-123")
    assert dlg.validate() is None
    p = dlg.collect()
    assert (p.host, p.port, p.username, p.room, p.create) == ("192.168.1.20", 5055, "alice", "ABC-123", False)

    dlg.mode.set_index(1)                                # host mode
    assert dlg.ok_btn.text() == "Create room" and not dlg.gen_btn.isHidden()
    dlg.room.clear()
    dlg.gen_btn.click()
    assert ROOM_CODE_RE.match(dlg.room.text()) and len(dlg.room.text()) == 7
    dlg.start_server.setChecked(True)
    assert dlg.collect().host == "127.0.0.1" and not dlg.host.isEnabled()
    assert dlg.collect().create and dlg.collect().start_server
    assert generate_room_code()[3] == "-"
    dlg.start_server.setChecked(False)
    dlg._on_ok()                                          # valid, no server -> accepted
    assert dlg.params() is not None and dlg.params().create
    assert settings.value("connect/username") == "alice"
    dlg.deleteLater()

    # the next dialog remembers the server/room as a "recent" chip
    dlg2 = ConnectDialog(discovery=FakeDiscovery(), settings=settings)
    assert dlg2.recents()[0]["room"] == dlg.params().room
    rec = dlg2.recents()[0]
    dlg2.room.clear()
    dlg2.recent_chips[0].click()
    assert dlg2.room.text() == rec["room"] and dlg2.mode.index() == 0
    assert dlg2.username.completer() is not None
    dlg2.hero.repaint()                                   # animated brand panel paints
    dlg2.hero._tick()
    dlg2.close()
    assert not dlg2.hero._timer.isActive()                # animation stops when hidden
    dlg2.deleteLater()


def test_theme_icons_and_dark_mode(qapp):
    from lanboard.client.ui import theme
    for name in theme.icon_names():
        assert not theme.icon(name, "#333333", 24).isNull()
    theme.apply_theme(qapp, "dark")
    assert theme.manager().dark and theme.DARK["bg"] in qapp.styleSheet()
    theme.apply_theme(qapp, "light")
    assert not theme.manager().dark and theme.LIGHT["bg"] in qapp.styleSheet()
    assert theme.load_fonts()["ui"] and theme.UI_FAMILY == "Inter"     # bundled fonts load


def test_window_theme_switch_both_ways(env):
    from lanboard.client.ui import theme
    env.win.toggle_theme()
    assert theme.manager().dark and env.canvas.last("set_board_theme") == (True,)
    env.win.grab()                                          # every widget paints in dark
    env.win.tabs.setCurrentIndex(env.win.TAB_NET)
    env.win.grab()
    env.win.toggle_theme()
    assert not theme.manager().dark and env.canvas.last("set_board_theme") == (False,)
    assert env.win.settings.value("ui/theme") == "light"


def test_toasts_stack_top_centre_and_group(env):
    w = env.win
    w.toasts.show("one", "info")
    w.toasts.show("two", "success")
    w.toasts.show("two", "success")                         # duplicate is ignored
    assert w.toasts.texts() == ["two", "one"]               # newest first
    w.toasts.show_grouped("joined", "bob", "joined", window_ms=10)
    w.toasts.show_grouped("joined", "carol", "joined", window_ms=10)
    w.toasts.show_grouped("joined", "dan", "joined", window_ms=10)
    for _ in range(200):                                    # burst window closes after 10 ms
        if "bob, carol and 1 other joined" in w.toasts.texts():
            break
        QTest.qWait(10)
    assert "bob, carol and 1 other joined" in w.toasts.texts()
    toast = w.toasts._toasts[0]
    QTest.qWait(300)                                        # glide animation done
    centre = toast.geometry().center().x()
    assert abs(centre - w.stage.width() // 2) <= 2 and toast.y() < 40


def test_join_leave_and_role_toasts(env):
    ctl, w = env.ctl, env.win
    env.ctl.connection_state.emit("joined")
    ctl.participants_changed.emit()                         # initial roster: no toast
    ctl.participants["u2"] = {"user_id": "u2", "username": "bob", "color": "#3cb44b", "role": "editor",
                              "online": True, "hand": False}
    ctl.participants_changed.emit()
    QTest.qWait(1000)
    assert any("bob joined" in t for t in w.toasts.texts())
    del ctl.participants["u2"]
    ctl.participants_changed.emit()
    QTest.qWait(1000)
    assert any("bob left" in t for t in w.toasts.texts())
    ctl.me["role"] = "viewer"
    ctl.me_changed.emit(ctl.me)
    assert any("viewer" in t for t in w.toasts.texts())


def test_side_panel_collapses_and_restores(env):
    w = env.win
    assert w.side.isVisible() and w.side_panel_open()
    w.toggle_side_panel()
    QTest.qWait(400)
    assert not w.side.isVisible() and w.settings.value("ui/side_open") == "false"
    env.ctl.chat_added.emit({"id": "z1", "user_id": "u2", "username": "bob", "ts": time.time(), "text": "hi",
                             "kind": "user"})
    assert w.chat.unread == 1                               # hidden panel counts unread
    w.focus_chat()                                          # Ctrl+Enter re-opens it on Chat
    QTest.qWait(400)
    assert w.side.isVisible() and w.tabs.currentIndex() == w.TAB_CHAT and w.side.width() >= 300


def test_invite_popover_and_shortcuts(env):
    w = env.win
    w.lan_addresses = ["192.168.1.20"]
    w.open_invite()
    assert w.invite.isVisible()
    assert w.invite.addr.text == "192.168.1.20 : 5050" and w.invite.code.text == "K7Q-2MX"
    w.invite.copy()
    assert QApplication.clipboard().text() == "LANBoard room K7Q-2MX at 192.168.1.20:5050"
    assert not w.invite.isVisible() and any("copied" in t for t in w.toasts.texts())
    w.show_shortcuts()
    assert w.shortcuts.isVisible()
    w.shortcuts.repaint()
    QTest.keyClick(w.shortcuts, "a")
    assert not w.shortcuts.isVisible()


def test_follow_changed_updates_people(env):
    env.win.participants.follow("u2")
    assert env.win.participants.following == "u2"
    env.canvas.follow_changed.emit(None)                    # user panned away
    assert env.win.participants.following is None
    assert any("Stopped following" in t for t in env.win.toasts.texts())


def test_grid_and_minimap_settings(env):
    env.win.set_grid("lines")
    assert env.canvas.last("set_grid") == ("lines",) and env.win.settings.value("ui/grid") == "lines"
    env.win.set_minimap_visible(False)
    assert env.win.settings.value("ui/minimap") == "false" and not env.win.act_minimap.isChecked()


def test_password_prompt_and_fatal_error(env, monkeypatch):
    from lanboard.client.ui import main_window
    monkeypatch.setattr(main_window, "ask_password", lambda *a: "s3cret")
    env.net.password_required.emit("K7Q-2MX", False)
    assert env.net.passwords == ["s3cret"]
    ended = []
    env.win.session_ended.connect(lambda r, m: ended.append((r, m)))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self.buttons()[0])
    env.win._handle_error("KICKED", "kicked by owner")
    assert ended and ended[0][0] in ("fatal", "quit") and env.net.closed


def test_deleted_window_ignores_theme_switch(qapp, settings, monkeypatch):
    """After Leave the window is deleted; later theme toggles must not reach it."""
    import sys
    from PySide6.QtCore import QCoreApplication, QEvent
    from lanboard.client.ui import theme
    from lanboard.client.ui.main_window import MainWindow
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a))
    net = FakeNet()
    win = MainWindow(FakeController(net), canvas=FakeCanvas(), settings=settings)
    win._ending = True
    win.close()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    theme.apply_theme(qapp, "dark")
    theme.apply_theme(qapp, "light")
    assert not errors
