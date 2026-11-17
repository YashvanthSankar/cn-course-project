"""Real-app end-to-end tests: the real server + two (or three) complete client windows.

Unlike test_ui_smoke.py (fakes) and test_e2e_client.py (controller only), every
client here is the full stack a user runs: ``NetClient`` -> ``SessionController``
-> ``MainWindow`` with the real ``CanvasView``, side panels and overlays. The
tests drive the UI the way a user does (mouse drags on the canvas, keyboard
shortcuts, toolbar actions, panel buttons) and assert that the *other* window
sees the effect, through the server.

Modal dialogs (QMessageBox / QFileDialog / QInputDialog) are monkeypatched to
answer immediately; everything else is real.
"""

from __future__ import annotations

import gc
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPointF, QSettings, Qt          # noqa: E402
from PySide6.QtGui import QImage, QMouseEvent                     # noqa: E402
from PySide6.QtTest import QTest                                  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox           # noqa: E402

from lanboard.client.controller import SessionController          # noqa: E402
from lanboard.client.net import NetClient                         # noqa: E402

from test_e2e_client import ServerThread, wait_until              # noqa: E402

YES = QMessageBox.StandardButton.Yes


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    from lanboard.client.ui import theme
    theme.apply_theme(app, "light")
    return app


@pytest.fixture
def srv(tmp_path):
    s = ServerThread(tmp_path / "data")
    yield s
    s.stop()


@pytest.fixture
def apps(qapp, srv, tmp_path):
    """Factory: ``apps("alice", create=True)`` -> a joined, visible client window."""
    from lanboard.client.ui.main_window import MainWindow
    live: list[SimpleNamespace] = []

    def make(name, room="E2E", create=False, password=None, wait=True):
        net = NetClient()
        ctl = SessionController(net)
        settings = QSettings(str(tmp_path / f"{name}.ini"), QSettings.Format.IniFormat)
        win = MainWindow(ctl, settings=settings)
        ended: list[tuple[str, str]] = []
        win.session_ended.connect(lambda r, m: ended.append((r, m)))
        win.resize(1280, 800)
        win.show()
        QTest.qWaitForWindowExposed(win)
        net.connect_to("127.0.0.1", srv.port, name, room, create=create, password=password, room_name="Demo")
        a = SimpleNamespace(net=net, ctl=ctl, win=win, canvas=win.canvas, scene=win.canvas.canvas,
                            ended=ended, name=name)
        live.append(a)
        if wait:
            assert wait_until(net.is_joined), f"{name} failed to join"
            assert wait_until(lambda: ctl.me is not None)
        return a

    yield make
    for a in live:
        a.win._ending = True
        a.net.close()
        a.win.close()
    QTest.qWait(50)
    for a in live:
        a.win.deleteLater()
        a.ctl.deleteLater()
        a.net.deleteLater()
    QTest.qWait(50)
    gc.collect()


@pytest.fixture
def pair(apps):
    a = apps("alice", create=True)
    b = apps("bob")
    assert wait_until(lambda: b.ctl.user_id in a.ctl.participants and a.ctl.user_id in b.ctl.participants)
    activate(a)
    return a, b


def vp(app, x, y) -> QPointF:
    return QPointF(app.canvas.mapFromScene(QPointF(x, y)))


def mouse(app, kind, pos: QPointF, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton,
          mods=Qt.KeyboardModifier.NoModifier):
    v = app.canvas.viewport()
    QApplication.sendEvent(v, QMouseEvent(kind, pos, v.mapToGlobal(pos), button, buttons, mods))


def drag(app, pts, mods=Qt.KeyboardModifier.NoModifier, release=True):
    mouse(app, QEvent.Type.MouseButtonPress, vp(app, *pts[0]), mods=mods)
    for x, y in pts[1:]:
        mouse(app, QEvent.Type.MouseMove, vp(app, x, y), button=Qt.MouseButton.NoButton, mods=mods)
        QTest.qWait(5)
    if release:
        mouse(app, QEvent.Type.MouseButtonRelease, vp(app, *pts[-1]), buttons=Qt.MouseButton.NoButton, mods=mods)


def click(app, x, y, mods=Qt.KeyboardModifier.NoModifier):
    drag(app, [(x, y)], mods=mods)


def hover(app, x, y):
    mouse(app, QEvent.Type.MouseMove, vp(app, x, y), button=Qt.MouseButton.NoButton,
          buttons=Qt.MouseButton.NoButton)


def activate(app):
    """Make ``app`` the active window (window-wide shortcuts only fire in the active window)."""
    app.win.raise_()
    app.win.activateWindow()
    QTest.qWaitForWindowActive(app.win)
    app.canvas.setFocus()
    QApplication.processEvents()


def tool(app, name):
    """Pick a tool the way a user does: click its palette button."""
    btn = app.win.rail.tool_buttons[name]
    assert btn.isEnabled(), f"{name} is disabled"
    QTest.mouseClick(btn, Qt.MouseButton.LeftButton)
    assert app.canvas.tool.name == name


def live_of(app, typ=None):
    return [o for o in app.ctl.view.live() if typ is None or o["type"] == typ]


def settled(*apps_):
    return all(not a.ctl.pending for a in apps_) and len({a.ctl.board.hash() for a in apps_}) == 1 \
        and len({a.ctl.board.seq for a in apps_}) == 1


def converge(*apps_, timeout=5.0):
    assert wait_until(lambda: settled(*apps_), timeout), "clients did not converge"


def png(tmp_path, name="pic.png", w=300, h=200, color=Qt.GlobalColor.blue) -> str:
    p = str(tmp_path / name)
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(color)
    assert img.save(p)
    return p


def test_pen_stroke_live_preview_then_commit_reaches_other_window(pair):
    a, b = pair
    tool(a, "pen")
    pts = [(10 + i * 8, 20 + (i % 3) * 6) for i in range(25)]
    drag(a, pts, release=False)
    assert wait_until(lambda: any(k[0] == a.ctl.user_id for k in b.scene.live))
    mouse(a, QEvent.Type.MouseButtonRelease, vp(a, *pts[-1]), buttons=Qt.MouseButton.NoButton)
    assert wait_until(lambda: len(live_of(b, "stroke")) == 1)
    oid = live_of(b, "stroke")[0]["id"]
    assert oid in b.scene.items_by_id
    assert live_of(b)[0]["owner"] == a.ctl.user_id
    converge(a, b)


def test_shapes_text_and_sticky_via_tools_and_keyboard(pair):
    a, b = pair
    tool(a, "rect")
    drag(a, [(0, 0), (60, 40), (120, 80)])
    tool(a, "ellipse")
    drag(a, [(200, 0), (260, 60)])
    tool(a, "arrow")
    drag(a, [(0, 200), (150, 260)])
    tool(a, "text")
    click(a, 300, 300)
    assert a.canvas.editor_open()
    QTest.keyClicks(a.canvas.viewport(), "PATTERX")
    assert a.canvas.tool.name == "text", "typing in the text editor switched tools"
    QTest.keyClick(a.canvas.viewport(), Qt.Key.Key_Backspace)
    QTest.keyClick(a.canvas.viewport(), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert not a.canvas.editor_open()
    assert a.win.tabs.currentIndex() != a.win.TAB_CHAT or not a.win.chat.input.hasFocus()
    tool(a, "sticky")
    click(a, 500, 100)
    assert wait_until(a.canvas.editor_open)
    QTest.keyClicks(a.canvas.viewport(), "Vote me")
    QTest.keyClick(a.canvas.viewport(), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert wait_until(lambda: {o["type"] for o in live_of(b)} == {"rect", "ellipse", "arrow", "text", "sticky"})
    assert live_of(b, "text")[0]["geom"]["text"] == "PATTER"
    assert wait_until(lambda: live_of(b, "sticky")[0]["geom"]["text"] == "Vote me")
    converge(a, b)


def test_tool_shortcut_keys_switch_tools(pair):
    a, _b = pair
    a.canvas.setFocus()
    for key, name in ((Qt.Key.Key_R, "rect"), (Qt.Key.Key_E, "eraser"), (Qt.Key.Key_V, "select"),
                      (Qt.Key.Key_P, "pen"), (Qt.Key.Key_K, "laser"), (Qt.Key.Key_H, "pan")):
        QTest.keyClick(a.canvas.viewport(), key)
        assert a.canvas.tool.name == name
        assert a.win.rail.current_tool() == name


def test_eraser_removes_for_everyone(pair):
    a, b = pair
    tool(a, "pen")
    drag(a, [(0, 0), (50, 0), (100, 0)])
    drag(a, [(0, 100), (50, 100), (100, 100)])
    assert wait_until(lambda: len(live_of(b)) == 2)
    tool(a, "eraser")
    drag(a, [(50, -20), (50, 0), (50, 20)])
    assert wait_until(lambda: len(live_of(b)) == 1)
    assert live_of(b)[0]["geom"]["pts"][1] == pytest.approx(100, abs=1)
    converge(a, b)


def test_select_move_recolour_and_delete(pair):
    a, b = pair
    tool(a, "rect")
    drag(a, [(0, 0), (100, 80)])
    assert wait_until(lambda: len(live_of(b)) == 1)
    tool(a, "select")
    click(a, 50, 0)
    assert a.scene.selected_ids
    assert a.win.style_bar.isVisible()
    sw = next(s for s in a.win.style_bar.swatches if s.hex.lower() != live_of(a)[0]["style"]["color"].lower())
    QTest.mouseClick(sw, Qt.MouseButton.LeftButton)
    assert wait_until(lambda: live_of(b)[0]["style"]["color"].lower() == sw.hex.lower())
    drag(a, [(50, 0), (100, 0), (150, 0), (250, 0)])
    assert wait_until(lambda: live_of(b)[0].get("off", [0, 0])[0] == pytest.approx(200, abs=2))
    QTest.keyClick(a.canvas.viewport(), Qt.Key.Key_Delete)
    assert wait_until(lambda: len(live_of(b)) == 0)
    converge(a, b)


def test_undo_redo_toolbar_and_shortcuts(pair):
    a, b = pair
    tool(a, "pen")
    drag(a, [(0, 0), (40, 40), (80, 0)])
    assert wait_until(lambda: len(live_of(b)) == 1)
    assert wait_until(a.win.act_undo.isEnabled)
    a.win.act_undo.trigger()
    assert wait_until(lambda: len(live_of(b)) == 0)
    assert wait_until(a.win.act_redo.isEnabled)
    QTest.keyClick(a.win, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert wait_until(lambda: len(live_of(b)) == 1)
    QTest.keyClick(a.win, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert wait_until(lambda: len(live_of(b)) == 0)
    assert not b.win.act_undo.isEnabled()
    converge(a, b)


def test_live_cursor_laser_and_ping_reach_other_window(pair):
    a, b = pair
    tool(a, "pen")
    for i in range(5):
        hover(a, 100 + i * 10, 100)
        QTest.qWait(30)
    assert wait_until(lambda: a.ctl.user_id in b.scene.cursors)
    cur = b.scene.cursors[a.ctl.user_id]
    assert wait_until(lambda: abs(cur.pos().x() - 140) < 15)
    tool(a, "laser")
    drag(a, [(0, 0), (30, 30), (60, 0), (90, 30)])
    assert wait_until(lambda: a.ctl.user_id in b.scene.lasers)
    assert len(live_of(b)) == 0
    click(a, 200, 200, mods=Qt.KeyboardModifier.AltModifier)
    assert wait_until(lambda: len(b.scene.pings) >= 1)


def test_insert_image_reaches_other_window_with_pixels(pair, tmp_path, monkeypatch):
    from lanboard.client.ui import main_window as mw
    a, b = pair
    path = png(tmp_path)
    monkeypatch.setattr(mw.QFileDialog, "getOpenFileName", staticmethod(lambda *a_, **k: (path, "")))
    a.win.act_image.trigger()
    assert wait_until(lambda: len(live_of(b, "image")) == 1)
    h = live_of(b, "image")[0]["geom"]["blob"]
    assert wait_until(lambda: b.scene.pixmap_for(h) is not None, timeout=8)
    assert b.scene.pixmap_for(h).width() == 300
    assert not any("could not" in t.lower() for t in a.win.toasts.texts())


def test_image_inserted_offline_reaches_other_window_after_reconnect(pair, tmp_path):
    a, b = pair
    a.net.cut_link()
    assert wait_until(lambda: not a.net.is_joined(), 2)
    assert a.canvas.insert_image_file(png(tmp_path, color=Qt.GlobalColor.red))
    assert wait_until(a.net.is_joined, 10)
    assert wait_until(lambda: len(live_of(b, "image")) == 1, 10)
    h = live_of(b, "image")[0]["geom"]["blob"]
    assert wait_until(lambda: b.scene.pixmap_for(h) is not None, timeout=8), \
        "image inserted while offline never reached the other client"
    assert not any("unknown blob" in t for t in b.win.toasts.texts())


def test_import_pdf_reaches_other_window(pair, tmp_path, monkeypatch):
    pytest.importorskip("pypdfium2")
    from PySide6.QtGui import QPageSize, QPainter, QPdfWriter
    from lanboard.client.ui import main_window as mw
    a, b = pair
    path = str(tmp_path / "deck.pdf")
    w = QPdfWriter(path)
    w.setPageSize(QPageSize(QPageSize.PageSizeId.A6))
    p = QPainter(w)
    p.drawText(100, 100, "page 1")
    w.newPage()
    p.drawText(100, 100, "page 2")
    p.end()
    monkeypatch.setattr(mw.QFileDialog, "getOpenFileName", staticmethod(lambda *a_, **k: (path, "")))
    a.win.act_pdf.trigger()
    assert wait_until(lambda: len(live_of(b, "image")) == 2, 8)
    for o in live_of(b, "image"):
        assert wait_until(lambda o=o: b.scene.pixmap_for(o["geom"]["blob"]) is not None, 8)
    a.win.act_undo.trigger()
    assert wait_until(lambda: len(live_of(b, "image")) == 0)


@pytest.mark.parametrize("ext", [".png", ".svg", ".pdf"])
def test_export_from_window(pair, tmp_path, monkeypatch, ext):
    from lanboard.client.ui import main_window as mw
    a, b = pair
    tool(a, "rect")
    drag(a, [(0, 0), (100, 80)])
    assert wait_until(lambda: len(live_of(b)) == 1)
    out = str(tmp_path / f"board{ext}")
    monkeypatch.setattr(mw, "export_dialog", lambda *a_, **k: out)
    b.win.act_export.trigger()
    assert os.path.getsize(out) > 0
    texts = b.win.toasts.texts()
    assert sum("xport" in t for t in texts) == 1, f"expected one export toast, got {texts}"


def test_export_empty_board_says_why_once(pair, tmp_path, monkeypatch):
    from lanboard.client.ui import main_window as mw
    a, _b = pair
    monkeypatch.setattr(mw, "export_dialog", lambda *a_, **k: str(tmp_path / "x.png"))
    a.win.act_export.trigger()
    texts = a.win.toasts.texts()
    assert any("empty" in t for t in texts)
    assert len(texts) == 1, texts


def test_chat_typed_in_panel_reaches_other_window_with_unread_badge(pair):
    a, b = pair
    b.win.show_tab(b.win.TAB_PEOPLE)
    QTest.qWait(100)
    unread0 = b.win.chat.unread
    a.win.focus_chat()
    QTest.keyClicks(a.win.chat.input, "hello bob")
    QTest.keyClick(a.win.chat.input, Qt.Key.Key_Return)
    assert a.win.chat.input.text() == ""
    assert wait_until(lambda: "alice: hello bob" in b.win.chat.plain_text())
    assert wait_until(lambda: b.win.chat.unread == unread0 + 1)
    b.win.show_tab(b.win.TAB_CHAT)
    assert wait_until(lambda: b.win.chat.unread == 0)


def test_participants_panel_shows_everyone_and_hand(pair):
    a, b = pair
    assert wait_until(lambda: {d["username"] for d in a.win.participants.row_data()} == {"alice", "bob"})
    rows = {d["username"]: d for d in a.win.participants.row_data()}
    assert rows["alice"]["role"] == "owner" and rows["bob"]["role"] == "editor"
    b.win.act_hand.trigger()
    assert wait_until(lambda: any(d.get("hand") for d in a.win.participants.row_data()))
    assert wait_until(lambda: "✋ bob raised a hand" in a.win.toasts.texts())
    assert a.win.tabs.badge(a.win.TAB_PEOPLE) == 1
    b.win.act_hand.trigger()
    assert wait_until(lambda: not any(d.get("hand") for d in a.win.participants.row_data()))


def test_viewer_role_disables_tools_and_blocks_edits(pair):
    a, b = pair
    a.ctl.set_role(b.ctl.user_id, "viewer")
    assert wait_until(lambda: not b.ctl.can_edit())
    assert not b.win.rail.tool_buttons["pen"].isEnabled()
    assert b.canvas.tool.name in ("pan", "select", "laser")
    assert not b.win.act_image.isEnabled()
    assert not b.win.act_save.isEnabled()
    b.canvas.set_tool("pen")
    drag(b, [(0, 0), (50, 50)])
    QTest.qWait(200)
    assert len(live_of(a)) == 0 and len(live_of(b)) == 0
    assert any("viewer" in t for t in b.win.toasts.texts())
    a.ctl.set_role(b.ctl.user_id, "editor")
    assert wait_until(lambda: b.ctl.can_edit() and b.win.rail.tool_buttons["pen"].isEnabled())


def test_lock_board_from_toolbar(pair):
    a, b = pair
    a.win.act_lock.trigger()
    assert wait_until(lambda: bool((b.ctl.room or {}).get("locked")))
    assert not b.win.act_lock.isEnabled()
    assert not b.win.rail.tool_buttons["pen"].isEnabled()
    assert any("locked" in t for t in b.win.toasts.texts())
    b.canvas.set_tool("pen")
    drag(b, [(0, 0), (50, 50)])
    tool(a, "pen")
    drag(a, [(0, 100), (50, 150)])
    assert wait_until(lambda: len(live_of(b)) == 1)
    a.win.act_lock.trigger()
    assert wait_until(lambda: not (b.ctl.room or {}).get("locked"))
    assert wait_until(lambda: b.win.rail.tool_buttons["pen"].isEnabled())
    converge(a, b)
    assert len(live_of(a)) == 1


def test_kick_from_people_panel_ends_their_session(pair, monkeypatch):
    a, b = pair
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a_, **k: YES))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    a.win.participants._kick({"user_id": b.ctl.user_id, "username": "bob"})
    assert wait_until(lambda: bool(b.ended))
    assert "removed" in b.ended[0][1].lower()
    assert wait_until(lambda: b.ctl.user_id not in a.ctl.participants)


def test_clear_board_confirm_then_undo(pair, monkeypatch):
    a, b = pair
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a_, **k: YES))
    tool(a, "pen")
    drag(a, [(0, 0), (50, 50)])
    tool(b, "pen")
    drag(b, [(0, 100), (50, 150)])
    assert wait_until(lambda: len(live_of(a)) == 2 and len(live_of(b)) == 2)
    assert not b.win.act_clear.isEnabled()
    a.win.act_clear.trigger()
    assert wait_until(lambda: len(live_of(b)) == 0)
    a.win.act_undo.trigger()
    assert wait_until(lambda: len(live_of(b)) == 2)
    converge(a, b)


def test_room_settings_rename_and_password_then_join(apps, monkeypatch):
    from lanboard.client.ui import main_window as mw
    a = apps("alice", create=True)
    real = mw.RoomSettingsDialog

    class Scripted(real):
        def exec(self):
            self.name.setText("Networks Lab")
            self.password.setText("s3cret")
            self.editors_clear.setChecked(True)
            return 1

    monkeypatch.setattr(mw, "RoomSettingsDialog", Scripted)
    a.win.act_settings.trigger()
    assert wait_until(lambda: (a.ctl.room or {}).get("protected"))
    assert a.ctl.room["name"] == "Networks Lab" and a.ctl.room["editors_can_clear"]
    assert "Networks Lab" in a.win.windowTitle()
    answers = iter(["nope", "s3cret"])
    asked: list[bool] = []

    def fake_ask(_parent, _code, wrong):
        asked.append(wrong)
        return next(answers)

    monkeypatch.setattr(mw, "ask_password", fake_ask)
    b = apps("bob", wait=False)
    assert wait_until(b.net.is_joined, 10)
    assert asked == [False, True]
    assert b.win.act_clear.isEnabled()


def test_saves_dialog_save_list_and_load(pair, monkeypatch):
    from lanboard.client.ui.dialogs import SavesDialog
    a, b = pair
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a_, **k: YES))
    tool(a, "rect")
    drag(a, [(0, 0), (100, 80)])
    assert wait_until(lambda: len(live_of(b)) == 1)
    dlg = SavesDialog(a.ctl, a.win)
    dlg.show()
    dlg.name.setText("v1")
    QTest.mouseClick(dlg.save_btn, Qt.MouseButton.LeftButton)
    assert wait_until(lambda: dlg.list.count() == 1)
    assert "v1" in dlg.list.item(0).text() and "1 objects" in dlg.list.item(0).text()
    dlg.close()
    tool(a, "pen")
    drag(a, [(0, 200), (50, 250)])
    drag(a, [(0, 300), (50, 350)])
    assert wait_until(lambda: len(live_of(b)) == 3)
    bdlg = SavesDialog(b.ctl, b.win)
    bdlg.show()
    assert wait_until(lambda: bdlg.list.count() == 1)
    bdlg.list.setCurrentRow(0)
    assert not bdlg.load_btn.isEnabled()
    bdlg.close()
    dlg = SavesDialog(a.ctl, a.win)
    dlg.show()
    assert wait_until(lambda: dlg.list.count() == 1)
    dlg.list.setCurrentRow(0)
    assert dlg.load_btn.isEnabled()
    QTest.mouseClick(dlg.load_btn, Qt.MouseButton.LeftButton)
    assert wait_until(lambda: [o["type"] for o in live_of(b)] == ["rect"])
    converge(a, b)


def test_quick_save_shortcut(pair, monkeypatch):
    from lanboard.client.ui import main_window as mw
    a, b = pair
    monkeypatch.setattr(mw.QInputDialog, "getText", staticmethod(lambda *a_, **k: ("quick", True)))
    a.win.act_save.trigger()
    assert wait_until(lambda: "saved the board as 'quick'" in b.win.chat.plain_text())
    assert wait_until(lambda: "Saved “quick” on the server" in a.win.toasts.texts())


def test_history_scrub_preview_and_restore(pair, monkeypatch):
    a, b = pair
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a_, **k: YES))
    tool(a, "pen")
    for i in range(3):
        drag(a, [(0, i * 100), (50, i * 100 + 50)])
    assert wait_until(lambda: len(live_of(b)) == 3)
    converge(a, b)
    h = a.win.history
    a.win.show_tab(a.win.TAB_HISTORY)
    QTest.mouseClick(h.load_btn, Qt.MouseButton.LeftButton)
    assert wait_until(lambda: h.timeline is not None and h.timeline.end == 3)
    h.slider.setValue(1)
    assert h.previewing and a.win.preview_banner.isVisible()
    assert len(a.scene.items_by_id) == 1
    assert not a.canvas.can_edit()
    assert len(b.scene.items_by_id) == 3
    QTest.mouseClick(h.restore_btn, Qt.MouseButton.LeftButton)
    assert wait_until(lambda: len(live_of(b)) == 1)
    assert not h.previewing and not a.win.preview_banner.isVisible()
    converge(a, b)
    bh = b.win.history
    bh.request()
    assert wait_until(lambda: bh.timeline is not None)
    bh.slider.setValue(0)
    assert not bh.restore_btn.isEnabled()
    bh.exit_preview()


def test_present_and_follow(pair):
    a, b = pair
    a.win.act_present.trigger()
    assert a.win.act_present.isChecked()
    b.win.participants.follow(a.ctl.user_id)
    assert b.canvas.following == a.ctl.user_id
    a.canvas.centerOn(3000, 2000)
    target = QPointF(3000, 2000)

    def near():
        c = b.canvas.visible_rect().center()
        return abs(c.x() - target.x()) < 80 and abs(c.y() - target.y()) < 80
    assert wait_until(near, 5), "the follower's view did not follow the presenter"
    b.canvas.zoom_in()
    assert b.canvas.following is None
    assert wait_until(lambda: b.win.participants.following is None)


def test_only_owner_or_chosen_presenter_can_present(pair):
    a, b = pair
    assert a.win.act_present.isEnabled()
    assert not b.win.act_present.isEnabled()
    a.ctl.set_room(presenter=b.ctl.user_id)
    assert wait_until(b.win.act_present.isEnabled)
    assert any("made you the presenter" in t for t in b.win.toasts.texts())
    assert wait_until(lambda: {d["username"]: d for d in a.win.participants.row_data()}["bob"]["presenter"])
    activate(b)
    b.win.act_present.trigger()
    a.win.participants.follow(b.ctl.user_id)
    b.canvas.centerOn(-2500, 1500)

    def near():
        c = a.canvas.visible_rect().center()
        return abs(c.x() + 2500) < 80 and abs(c.y() - 1500) < 80
    assert wait_until(near, 5), "following the chosen presenter did not move the view"
    a.ctl.set_room(presenter=None)
    assert wait_until(lambda: not b.win.act_present.isEnabled())
    assert not b.win.act_present.isChecked() and not b.canvas._presenting


def test_viewer_keeps_laser_pointer(pair):
    a, b = pair
    a.ctl.set_role(b.ctl.user_id, "viewer")
    assert wait_until(lambda: not b.ctl.can_edit())
    assert b.win.rail.tool_buttons["laser"].isEnabled()
    tool(b, "laser")
    drag(b, [(0, 0), (30, 30), (60, 0), (90, 30)])
    assert wait_until(lambda: b.ctl.user_id in a.scene.lasers), "a viewer's laser never reached the owner"


def test_reconnect_banner_and_offline_edits(pair):
    a, b = pair
    tool(a, "pen")
    a.net.cut_link()
    assert wait_until(lambda: a.win.banner.isVisible() and "reconnecting" in a.win.banner.text().lower(), 3)
    drag(a, [(0, 0), (50, 50)])
    assert len(live_of(a)) == 1 and a.ctl.pending
    assert wait_until(a.net.is_joined, 10)
    assert wait_until(lambda: not a.win.banner.isVisible())
    assert wait_until(lambda: any("Reconnected" in t for t in a.win.toasts.texts()))
    assert wait_until(lambda: len(live_of(b)) == 1)
    converge(a, b)


def test_leave_ends_session_and_others_see_it(pair):
    a, b = pair
    b.win.act_leave.trigger()
    assert b.ended and b.ended[0][0] == "leave"
    assert wait_until(lambda: b.ctl.user_id not in a.ctl.participants)
    assert wait_until(lambda: {d["username"] for d in a.win.participants.row_data()} == {"alice"})


def test_network_tab_counts_real_traffic(pair):
    a, b = pair
    a.win.show_tab(a.win.TAB_NET)
    tool(a, "pen")
    for i in range(3):
        drag(a, [(0, i * 30), (60, i * 30 + 20)])
    assert wait_until(lambda: len(live_of(b)) == 3)
    insp = a.win.network.inspector
    names = lambda: {insp.model.frame(r).get("name") for r in range(insp.model.rowCount())}  # noqa: E731
    assert wait_until(lambda: "OP_SUBMIT" in names() and "OP" in names(), 5)
    hud = a.win.network.hud
    assert wait_until(lambda: hud.last_stats.get("total_bytes_out", 0) > 0, 5)
    assert hud.metric_labels["total"].text() not in ("", "0 B / 0 B")
    assert hud.metric_labels["pending"].text() == "0"


def people_menu(app, username, monkeypatch) -> dict:
    """Open the People right-click menu on ``username``; returns {text: QAction} (menu not shown)."""
    from lanboard.client.ui import participants as pmod
    captured: dict = {}

    class NoShowMenu(pmod.QMenu):
        def exec(self, *_a, **_k):
            captured.update({a.text(): a for a in self.actions() if a.text()})
            captured["_menu"] = self

    monkeypatch.setattr(pmod, "QMenu", NoShowMenu)
    lst = app.win.participants.list
    row = next(i for i in range(lst.count()) if lst.item(i).text() == username)
    app.win.participants._menu(lst.visualItemRect(lst.item(row)).center())
    return captured


def test_people_menu_actions_drive_the_server(pair, monkeypatch):
    a, b = pair
    menu = people_menu(a, "bob", monkeypatch)
    assert {"Follow bob's view", "Spotlight their work", "Make editor", "Make viewer", "Make presenter",
            "Kick from room…"} <= set(menu)
    assert not menu["Make editor"].isEnabled()
    menu["Make viewer"].trigger()
    assert wait_until(lambda: b.ctl.me["role"] == "viewer")
    assert any("viewer" in t for t in b.win.toasts.texts())
    assert wait_until(lambda: "alice made bob a viewer" in a.win.chat.plain_text())
    people_menu(a, "bob", monkeypatch)["Make editor"].trigger()
    assert wait_until(lambda: b.ctl.me["role"] == "editor")
    people_menu(a, "bob", monkeypatch)["Spotlight their work"].trigger()
    assert a.scene.spotlight == b.ctl.user_id
    people_menu(a, "bob", monkeypatch)["Clear spotlight"].trigger()
    assert a.scene.spotlight is None
    bmenu = people_menu(b, "alice", monkeypatch)
    assert "Kick from room…" not in bmenu and "Make viewer" not in bmenu
    assert "Follow alice's view" in bmenu


def test_sticky_vote_reaches_other_window(pair):
    a, b = pair
    tool(a, "sticky")
    click(a, 0, 0)
    assert wait_until(a.canvas.editor_open)
    QTest.keyClick(a.canvas.viewport(), Qt.Key.Key_Escape)
    assert wait_until(lambda: len(live_of(b, "sticky")) == 1)
    oid = live_of(b, "sticky")[0]["id"]
    b.canvas.toggle_vote(oid)
    assert wait_until(lambda: live_of(a, "sticky")[0].get("votes") == [b.ctl.user_id])
    a.canvas.toggle_vote(oid)
    assert wait_until(lambda: sorted(live_of(b, "sticky")[0].get("votes")) == sorted([a.ctl.user_id,
                                                                                    b.ctl.user_id]))
    converge(a, b)


def test_window_shortcuts(pair):
    a, b = pair
    w = a.win
    assert w.side.isVisible()
    QTest.keyClick(w, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
    assert wait_until(lambda: not w.side.isVisible())
    QTest.keyClick(w, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
    assert wait_until(w.side.isVisible)
    QTest.keyClick(w, Qt.Key.Key_F1)
    assert wait_until(w.shortcuts.isVisible)
    w.shortcuts.close()
    mini = w.minimap is not None and w.minimap.isVisible()
    QTest.keyClick(w, Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert w.act_minimap.isChecked() != mini
    tool(a, "rect")
    drag(a, [(0, 0), (100, 100)])
    drag(a, [(50, 50), (150, 150)])
    assert wait_until(lambda: len(live_of(b)) == 2)
    first = min(live_of(a), key=lambda o: o["z"])["id"]
    tool(a, "select")
    a.scene.set_selection([first])
    QTest.keyClick(a.canvas.viewport(), Qt.Key.Key_BracketRight, Qt.KeyboardModifier.ControlModifier)
    assert wait_until(lambda: max(live_of(b), key=lambda o: o["z"])["id"] == first)
    QTest.keyClick(a.canvas.viewport(), Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert len(a.scene.selected_ids) == 2


def test_dark_mode_toggle_switches_board_and_back(pair):
    from lanboard.client.ui import theme
    a, _b = pair
    try:
        a.win.act_theme.trigger()
        assert theme.manager().dark and a.canvas.board_dark
        assert a.win.act_theme.text() == "Light mode"
        a.win.act_theme.trigger()
        assert not theme.manager().dark and not a.canvas.board_dark
    finally:
        theme.apply_theme(None, "light")


def test_late_joiner_sees_board_images_and_chat(apps, tmp_path):
    a = apps("alice", create=True)
    activate(a)
    tool(a, "pen")
    drag(a, [(0, 0), (40, 40)])
    assert a.canvas.insert_image_file(png(tmp_path))
    a.ctl.send_chat("before you came")
    assert wait_until(lambda: not a.ctl.pending)
    c = apps("carol")
    assert wait_until(lambda: len(live_of(c)) == 2)
    h = live_of(c, "image")[0]["geom"]["blob"]
    assert wait_until(lambda: c.scene.pixmap_for(h) is not None, 8)
    assert "alice: before you came" in c.win.chat.plain_text()
    converge(a, c)


def test_discovery_browser_finds_real_server(qapp, tmp_path):
    import asyncio
    import threading
    from lanboard.client.discovery import DiscoveryBrowser
    from lanboard.server import Server

    loop = asyncio.new_event_loop()
    server = Server("127.0.0.1", 0, str(tmp_path / "d"), "Lab server", discovery=True, discovery_port=0)
    ready = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start())
        ready.set()
        loop.run_forever()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(5)
    browser = DiscoveryBrowser(port=server.discovery_port, interval_ms=200)
    seen: list = []
    browser.servers_changed.connect(lambda lst: seen.append(lst))
    try:
        browser.start()
        assert wait_until(lambda: any(s["name"] == "Lab server" for s in browser.servers()), 5)
        s = next(s for s in browser.servers() if s["name"] == "Lab server")
        assert s["port"] == server.port and s["server_id"] == server.server_id
    finally:
        browser.stop()
        asyncio.run_coroutine_threadsafe(server.stop(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        t.join(5)


def test_kicked_user_cannot_rejoin_from_the_same_app(apps, qapp, srv, tmp_path, monkeypatch):
    """Kick = ban of the app's client id; "Back to start" + Join in the same app must stay banned."""
    from lanboard.client.__main__ import ClientShell
    from lanboard.client.ui.connect_dialog import ConnectParams
    a = apps("alice", create=True)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a_, **k: YES))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    shell = ClientShell(qapp, QSettings(str(tmp_path / "shell.ini"), QSettings.Format.IniFormat))
    back: list = []
    monkeypatch.setattr(shell, "show_connect", lambda err="", prefill=None: back.append(err))
    monkeypatch.setattr(qapp, "quit", lambda: back.append("quit"))
    params = ConnectParams(host="127.0.0.1", port=srv.port, username="bob", room="E2E")
    try:
        shell.start_session(params)
        first_id = shell.net.client_id
        assert wait_until(lambda: shell.net is not None and shell.net.is_joined())
        bob_id = shell.controller.user_id
        assert wait_until(lambda: bob_id in a.ctl.participants)
        a.win.participants._kick({"user_id": bob_id, "username": "bob"})
        assert wait_until(lambda: shell.window is None)
        QTest.qWait(50)
        shell.start_session(params)
        assert shell.net.client_id == first_id
        assert wait_until(lambda: shell.window is None, 5), "a kicked user rejoined the room"
        assert not any(p.get("username") == "bob" for p in a.ctl.participants.values())
    finally:
        if shell.net is not None:
            shell.window._ending = True
            shell.net.close()
            shell.window.close()
        QTest.qWait(50)


def test_history_playback_and_live_edits_during_preview(pair):
    a, b = pair
    tool(a, "pen")
    for i in range(4):
        drag(a, [(0, i * 60), (50, i * 60 + 40)])
    assert wait_until(lambda: len(live_of(b)) == 4)
    converge(a, b)
    h = a.win.history
    h.request()
    assert wait_until(lambda: h.timeline is not None and h.timeline.end == 4)
    h.slider.setValue(0)
    assert len(a.scene.items_by_id) == 0
    QTest.mouseClick(h.play_btn, Qt.MouseButton.LeftButton)
    assert h.timer.isActive()
    assert wait_until(lambda: h.slider.value() == 4, 5)
    assert not h.timer.isActive()
    assert len(a.scene.items_by_id) == 4
    h.slider.setValue(2)
    tool(b, "rect")
    drag(b, [(300, 300), (400, 400)])
    assert wait_until(lambda: len(live_of(a)) == 5)
    assert len(a.scene.items_by_id) == 2
    a.win.preview_banner.action.emit()
    assert not h.previewing
    assert wait_until(lambda: len(a.scene.items_by_id) == 5)
    assert a.canvas.can_edit()


def test_owner_leaving_hands_ownership_to_an_editor(pair):
    a, b = pair
    assert not b.win.act_lock.isEnabled() and not b.win.act_settings.isEnabled()
    a.win.act_leave.trigger()
    assert wait_until(b.ctl.is_owner, 5)
    assert wait_until(lambda: b.win.act_lock.isEnabled() and b.win.act_settings.isEnabled()
                      and b.win.act_clear.isEnabled() and b.win.act_present.isEnabled())
    assert any("owner" in t for t in b.win.toasts.texts())
