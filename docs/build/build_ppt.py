"""Build docs/LANBoard_Presentation.pptx (16:9) with python-pptx.

Diagrams are native, editable PowerPoint shapes and connectors. Screenshots come
from docs/assets/screens (regenerate them with docs/build/screenshots.py).

    python docs/build/build_ppt.py [OUT.pptx]

Needs python-pptx (install it into a separate venv, not the project's .venv).
"""

from __future__ import annotations

import os
import sys

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOTS = os.path.join(ROOT, "docs", "assets", "screens")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "LANBoard_Presentation.pptx")

FONT, MONO = "Inter", "JetBrains Mono"
INDIGO, VIOLET = "4F46E5", "7C3AED"
INK, TEXT2, MUTED = "111827", "374151", "6B7280"
BG_DARK, BG_DARK2 = "0F1220", "1C2033"
CARD, LINE = "F4F5FB", "D9DCEA"
GREEN, AMBER, RED, CYAN = "16A34A", "D97706", "DC2626", "0891B2"

TEAM = {
    "Y": ("Yashvanth S", "CS24I1029", "Server core & state", "4F46E5"),
    "J": ("Jeevanandham T", "CS24I1035", "Protocol & networking", "0891B2"),
    "S": ("Akula Sai Teja", "CS24I1054", "Canvas & drawing tools", "DB2777"),
    "N": ("Nithillakrishi P Y", "CS24I1039", "UI & collaboration", "059669"),
}

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
BLANK = prs.slide_layouts[6]
SW, SH = 13.333, 7.5


def rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h)


# ----------------------------------------------------------------------------- primitives
def text(slide, x, y, w, h, runs, size=16, color=INK, bold=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, font=FONT, spacing=None, name=None):
    """runs: str, or list of paragraphs; a paragraph is str or list of (text, opts) runs."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    paras = runs if isinstance(runs, list) else [runs]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if spacing:
            p.space_after = Pt(spacing)
        items = para if isinstance(para, list) else [(para, {})]
        for t, o in items:
            r = p.add_run()
            r.text = t
            f = r.font
            f.name = o.get("font", font)
            f.size = Pt(o.get("size", size))
            f.bold = o.get("bold", bold)
            f.italic = o.get("italic", False)
            f.color.rgb = rgb(o.get("color", color))
    return tb


def box(slide, x, y, w, h, fill=CARD, line=None, radius=0.08, shape=MSO_SHAPE.ROUNDED_RECTANGLE,
        label=None, size=14, color=INK, bold=False, font=FONT, align=PP_ALIGN.CENTER, shadow=False,
        lw=1.25, anchor=MSO_ANCHOR.MIDDLE, name=None):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        s.name = name
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = min(0.5, radius / max(min(w, h), 0.01))
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = rgb(fill)
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = rgb(line)
        s.line.width = Pt(lw)
    if not shadow:
        sp = s._element.spPr
        sp.append(etree.SubElement(sp, qn("a:effectLst")))
    tf = s.text_frame
    tf.margin_left = tf.margin_right = Inches(0.08)
    tf.margin_top = tf.margin_bottom = Inches(0.04)
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    if label is not None:
        paras = label if isinstance(label, list) else [label]
        for i, para in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            items = para if isinstance(para, list) else [(para, {})]
            for t, o in items:
                r = p.add_run()
                r.text = t
                r.font.name = o.get("font", font)
                r.font.size = Pt(o.get("size", size))
                r.font.bold = o.get("bold", bold)
                r.font.color.rgb = rgb(o.get("color", color))
    return s


def gradient(shape, a=INDIGO, b=VIOLET, angle=0):
    shape.fill.gradient()
    shape.fill.gradient_angle = angle
    st = shape.fill.gradient_stops
    st[0].color.rgb, st[0].position = rgb(a), 0
    st[1].color.rgb, st[1].position = rgb(b), 1.0


def arrow(slide, x1, y1, x2, y2, color=INDIGO, width=2.0, head=True, tail=False, dash=False, name=None):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    if name:
        c.name = name
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    if dash:
        d = etree.SubElement(ln, qn("a:prstDash"))
        d.set("val", "dash")
    if tail:
        e = etree.SubElement(ln, qn("a:headEnd"))
        e.set("type", "triangle"); e.set("w", "med"); e.set("len", "med")
    if head:
        e = etree.SubElement(ln, qn("a:tailEnd"))
        e.set("type", "triangle"); e.set("w", "med"); e.set("len", "med")
    return c


def picture(slide, name, x, y, w=None, h=None, crop=None, border=True, shadow=True):
    """crop = (left, top, right, bottom) fractions. Size: give w or h; the other keeps aspect."""
    path = os.path.join(SHOTS, name)
    from PIL import Image
    iw, ih = Image.open(path).size
    l, t, r, b = crop or (0, 0, 0, 0)
    aspect = (iw * (1 - l - r)) / (ih * (1 - t - b))
    if w is not None and h is None:
        h = w / aspect
    elif h is not None and w is None:
        w = h * aspect
    elif w is not None and h is not None:           # fit inside w x h, centred
        if w / h > aspect:
            nw = h * aspect; x += (w - nw) / 2; w = nw
        else:
            nh = w / aspect; y += (h - nh) / 2; h = nh
    pic = slide.shapes.add_picture(path, Inches(x), Inches(y), Inches(w), Inches(h))
    pic.name = f"Screenshot {name}"
    pic.crop_left, pic.crop_top, pic.crop_right, pic.crop_bottom = l, t, r, b
    if border:
        pic.line.color.rgb = rgb(LINE)
        pic.line.width = Pt(0.75)
    if shadow:
        sp = pic._element.spPr
        eff = etree.SubElement(sp, qn("a:effectLst"))
        sh = etree.SubElement(eff, qn("a:outerShdw"))
        sh.set("blurRad", "190500"); sh.set("dist", "38100"); sh.set("dir", "5400000"); sh.set("algn", "t")
        clr = etree.SubElement(sh, qn("a:srgbClr")); clr.set("val", "1E1B4B")
        a = etree.SubElement(clr, qn("a:alpha")); a.set("val", "22000")
    return pic, (x, y, w, h)


def caption(slide, x, y, w, t, color=MUTED, size=11, align=PP_ALIGN.LEFT):
    return text(slide, x, y, w, 0.3, t, size=size, color=color, align=align)


def notes(slide, t: str):
    slide.notes_slide.notes_text_frame.text = t.strip()


SLIDE_NO = [0]


def content_slide(title, who=None, subtitle=None, dark=False):
    s = prs.slides.add_slide(BLANK)
    SLIDE_NO[0] += 1
    bg = s.background.fill
    bg.solid()
    bg.fore_color.rgb = rgb(BG_DARK if dark else "FFFFFF")
    tc = "FFFFFF" if dark else INK
    text(s, 0.6, 0.42, 9.6, 0.7, title, size=28, bold=True, color=tc, name="Title")
    if subtitle:
        text(s, 0.6, 1.08, 10.5, 0.4, subtitle, size=15, color="A5B4FC" if dark else MUTED, name="Subtitle")
    if who:
        name, _roll, area, col = TEAM[who]
        chip = box(s, 10.35, 0.48, 2.4, 0.44, fill=col, radius=0.22,
                   label=[[(name, {"bold": True, "size": 12, "color": "FFFFFF"})]], name="Presenter chip")
        chip.text_frame.margin_left = Inches(0.1)
    text(s, 0.6, 7.05, 6, 0.25, "LANBoard · CN Lab Project 2026", size=10,
         color="7A8299" if dark else "9CA3AF")
    text(s, 11.9, 7.05, 0.85, 0.25, str(SLIDE_NO[0]), size=10, color="7A8299" if dark else "9CA3AF",
         align=PP_ALIGN.RIGHT)
    return s


def bullets(slide, x, y, w, items, size=16, color=TEXT2, gap=8, dot=INDIGO, h=None):
    """Bullet list drawn as dot + text rows (keeps alignment exact)."""
    paras = []
    for it in items:
        if isinstance(it, tuple):
            head, rest = it
            paras.append([("●  ", {"color": dot, "size": size - 4}), (head, {"bold": True, "color": INK}),
                          (rest, {})])
        else:
            paras.append([("●  ", {"color": dot, "size": size - 4}), (it, {})])
    return text(slide, x, y, w, h or 0.4 * len(items), paras, size=size, color=color, spacing=gap)


def stat(slide, x, y, w, big, small, color=INDIGO, big_size=34, fill=CARD):
    box(slide, x, y, w, 1.35, fill=fill, radius=0.14)
    text(slide, x + 0.2, y + 0.14, w - 0.4, 0.62, big, size=big_size, bold=True, color=color)
    text(slide, x + 0.2, y + 0.8, w - 0.4, 0.5, small, size=12, color=TEXT2)


def mono_box(slide, x, y, w, h, lines, size=12, fill="0F1220", color="E6E9F2"):
    b = box(slide, x, y, w, h, fill=fill, radius=0.1, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
            label=[[(ln, {"font": MONO, "size": size, "color": color})] for ln in lines])
    b.text_frame.margin_left = b.text_frame.margin_right = Inches(0.2)
    b.text_frame.margin_top = Inches(0.15)
    return b


def lifeline(slide, x, y0, y1, label, col):
    box(slide, x - 0.85, y0, 1.7, 0.5, fill=col, radius=0.1, label=label, size=13, bold=True, color="FFFFFF")
    arrow(slide, x, y0 + 0.5, x, y1, color="C7CBE0", width=1.25, head=False, dash=True)


def msg(slide, x1, x2, y, label, color=INDIGO, size=11, above=True, dash=False):
    arrow(slide, x1, y, x2, y, color=color, width=1.75, dash=dash)
    lx, lw = min(x1, x2), abs(x2 - x1)
    text(slide, lx, y - 0.3 if above else y + 0.04, lw, 0.28, label, size=size, color=INK,
         align=PP_ALIGN.CENTER, font=MONO)


# ============================================================================= slides
# 1. title --------------------------------------------------------------------
s = prs.slides.add_slide(BLANK)
SLIDE_NO[0] += 1
bg = box(s, 0, 0, SW, SH, fill=INDIGO, shape=MSO_SHAPE.RECTANGLE, name="Background")
gradient(bg, "1E1B4B", "4C1D95", angle=315)
logo = box(s, 0.8, 0.8, 0.9, 0.9, fill=INDIGO, radius=0.22, label="~", size=40, bold=True, color="FFFFFF")
gradient(logo)
text(s, 0.8, 1.95, 11, 1.2, "LANBoard", size=66, bold=True, color="FFFFFF")
text(s, 0.8, 3.1, 11, 0.6, "A LAN-only, real-time collaborative whiteboard over our own TCP protocol",
     size=22, color="C7D2FE")
text(s, 0.8, 3.7, 11, 0.4, "Computer Networks Lab Project 2026  ·  Python 3.12 asyncio server  ·  PySide6 client",
     size=14, color="A5B4FC")
for i, k in enumerate("YJSN"):
    name, roll, area, col = TEAM[k]
    x = 0.8 + i * 3.0
    box(s, x, 4.9, 2.75, 1.45, fill="FFFFFF", radius=0.16)
    box(s, x + 0.2, 5.12, 0.5, 0.5, fill=col, shape=MSO_SHAPE.OVAL, label=name[0], size=16, bold=True,
        color="FFFFFF")
    text(s, x + 0.85, 5.1, 1.85, 0.3, name, size=14, bold=True, color=INK)
    text(s, x + 0.85, 5.4, 1.85, 0.3, roll, size=11, color=MUTED, font=MONO)
    text(s, x + 0.2, 5.82, 2.4, 0.4, area, size=12, color=TEXT2)
notes(s, """
Opening (any member, ~30 s). Introduce LANBoard: a whiteboard that runs entirely on a local network,
with a standalone asyncio server and a PySide6 desktop client that talk over our own framed TCP protocol.
Introduce the four of us and our areas: Yashvanth (server core and state), Jeevanandham (protocol and
networking), Sai Teja (canvas and drawing tools), Nithillakrishi (UI and collaboration features).
Each of us presents our own section; the coloured chip in the top-right corner of every slide shows who presents it.
""")

# 2. problem & goals ----------------------------------------------------------
s = content_slide("Problem & goals", "Y", "Collaborate on one canvas in a lab with no internet")
goals = [
    ("Real time", "Every stroke reaches everyone with minimal delay", INDIGO),
    ("Consistent", "All replicas show the same board, even under loss and reordering", VIOLET),
    ("Recoverable", "Late join, reconnect and server restart lose nothing", CYAN),
    ("Controlled", "Owner / editor / viewer roles, locks, password rooms", GREEN),
]
for i, (h, d, col) in enumerate(goals):
    x = 0.6 + i * 3.08
    box(s, x, 1.9, 2.85, 2.3, fill=CARD, radius=0.16)
    box(s, x + 0.25, 2.15, 0.55, 0.55, fill=col, shape=MSO_SHAPE.OVAL, label=str(i + 1), size=16, bold=True,
        color="FFFFFF")
    text(s, x + 0.25, 2.9, 2.4, 0.4, h, size=20, bold=True)
    text(s, x + 0.25, 3.35, 2.4, 0.8, d, size=13, color=TEXT2)
box(s, 0.6, 4.6, 12.13, 1.9, fill="EEF0FF", radius=0.16)
text(s, 0.95, 4.82, 11.4, 0.4, "Constraints from the brief", size=16, bold=True, color=INDIGO)
bullets(s, 0.95, 5.25, 11.4, [
    ("Client-server over TCP/IP sockets: ", "a central server coordinates sessions, state and event distribution."),
    ("No internet at the demo: ", "everything (fonts, icons, discovery) works on a hotspot or a switch."),
    ("Standalone executables: ", "PyInstaller one-dir builds for server and client."),
], size=14, gap=4)
notes(s, """
Yashvanth. The brief asks for a server-based whiteboard that works only on a LAN. We turned that into four goals:
real-time propagation, a provably consistent shared canvas, recovery from joins, disconnects and restarts,
and access control. The constraints matter for the design: the demo runs with the internet switched off,
so nothing may be downloaded at run time, and the brief requires standalone executables, which we build with PyInstaller.
""")

# 3. requirements coverage ----------------------------------------------------
s = content_slide("Requirements coverage at a glance", "Y")
rows = [
    ("1  Multi-user drawing", "Pen, highlighter, eraser, colours; server validates every op; permissioned clear; save / load"),
    ("2  Sessions", "Host or join by LAN IP + room code; participant list; join/leave notices; per-user colours and cursors"),
    ("3  Real-time events", "OP_SUBMIT → seq → broadcast; duplicates, gaps, delays handled; heartbeat, timeout, resume"),
    ("4  Chat", "CHAT_SEND → CHAT broadcast with sender, timestamp; history sent on join"),
    ("5  State & recovery", "SNAPSHOT for late joiners; tail replay on reconnect; per-user undo/redo with defined rules"),
    ("Rubric: security", "scrypt + HMAC challenge, roles, lock, kick; every op checked on the server"),
    ("Rubric: performance", "30 Hz live batching, 20 Hz cursors, RDP simplification, TCP_NODELAY, bounded queues"),
]
for i, (k, v) in enumerate(rows):
    y = 1.35 + i * 0.77
    box(s, 0.6, y, 12.13, 0.66, fill=CARD if i % 2 == 0 else "FFFFFF", radius=0.1, line=None)
    box(s, 0.78, y + 0.15, 0.36, 0.36, fill=GREEN, shape=MSO_SHAPE.OVAL, label="✓", size=13, bold=True,
        color="FFFFFF")
    text(s, 1.3, y + 0.17, 3.1, 0.35, k, size=15, bold=True)
    text(s, 4.4, y + 0.17, 8.2, 0.35, v, size=13, color=TEXT2)
notes(s, """
Yashvanth. A one-slide map of the brief onto what we built. Every core module is implemented, and so are the
rubric items on security and performance. Extras from the brief's advanced list: shapes, sticky notes, images,
PDF annotation, presentation mode, roles, version history (time-lapse), offline editing, export and LAN
auto-discovery. The rest of the deck explains how each of these works.
""")

# 4. architecture -------------------------------------------------------------
s = content_slide("Architecture: one server, one LAN", "Y")
lan = box(s, 0.6, 1.35, 12.13, 5.45, fill="FFFFFF", line="C7CBE0", radius=0.2, name="LAN boundary")
lan.line.dash_style = 4
text(s, 0.85, 1.48, 6, 0.3, "LAN (hotspot / switch, no internet)", size=12, color=MUTED, bold=True)
srv = box(s, 5.1, 2.35, 3.15, 2.9, fill="F3EAFF", line=VIOLET, radius=0.16, lw=2, anchor=MSO_ANCHOR.TOP,
          label=[[("Server", {"bold": True, "size": 18})], [("python -m lanboard.server", {"font": MONO, "size": 10, "color": MUTED})]])
for i, t in enumerate(("Listener + ClientConnection", "Room actors: board, op log, chat",
                       "Persistence (snapshot + JSONL)", "Discovery responder (UDP)")):
    box(s, 5.3, 3.12 + i * 0.5, 2.75, 0.4, fill="FFFFFF", radius=0.08, label=t, size=11, color=TEXT2)
for i, nm in enumerate(("Laptop A · owner", "Laptop B · editor", "Laptop C · viewer")):
    y = 1.95 + i * 1.5
    box(s, 1.0, y, 2.6, 1.05, fill="EEF0FF", line=INDIGO, radius=0.14,
        label=[[("Client (PySide6)", {"bold": True, "size": 14})], [(nm, {"size": 11, "color": MUTED})]])
    arrow(s, 3.62, y + 0.52, 5.08, 3.8, color=INDIGO, width=2.25, tail=True)
text(s, 3.2, 2.0, 2.1, 0.3, "TCP 5050 · framed JSON", size=11, color=INDIGO, bold=True, align=PP_ALIGN.CENTER)
for i, nm in enumerate(("Laptop D", "Laptop E")):
    y = 2.3 + i * 1.6
    box(s, 9.75, y, 2.5, 1.0, fill="ECFEFF", line=CYAN, radius=0.14,
        label=[[("Client", {"bold": True, "size": 14})], [(nm + " · joining", {"size": 11, "color": MUTED})]])
    arrow(s, 9.72, y + 0.5, 8.27, 3.0 + i * 1.3, color=CYAN, width=1.75, dash=True, tail=True)
text(s, 8.35, 5.4, 4.2, 0.6, "UDP 5051 broadcast probe → unicast reply\n(discovery only: \"Nearby servers\")",
     size=11, color=CYAN, align=PP_ALIGN.CENTER)
box(s, 5.1, 5.55, 3.15, 0.85, fill=CARD, line=GREEN, radius=0.12,
    label=[[("Disk: ", {"bold": True, "size": 12}), ("snapshot.json · ops.jsonl · history.jsonl", {"size": 11, "font": MONO})]])
arrow(s, 6.68, 5.27, 6.68, 5.53, color=GREEN, width=2)
notes(s, """
Yashvanth. Deployment view. One machine runs the standalone server; every participant runs the client.
All essential communication is TCP on port 5050 using our framed protocol. UDP on 5051 is used only for discovery:
a client broadcasts a probe and servers answer by unicast, which fills the Nearby servers list.
Inside the server: one ClientConnection per socket, one Room object per room holding the authoritative board,
the op log and chat, and a persistence layer that writes the log before broadcasting. No internet is needed anywhere.
""")

# 5. layers -------------------------------------------------------------------
s = content_slide("Client and server layers", "Y", "Same model code on both sides: common/model.py applies every op deterministically")
cols = [
    ("Client (Qt event loop, one thread)", INDIGO, [
        ("UI", "MainWindow, side panel tabs, toasts  ·  client/ui/"),
        ("Canvas", "CanvasView / CanvasScene, tools, items  ·  client/canvas/"),
        ("Sync", "SessionController: board + pending = view  ·  controller.py"),
        ("Transport", "NetClient (QTcpSocket), FaultInjector  ·  net.py, chaos.py"),
    ]),
    ("Shared core  (common/)", VIOLET, [
        ("Codec", "encode / FrameDecoder / PartAssembler  ·  protocol.py"),
        ("Schema", "MsgType + payload contracts  ·  messages.py"),
        ("Model", "Board.apply(op), validate_op, hash()  ·  model.py"),
        ("Undo, auth", "UndoManager · scrypt / HMAC  ·  undo.py, auth.py"),
    ]),
    ("Server (asyncio, one thread)", "0F766E", [
        ("Listener", "start_server, TCP_NODELAY, max clients  ·  server.py"),
        ("Connection", "reader loop + writer loop, bounded queue"),
        ("Room", "permission → rate limit → validate → seq → log → broadcast  ·  room.py"),
        ("Storage", "atomic snapshot, write-ahead JSONL  ·  persistence.py"),
    ]),
]
for c, (title, col, layers) in enumerate(cols):
    x = 0.6 + c * 4.1
    box(s, x, 1.65, 3.9, 0.5, fill=col, radius=0.1, label=title, size=14, bold=True, color="FFFFFF")
    for i, (h, d) in enumerate(layers):
        y = 2.3 + i * 1.12
        box(s, x, y, 3.9, 0.98, fill=CARD, radius=0.1, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE,
            label=[[(h, {"bold": True, "size": 14})], [(d, {"size": 11, "color": TEXT2})]])
arrow(s, 4.52, 4.5, 4.68, 4.5, color=MUTED, width=2, tail=True)
arrow(s, 8.62, 4.5, 8.78, 4.5, color=MUTED, width=2, tail=True)
notes(s, """
Yashvanth. Three columns: client, shared core, server. The key decision is the shared core: the server and every
client run the same Board.apply from common/model.py, so the same ops in the same order give byte-identical boards,
which we check with hashes. Both processes are single-threaded event loops: Qt on the client, asyncio on the server.
That means no locks: all room state is mutated on one thread, so there are no data races by construction.
""")

# 6. Yashvanth contribution ----------------------------------------------------
def contribution(who, files, decisions, shot=None, crop=None, shot_caption=None, note=""):
    name, roll, area, col = TEAM[who]
    s = content_slide(f"My area: {area}", who, f"{name} · {roll}")
    text(s, 0.6, 1.65, 5.6, 0.35, "Key files", size=15, bold=True, color=col)
    mono_box(s, 0.6, 2.05, 5.6, 0.27 * len(files) + 0.35, files, size=11)
    y = 2.05 + 0.27 * len(files) + 0.55
    text(s, 0.6, y, 5.6, 0.35, "Design decisions", size=15, bold=True, color=col)
    bullets(s, 0.6, y + 0.42, 5.6, decisions, size=13, gap=4, dot=col, h=6.85 - y - 0.42)
    if shot:
        _pic, (px, py, pw, ph) = picture(s, shot, 6.6, 1.65, w=6.13, h=4.95, crop=crop)
        if shot_caption:
            caption(s, 6.6, py + ph + 0.08, 6.13, shot_caption)
    notes(s, note)
    return s


contribution("Y", [
    "server/server.py       Server, ClientConnection",
    "server/room.py         Room.submit() pipeline",
    "common/model.py        Board.apply, hash()",
    "server/persistence.py  RoomStore, atomic I/O",
    "server/dashboard.py    terminal dashboard",
    "client/controller.py   shared with Jeevanandham",
], [
    ("Server-assigned seq: ", "one total order per room, no clocks needed."),
    ("Room as an actor: ", "all mutations on the asyncio loop thread."),
    ("Write-ahead log: ", "append to ops.jsonl before broadcasting."),
    ("Bounded send queue: ", "a slow client is dropped as \"lagging\", never stalls the room."),
], shot="server_dashboard.png", shot_caption="Real output of python -m lanboard.server --dashboard (4 people + 4 stress bots)",
    note="""
Yashvanth, my section starts here. I built the server core: the listener and per-connection reader and writer
loops in server.py, the Room class with the op pipeline in room.py, the shared board model in model.py and
persistence. The screenshot is the real server dashboard: each room with its seq, board hash and op rate,
and per user the role, RTT, bytes in and out, and outbound queue depth. Queue depth is the early warning for a
client about to be dropped as lagging. I will show Room.submit and Board.apply in the code.
""")

# 7. ordering -----------------------------------------------------------------
s = content_slide("Ordering: the server's seq decides", "Y",
                  "Concurrent edits to the same sticky note: last writer by server order wins, everywhere")
xa, xs, xb = 1.6, 5.4, 9.2
lifeline(s, xa, 1.7, 6.6, "Client A", INDIGO)
lifeline(s, xs, 1.7, 6.6, "Server (room)", VIOLET)
lifeline(s, xb, 1.7, 6.6, "Client B", "DB2777")
msg(s, xa, xs, 2.75, "OP_SUBMIT fill=red  (A:7)")
msg(s, xb, xs, 3.05, "OP_SUBMIT fill=blue (B:3)", color="DB2777", above=False)
box(s, xs - 1.05, 3.55, 2.1, 0.62, fill="F3EAFF", line=VIOLET, radius=0.08,
    label=[[("seq 41 = A:7", {"font": MONO, "size": 11})], [("seq 42 = B:3", {"font": MONO, "size": 11})]])
msg(s, xs, xa, 4.6, "OP seq 41 (red)", color=VIOLET)
msg(s, xs, xb, 4.6, "OP seq 41 (red)", color=VIOLET)
msg(s, xs, xa, 5.35, "OP seq 42 (blue)", color=VIOLET)
msg(s, xs, xb, 5.35, "OP seq 42 (blue)", color=VIOLET)
box(s, 0.75, 5.85, 1.7, 0.5, fill="DBEAFE", radius=0.08, label="blue ✓", size=13, bold=True, color="1E3A8A")
box(s, 8.35, 5.85, 1.7, 0.5, fill="DBEAFE", radius=0.08, label="blue ✓", size=13, bold=True, color="1E3A8A")
box(s, 10.3, 1.7, 2.45, 4.9, fill=CARD, radius=0.14)
bullets(s, 10.45, 1.9, 2.2, [
    ("Total order: ", "seq = room seq + 1 on accept."),
    ("Echo = ACK: ", "the sender also gets its OP back; it leaves pending."),
    ("Optimistic UI: ", "own ops drawn at once (view = board + pending)."),
    ("LWW per key ", "by seq; edits to a deleted object are no-ops."),
], size=12, gap=6, h=4.6)
notes(s, """
Yashvanth. How we order operations. Clients never apply each other's ops directly; every edit goes to the server
as OP_SUBMIT with a unique op_id. The room assigns the next sequence number, applies it, appends it to the log and
broadcasts it to everyone including the sender. That echo is the acknowledgement: the client moves the op from
pending to confirmed. In the example, A and B recolour the same sticky at the same moment. The server happened to
receive A first, so A gets 41 and B gets 42. Everyone applies 41 then 42, so blue wins on every screen.
No vector clocks or OT needed, because a single server totally orders the log.
""")

# 8. security -----------------------------------------------------------------
s = content_slide("Security & access control", "Y", "The client mirrors permissions; the server enforces them")
box(s, 0.6, 1.65, 5.9, 2.35, fill=CARD, radius=0.14)
text(s, 0.85, 1.8, 5.5, 0.35, "Password rooms without sending the password", size=15, bold=True, color=INDIGO)
mono_box(s, 0.85, 2.25, 5.4, 1.55, [
    "verifier = scrypt(password, salt)   # stored",
    "S→C CHALLENGE {nonce}               # fresh",
    "C→S JOIN {proof}",
    "proof = HMAC-SHA256(verifier, nonce)",
], size=11)
bullets(s, 0.6, 4.25, 5.9, [
    ("Roles: ", "owner (clear, lock, kick, set roles), editor, viewer."),
    ("Every op checked: ", "viewer → FORBIDDEN, locked room → LOCKED."),
    ("Validation: ", "8 MiB frame cap, schema + coordinate checks, 120 ops/s token bucket."),
    ("Server assigns ", "user_id and role; client-sent ids are never trusted."),
    ("Optional TLS ", "(--tls-cert / --tls-key) for encrypted transport."),
], size=13, gap=5, h=2.6)
_p, (px, py, pw, ph) = picture(s, "viewer_locked.png", 6.85, 1.65, w=5.88, h=2.45, crop=(0, 0, 0.27, 0.45))
caption(s, 6.85, py + ph + 0.06, 5.88, "Ravi's own window: a viewer in a locked room; drawing tools disabled")
_p, (px, py, pw, ph) = picture(s, "password_prompt.png", 6.85, 4.55, w=5.88, h=1.85, crop=(0.22, 0.26, 0.22, 0.44))
caption(s, 6.85, py + ph + 0.06, 5.88, "Joining the protected room VIVA-7: the real password prompt")
notes(s, """
Yashvanth. Access control is enforced on the server in Room.check_permission, before validation and sequencing.
Passwords: the room stores only a scrypt verifier and salt. Every connection gets a fresh random nonce in CHALLENGE;
the client proves knowledge with an HMAC of the nonce keyed by the verifier, so the password never crosses the wire
and a captured proof cannot be replayed. Roles: owner, editor, viewer; a locked room accepts ops only from the owner.
Inputs are validated: frame size cap, op schema and coordinate bounds, and a per-client token bucket of 120 ops per second.
""")

# 9. persistence ----------------------------------------------------------------
s = content_slide("Persistence & durable history", "Y", "Crash-safe: an accepted op is on disk before anyone sees it")
steps = [("OP accepted", "seq assigned", VIOLET), ("append", "ops.jsonl", INDIGO), ("broadcast", "OP to room", CYAN),
         ("every 50 ops / 10 s", "compaction", GREEN)]
for i, (a, b, col) in enumerate(steps):
    x = 0.6 + i * 3.1
    box(s, x, 1.75, 2.7, 1.0, fill=col, radius=0.14,
        label=[[(a, {"bold": True, "size": 15, "color": "FFFFFF"})], [(b, {"size": 12, "color": "FFFFFF", "font": MONO})]])
    if i < 3:
        arrow(s, x + 2.72, 2.25, x + 3.08, 2.25, color=MUTED, width=2)
files = [("snapshot.json", "board at seq S, written atomically (tmp + fsync + os.replace)"),
         ("ops.jsonl", "every op after S: the write-ahead log, appended before broadcast"),
         ("history_base.json", "board where the time-lapse starts (seq B)"),
         ("history.jsonl", "every op in (B, S]: compacted ops move here, capped at 100k ops / 32 MiB")]
for i, (f, d) in enumerate(files):
    y = 3.15 + i * 0.68
    box(s, 0.6, y, 2.6, 0.55, fill="0F1220", radius=0.08, label=f, size=12, color="A5F3FC", font=MONO)
    text(s, 3.4, y + 0.13, 5.0, 0.4, d, size=12, color=TEXT2)
picture(s, "history.png", 8.65, 3.1, w=4.08, crop=(0.5, 0.0, 0.0, 0.46))
caption(s, 8.65, 6.45, 4.08, "History tab: time-lapse replay at seq 16 of 30")
text(s, 0.6, 6.0, 7.8, 0.6, "Restart: board = snapshot + replay of ops.jsonl. Resume and time-lapse still reach back past the snapshot.",
     size=13, color=INK, bold=True)
notes(s, """
Yashvanth. The op pipeline writes each accepted op to ops.jsonl before broadcasting it, so a crash after a client
has seen an op cannot lose it. Every 50 ops or 10 seconds the room compacts: it writes an atomic snapshot and moves
the compacted ops into history.jsonl. On restart the loader reads the snapshot and replays the tail; torn last lines
are skipped. Because the full history survives, the History tab can replay the session from the first op and
restore any earlier version as a new op.
""")

# 10. Jeevanandham contribution ------------------------------------------------
contribution("J", [
    "common/protocol.py    FrameDecoder, PartAssembler",
    "common/messages.py    MsgType catalogue",
    "client/net.py         NetClient state machine",
    "client/chaos.py       FaultInjector",
    "server/discovery.py   UDP responder",
    "client/discovery.py   DiscoveryBrowser",
    "client/controller.py  shared with Yashvanth",
], [
    ("Length-prefixed frames: ", "TCP has no message boundaries."),
    ("JSON payloads: ", "debuggable in the Network Inspector."),
    ("Resume, not restart: ", "resume_token + last_seq after any drop."),
    ("Multi-part transfers ", "for boards larger than 1 MiB."),
], shot="network_dark_inspector.png", crop=(0.65, 0.08, 0.0, 0.0),
    shot_caption="Inspector: RESYNC after a dropped OP, then OP_BATCH 68-71 fills the gap",
    note="""
Jeevanandham, my section. I own the wire protocol and the client transport: the framing codec and multi-part
transfers in protocol.py, the message catalogue in messages.py, the NetClient handshake, heartbeat and reconnect
state machine, LAN discovery on both sides, and the Chaos Lab fault injector. The screenshot shows our own
Network Inspector catching a real recovery: an OP was dropped by the Chaos Lab, the client noticed the seq gap,
sent RESYNC and received the missing ops as one OP_BATCH.
""")

# 11. why TCP -------------------------------------------------------------------
s = content_slide("Why TCP (and UDP only for discovery)", "J")
tbl = [("Need", "TCP", "UDP"), ("Every op delivered", "✓ retransmits", "✗ we'd rebuild ARQ"),
       ("In-order op log", "✓ byte order", "✗ reorders"), ("Flow control / slow clients", "✓ window, drain()", "✗"),
       ("Broadcast to find servers", "✗", "✓ subnet broadcast"), ("Loss tolerant probe", "-", "✓ retry every 2 s")]
for r, row in enumerate(tbl):
    for c, cell in enumerate(row):
        x, w = [0.6, 3.6, 5.3][c], [2.9, 1.6, 1.75][c]
        y = 1.55 + r * 0.6
        hdr = r == 0
        box(s, x, y, w, 0.52, fill=(INDIGO if hdr else (CARD if r % 2 else "FFFFFF")), radius=0.06,
            label=cell, size=12 if not hdr else 13, bold=hdr or c == 0,
            color="FFFFFF" if hdr else INK, align=PP_ALIGN.LEFT if c == 0 else PP_ALIGN.CENTER)
bullets(s, 0.6, 5.35, 6.5, [
    ("TCP_NODELAY ", "on both ends: no Nagle delay for small frames."),
    ("Heartbeat ", "PING every 3 s; silent for 12 s = dead peer."),
    ("Discovery: ", "probe 255.255.255.255 + directed broadcasts; reply by unicast."),
], size=13, gap=5, h=1.6)
_p, (px, py, pw, ph) = picture(s, "connect.png", 7.35, 1.55, w=5.38, crop=(0.415, 0.0, 0.0, 0.42))
caption(s, 7.35, py + ph + 0.08, 5.38, "Real discovery: the client found our test server and its two rooms (one password-protected)")
notes(s, """
Jeevanandham. Why TCP: the op log needs exactly what TCP provides, reliable in-order delivery with flow control.
Rebuilding acknowledgements and ordering over UDP would add complexity with no benefit on a LAN. We disable Nagle
with TCP_NODELAY because our frames are small and interactive. Liveness: the client pings every 3 seconds and
either side treats 12 seconds of silence as a dead link, which catches a pulled cable that TCP alone would not notice.
UDP is used for one job only: discovery, because only UDP can broadcast. The screenshot is a real discovery result.
""")

# 12. frame format --------------------------------------------------------------
s = content_slide("Frame format & message catalogue", "J", "[ length u32 big-endian | type u8 | UTF-8 JSON ]  ·  length = 1 + len(payload)  ·  max 8 MiB")
fx = 0.6
for w, lab, sub, col in [(2.6, "length", "4 bytes, u32 BE", INDIGO), (1.6, "type", "1 byte", VIOLET),
                         (7.9, "payload", "N-1 bytes of UTF-8 JSON", "0F766E")]:
    box(s, fx, 1.7, w, 0.95, fill=col, radius=0.06,
        label=[[(lab, {"bold": True, "size": 16, "color": "FFFFFF"})], [(sub, {"size": 11, "color": "E0E7FF"})]])
    fx += w + 0.02
mono_box(s, 0.6, 2.85, 12.13, 0.95, [
    "00 00 00 45 | 01 | {\"proto\":1,\"app\":\"lanboard\",\"username\":\"alice\",\"client_id\":\"3f2a9c\"}",
    "HELLO frame from encode(): 0x45 = 69 = 1 type byte + 68 payload bytes  (73 bytes on the wire)",
], size=12)
groups = [("0x0_ control", "HELLO CHALLENGE JOIN JOINED SNAPSHOT OP_BATCH ERROR PING PONG RESYNC HASH ROOMS"),
          ("0x1_ presence", "USER_JOINED USER_LEFT USER_UPDATE CURSOR STROKE_LIVE STROKE_END LASER VIEWPORT SIGNAL"),
          ("0x2_ board ops", "OP_SUBMIT  OP {seq,user_id,op_id,ts,op}  OP_REJECT"),
          ("0x3_ chat", "CHAT_SEND  CHAT"),
          ("0x4_ admin", "SET_ROLE KICK ROOM_SETTINGS ROOM_UPDATE"),
          ("0x5_ saves/history", "SAVE LIST_SAVES LOAD HISTORY_REQ HISTORY"),
          ("0x6_ blobs", "BLOB_PUT BLOB_GET BLOB_DATA BLOB_STORED (sha256, 192 KiB chunks)")]
for i, (g, m) in enumerate(groups):
    c, r = i % 2, i // 2
    x, y = 0.6 + c * 6.12, 4.0 + r * 0.72
    box(s, x, y, 6.0, 0.62, fill=CARD, radius=0.08, align=PP_ALIGN.LEFT,
        label=[[(g + "   ", {"bold": True, "size": 12, "color": INDIGO}), (m, {"size": 10, "font": MONO, "color": TEXT2})]])
notes(s, """
Jeevanandham. TCP is a byte stream, so we frame every message: a 4-byte big-endian length, a 1-byte message type,
then a UTF-8 JSON payload. The decoder in protocol.py is incremental: it buffers partial reads and can return
several frames from one read. The hex line is a real HELLO frame produced by our encode function.
The type byte's high nibble groups the catalogue: control, presence, board ops, chat, admin, history, blobs.
Ephemeral messages such as cursors and live strokes carry no seq and are never logged; only OP is ordered.
Payloads above 1 MiB, like a big SNAPSHOT, are split into parts with an xid and reassembled before use.
""")

# 13. join sequence ---------------------------------------------------------------
s = content_slide("Join and late join", "J", "A newcomer gets the whole board before any live op")
xc, xsv, xo = 1.8, 6.4, 11.0
lifeline(s, xc, 1.6, 6.75, "New client", CYAN)
lifeline(s, xsv, 1.6, 6.75, "Server", VIOLET)
lifeline(s, xo, 1.6, 6.75, "Others", INDIGO)
msg(s, xc, xsv, 2.55, "HELLO {proto, username, client_id}")
msg(s, xsv, xc, 3.05, "CHALLENGE {nonce}", color=VIOLET)
msg(s, xc, xsv, 3.55, "JOIN {room, proof?, resume_token?, last_seq?}")
msg(s, xsv, xc, 4.05, "JOINED {user_id, role, resume_token, chat}", color=VIOLET)
msg(s, xsv, xo, 4.05, "USER_JOINED", color=VIOLET)
msg(s, xsv, xc, 4.6, "SNAPSHOT {seq, board, hash}  (fresh join)", color=GREEN)
msg(s, xsv, xc, 5.15, "or OP_BATCH {ops > last_seq}  (resume)", color=GREEN, dash=True)
msg(s, xsv, xc, 5.75, "OP seq n+1 …  live from here", color=VIOLET)
msg(s, xsv, xo, 5.75, "OP seq n+1 …", color=VIOLET)
notes(s, """
Jeevanandham. The handshake. HELLO carries the protocol version so mismatched builds fail cleanly. The server always
sends a fresh nonce. JOIN carries the room code and, if needed, the password proof; on a reconnect it also carries
the resume token and last applied seq. JOINED returns the server-assigned user id, role and recent chat. Then the
state: a fresh joiner gets a SNAPSHOT of the board at seq n, a resuming client gets only the ops after its last_seq.
The server queues all parts of a SNAPSHOT at once, so no OP is interleaved, and live ops continue from n+1.
""")

# 14. reliability -------------------------------------------------------------------
s = content_slide("Reliability: duplicates, gaps, divergence", "J", "SessionController._on_op applies OPs strictly in seq order")
cards = [("Duplicate", "seq ≤ board.seq", "drop it (idempotent). Server also dedupes OP_SUBMIT by op_id, so resends are safe.", AMBER),
         ("Gap / delay", "seq > board.seq + 1", "buffer it; if not filled in 400 ms send RESYNC{last_seq}; server replays the tail.", CYAN),
         ("Divergence", "HASH every 25 ops", "compare sha256 of our board; on mismatch RESYNC{-1} → fresh SNAPSHOT (self-heal).", GREEN)]
for i, (h, cond, act, col) in enumerate(cards):
    x = 0.6 + i * 4.1
    box(s, x, 1.65, 3.9, 2.2, fill=CARD, radius=0.14)
    box(s, x + 0.2, 1.85, 1.6, 0.42, fill=col, radius=0.21, label=h, size=13, bold=True, color="FFFFFF")
    text(s, x + 0.2, 2.42, 3.5, 0.35, cond, size=14, bold=True, font=MONO)
    text(s, x + 0.2, 2.85, 3.5, 0.95, act, size=12, color=TEXT2)
_p, (px, py, pw, ph) = picture(s, "network_dark_inspector.png", 0.6, 4.05, w=7.4, h=2.45, crop=(0.67, 0.115, 0.0, 0.64))
caption(s, px, py + ph + 0.06, 7.4, "Real capture: OP 70 and 71 dropped by the Chaos Lab → RESYNC → recovered")
_p, (px, py, pw, ph) = picture(s, "light.png", 8.35, 4.05, w=4.38, crop=(0.235, 0.0, 0.53, 0.94))
caption(s, 8.35, py + ph + 0.08, 4.38, "Consistency badge: last HASH verified")
notes(s, """
Jeevanandham, with the controller logic shared with Yashvanth. Three failure cases and their fixes.
Duplicates: any OP with seq at or below what we have is dropped, and the server keeps accepted op_ids per session,
so a resend after reconnect is acknowledged again but never applied twice. Gaps: an OP that skips ahead is buffered;
if the hole is not filled within 400 ms the client sends RESYNC with its last seq and the server replays the missing
ops. Delayed frames simply wait in the buffer. Divergence: every 25 ops the server broadcasts the board hash; if ours
differs we discard the replica and fetch a snapshot. The green badge is backed by that evidence.
""")

# 15. reconnect + offline -------------------------------------------------------------
s = content_slide("Reconnect and offline editing", "J", "Cut the cable, keep drawing, plug back in: nothing is lost")
_p, (px, py, pw, ph) = picture(s, "offline.png", 0.6, 1.55, w=6.0, h=4.3, crop=(0, 0, 0.3, 0.0))
caption(s, px, py + ph + 0.06, 6.0, "Link cut: \"Offline · edits queued (3)\", banner, 3 pending")
_p, (px, py, pw, ph) = picture(s, "reconnected.png", 6.85, 1.55, w=5.88, h=4.3, crop=(0, 0, 0.3, 0.0))
caption(s, px, py + ph + 0.06, 5.88, "Resumed: same user id, missing ops replayed, queue flushed, in sync")
text(s, 0.6, 6.4, 12.1, 0.5, "Backoff 0.5, 1, 2, 4, 5 s (±20 % jitter) → JOIN{resume_token, last_seq} → OP_BATCH → resend pending ops (server dedupes by op_id)",
     size=13, bold=True, color=INK, font=FONT)
notes(s, """
Jeevanandham. The Chaos Lab cut-the-cable button aborts the TCP socket. NetClient switches to reconnecting and retries
with exponential backoff plus jitter, so many clients do not stampede the server. Meanwhile the user keeps drawing:
ops stay in the pending queue and are shown immediately. On reconnect the JOIN carries the resume token, so we keep
the same user id and role, and last_seq, so the server sends only what we missed. Then the pending ops are resent in
order. If the server had already accepted one before the drop, its op_id dedupe acknowledges it without applying it twice.
""")

# 16. performance ----------------------------------------------------------------------
s = content_slide("Performance: less traffic, lower delay", "J", "Measured with our real codec and the bot stress tool on one laptop (loopback)")
cd = CategoryChartData()
cd.categories = ["Live preview\n(1 s stroke)", "Committed stroke\n(RDP)", "Cursor\n(per second)"]
cd.add_series("Naive", (10497, 1851, 3120))
cd.add_series("LANBoard", (3927, 499, 520))
gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.6), Inches(1.6), Inches(6.6), Inches(4.4), cd)
ch = gf.chart
ch.has_legend = True
ch.legend.position = XL_LEGEND_POSITION.TOP
ch.legend.include_in_layout = False
ch.legend.font.size = Pt(12)
ch.legend.font.name = FONT
ch.has_title = True
ch.chart_title.text_frame.text = "Bytes on the wire"
ch.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(14)
ch.chart_title.text_frame.paragraphs[0].runs[0].font.bold = True
ch.chart_title.text_frame.paragraphs[0].runs[0].font.name = FONT
plot = ch.plots[0]
plot.gap_width = 60
plot.has_data_labels = True
plot.data_labels.font.size = Pt(11)
plot.data_labels.font.name = FONT
plot.data_labels.number_format = '#,##0'
plot.data_labels.number_format_is_linked = False
plot.data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
for ser, col in zip(plot.series, ("C7CBE0", INDIGO)):
    ser.format.fill.solid()
    ser.format.fill.fore_color.rgb = rgb(col)
ch.value_axis.visible = False
ch.value_axis.has_major_gridlines = False
ch.category_axis.tick_labels.font.size = Pt(11)
ch.category_axis.tick_labels.font.name = FONT
ch.category_axis.format.line.color.rgb = rgb(LINE)
stat(s, 7.55, 1.6, 2.5, "−63 %", "live-stroke bytes: 30 Hz batching (31 vs 121 frames)")
stat(s, 10.23, 1.6, 2.5, "−73 %", "commit size: RDP 121 → 26 points")
stat(s, 7.55, 3.1, 2.5, "0.2 ms", "loopback RTT, 8 bots × 20 ops/s (147 ops/s)", color=GREEN)
stat(s, 10.23, 3.1, 2.5, "0", "resyncs, rejects, hash mismatches; all 8 replicas converged", color=GREEN)
bullets(s, 7.55, 4.7, 5.2, [
    ("TCP_NODELAY: ", "no Nagle / delayed-ACK stalls."),
    ("Cursors ≤ 20 Hz, ", "latest position wins."),
    ("Bounded queue ", "(4000 frames) isolates slow clients."),
    ("Redraw only ", "changed QGraphicsItems."),
], size=13, gap=4, h=1.9)
notes(s, """
Jeevanandham. Numbers we measured, not estimates. A one-second stroke sampled at 120 Hz costs 121 frames and about
10.5 KB if every mouse event is sent; batching to 30 Hz sends 31 frames and 3.9 KB. When the stroke is committed,
Ramer-Douglas-Peucker simplification at 0.6 screen pixels cuts 121 points to 26, from 1,851 to 499 bytes. Cursor
updates are capped at 20 Hz. The bot stress test with 8 bots at 20 ops per second ran 1,771 ops in 12 seconds with
0.2 ms loopback RTT, zero resyncs and every replica's hash equal to the server's. On real Wi-Fi the RTT is higher,
which the HUD shows live.
""")

# 17. chaos lab ------------------------------------------------------------------------
s = content_slide("Chaos Lab: break the network on purpose", "J", "Fault injection on frames this client receives, then watch sync recover")
picture(s, "chaos_lab.png", 0.6, 1.6, h=5.3, crop=(0.65, 0.07, 0.0, 0.0))
knobs = [("Latency", "delays every inbound frame → RTT rises in the HUD"),
         ("Jitter", "random extra delay → frames arrive out of order; still applied by seq"),
         ("Drop OPs", "loses OP frames → seq gap → RESYNC → OP_BATCH"),
         ("Duplicate", "delivers twice → second copy ignored"),
         ("Cut the cable", "aborts TCP → reconnect, resume, replay queued edits")]
for i, (k, d) in enumerate(knobs):
    y = 1.7 + i * 0.98
    box(s, 5.4, y, 7.33, 0.85, fill=CARD, radius=0.1, align=PP_ALIGN.LEFT,
        label=[[(k, {"bold": True, "size": 15, "color": RED if k == "Cut the cable" else INDIGO})], [(d, {"size": 12, "color": TEXT2})]])
text(s, 5.4, 6.65, 7.3, 0.3, "Counters in the shot: dropped 2 · duplicated 3 · delayed 42, and the badge still says In sync", size=12, color=MUTED)
notes(s, """
Jeevanandham. The Chaos Lab is our way to prove the brief's requirement on delayed, duplicated and missing updates
live, instead of only claiming it. FaultInjector.process sits between the frame decoder and the controller. It can
delay frames, add jitter so they arrive out of order, drop OP frames and duplicate frames. Each fault exercises one
mechanism from the reliability slide, and the inspector tags each affected frame. Cut the cable exercises reconnect
and offline editing. In the screenshot, with 120 ms latency, 20 percent drops and 15 percent duplicates, the board
still reports in sync.
""")

# 18. Sai Teja contribution ------------------------------------------------------------
contribution("S", [
    "canvas/view.py    CanvasView: zoom, PDF, export",
    "canvas/scene.py   CanvasScene: one item per id",
    "canvas/tools.py   pen, shapes, select, laser",
    "canvas/items.py   stroke, sticky, image items",
    "common/geometry.py  RDP, hit tests, Smart Ink",
    "common/undo.py      per-user UndoManager",
], [
    ("QGraphicsScene: ", "hit-testing, zoom, export for free."),
    ("Incremental redraw: ", "only changed ids are rebuilt."),
    ("One stroke = one op ", "after RDP; live preview is ephemeral."),
    ("Undo = new inverse op ", "with its own seq."),
], shot="light.png", crop=(0.0, 0.05, 0.27, 0.03), shot_caption="Pen, shapes, text, stickies with votes, live cursors and a live stroke",
    note="""
Sai Teja, my section. I built the canvas: CanvasView for navigation, images, PDF import and export; CanvasScene which
keeps one graphics item per board object and rebuilds only the ids that changed; the tools; the item classes;
geometry helpers including RDP simplification, hit testing and Smart Ink shape recognition; and per-user undo.
A key decision: while you draw, points go out as ephemeral STROKE_LIVE previews; on pen-up the simplified stroke is
committed as one add op, which is also one undo step.
""")

# 19. drawing tools ---------------------------------------------------------------------
s = content_slide("Drawing tools", "S", "Every tool produces ops; the canvas never edits the board directly")
picture(s, "light.png", 0.6, 1.6, h=5.2, crop=(0.0, 0.12, 0.62, 0.0))
tools = [("V Select / move / resize", "rubber band, handles; drag = one update batch"),
         ("P Pen · M Highlighter", "30 Hz live preview, RDP on commit; highlighter at 35 % opacity"),
         ("E Eraser", "hit-tests strokes along the drag, deletes as one op"),
         ("L A R O Line, arrow, rect, ellipse", "Shift constrains angles / squares"),
         ("T Text · N Sticky note", "editable in place; stickies support dot voting"),
         ("K Laser pointer", "fading trail sent as LASER; never saved"),
         ("Smart Ink", "hold still at the end: a rough circle snaps into an ellipse")]
for i, (k, d) in enumerate(tools):
    y = 1.6 + i * 0.75
    box(s, 4.6, y, 8.13, 0.66, fill=CARD, radius=0.08, align=PP_ALIGN.LEFT,
        label=[[(k + "   ", {"bold": True, "size": 13, "color": "DB2777"}), (d, {"size": 12, "color": TEXT2})]])
notes(s, """
Sai Teja. The tool rail with its keyboard shortcuts. All tools follow one rule: the canvas never edits the board
directly. A tool builds an op and hands it to the controller, which applies it optimistically to the view and sends it.
The pen streams live points and commits a simplified stroke on release. The eraser hit-tests along the drag path using
geometry.hit_test and deletes everything it touched in one op. Smart Ink runs recognize_shape on the points if you
hold the pen still, and replaces a rough circle, rectangle, line or arrow with a clean shape.
""")

# 20. shapes, sticky, images, pdf ---------------------------------------------------------
s = content_slide("Shapes, stickies, images & PDF", "S")
_p, (px, py, pw, ph) = picture(s, "canvas_dark_selection.png", 0.6, 1.45, w=7.2, crop=(0.0, 0.18, 0.28, 0.25))
caption(s, 0.6, py + ph + 0.08, 7.2, "Dark board: two sticky notes selected; PDF page annotated by another user")
bullets(s, 8.15, 1.45, 4.6, [
    ("8 object types: ", "stroke, line, arrow, rect, ellipse, text, sticky, image."),
    ("IDs ", "<user_id>-<counter>: unique without a round trip."),
    ("Images & PDF pages ", "are sha256 content-addressed blobs, uploaded in 192 KiB chunks; the op only references the hash."),
    ("PDF import: ", "pypdfium2 renders each page; the deck is one batch op = one undo."),
    ("Export: ", "PNG, SVG or PDF from the scene."),
], size=13, gap=8, h=5.0)
notes(s, """
Sai Teja. The board model has eight object types, each with a fixed geometry schema validated on the server.
Object ids combine the user id with a local counter, so they are globally unique without asking the server.
Images and PDF pages are not inlined in ops: they are uploaded once as content-addressed blobs in chunks, and the
add op only carries the hash, so a late joiner fetches each image once. Importing a PDF rasterises each page and
submits all pages as one batch, which is a single undo step. In the screenshot another user circled the frame header
on an imported slide.
""")

# 21. undo/redo --------------------------------------------------------------------------
s = content_slide("Undo / redo with several people editing", "S", "Per-user stacks; undo submits the inverse as a brand-new op")
rules = [("add X", "delete X", "skipped if someone already erased X"),
         ("delete X", "restore X", "skipped if X is live again"),
         ("update X.k", "set k back", "only keys still holding our value"),
         ("batch", "inverse of each part", "reverse order, each filtered"),
         ("clear (by me)", "restore my cleared ids", "others' undo entries become no-ops")]
for i, row in enumerate([("My op", "Undo emits", "Concurrency rule")] + rules):
    for c, cell in enumerate(row):
        x, w = [0.6, 2.75, 5.25][c], [2.05, 2.4, 3.6][c]
        y = 1.6 + i * 0.62
        hdr = i == 0
        box(s, x, y, w, 0.54, fill="DB2777" if hdr else (CARD if i % 2 else "FFFFFF"), radius=0.06,
            label=cell, size=12, bold=hdr, color="FFFFFF" if hdr else INK,
            font=FONT if hdr or c == 2 else MONO, align=PP_ALIGN.LEFT)
xa, xb, xs2 = 9.6, 12.2, 10.9
box(s, 9.2, 1.6, 3.53, 4.9, fill=CARD, radius=0.14)
text(s, 9.4, 1.75, 3.2, 0.3, "Example", size=14, bold=True, color="DB2777")
steps = [("A", "moves sticky → seq 50", INDIGO), ("B", "recolours it → seq 51", "DB2777"),
         ("A", "presses Undo", INDIGO), ("→", "inverse: move back → seq 52", VIOLET),
         ("✓", "B's colour stays; A's move is undone on every screen", GREEN)]
for i, (who, t, col) in enumerate(steps):
    y = 2.2 + i * 0.82
    box(s, 9.4, y, 0.45, 0.45, fill=col, shape=MSO_SHAPE.OVAL, label=who, size=12, bold=True, color="FFFFFF")
    text(s, 10.0, y + 0.02, 2.6, 0.7, t, size=12, color=INK)
text(s, 0.6, 5.5, 8.3, 0.9, "A new action clears my redo stack. One stroke (pen-down to pen-up) is one entry. "
     "Undo never removes someone else's work.", size=14, color=TEXT2)
notes(s, """
Sai Teja. The brief asks for clearly defined undo behaviour with concurrent users. Our rules: each user's stack has only
their own confirmed ops. Undo is not a rewind; it computes the inverse and submits it as a new op that gets its own
seq, so every replica converges. Before sending, the inverse is filtered against the current board: if someone already
erased the object I added, there is nothing to undo and we skip to the next entry; for updates we only restore keys
that still hold our value, so we never overwrite another user's later change. This is UndoManager and applicable() in undo.py.
""")

# 22. Nithillakrishi contribution ---------------------------------------------------------
contribution("N", [
    "ui/main_window.py    window, actions",
    "ui/chat.py           ChatPanel",
    "ui/participants.py   People, follow",
    "ui/history.py        time-lapse replay",
    "ui/network_panel.py  HUD, inspector, chaos",
    "ui/topbar.py         consistency badge",
    "ui/theme.py          light / dark theme",
], [
    ("Network you can see: ", "HUD, inspector, badge."),
    ("UI mirrors permissions; ", "server enforces them."),
    ("Toasts, not dialogs, ", "for non-blocking events."),
    ("Offline assets: ", "bundled fonts, painted icons."),
], shot="network_light.png", crop=(0.62, 0.05, 0.0, 0.0), shot_caption="Network tab: mission-control header, live HUD charts, inspector",
    note="""
Nithillakrishi, my section. I built the user interface and the collaboration features: the main window and its
actions, the chat and people panels, the time-lapse History tab, the Network tab with HUD, inspector and Chaos Lab
panel, the consistency badge and the theme system with light and dark modes. Everything is drawn locally: fonts are
bundled and icons are painted in code, so the UI works with no internet. The UI greys out what your role cannot do,
but the server is what enforces it.
""")

# 23. collaboration -----------------------------------------------------------------------
s = content_slide("Chat, people & classroom kit", "N")
picture(s, "chat.png", 0.6, 1.35, h=5.4, crop=(0.715, 0.05, 0.0, 0.0))
_p, (px, py, pw, ph) = picture(s, "people_roles.png", 4.55, 1.35, w=3.6)
bullets(s, 8.5, 1.45, 4.25, [
    ("Chat: ", "CHAT_SEND → server stamps sender, time, id → CHAT to all; tail sent on join."),
    ("People: ", "roles, online state, raised hands; owner menu: role, kick, follow."),
    ("Join / leave ", "notices as system chat lines and toasts."),
    ("Classroom kit: ", "laser pointer, follow the presenter (VIEWPORT), raise hand, \"look here\" ping."),
    ("Spotlight ", "dims everyone else's work; hover shows who drew what."),
], size=13, gap=8, h=5.2)
caption(s, 4.55, py + ph + 0.08, 3.6, "Owner, editors, viewers; Nithilla has raised a hand")
notes(s, """
Nithillakrishi. Chat goes through the server like everything else: the client sends CHAT_SEND with a client message id,
the server adds the sender's name, colour and timestamp and broadcasts CHAT, so everyone sees the same chronological
history; the recent tail is included in JOINED for newcomers. The People tab shows roles and online state. The owner
can change roles or kick from the context menu. The classroom kit adds a laser pointer, follow-the-presenter using
VIEWPORT messages, raised hands and a look-here ping; these are ephemeral, so they are never logged.
""")

# 24. time-lapse -----------------------------------------------------------------------------
s = content_slide("Time-lapse history & version restore", "N", "Replays the server's op log; the live board keeps syncing underneath")
picture(s, "history.png", 0.6, 1.6, w=8.2)
bullets(s, 9.1, 1.7, 3.65, [
    ("HISTORY_REQ → HISTORY ", "{base, ops} (multi-part if large)."),
    ("Scrubber ", "replays ops into a separate Board: read-only preview."),
    ("Play at 1×, 4×, 16×.", ""),
    ("Restore to here ", "submits a replace op (owner only)."),
    ("Durable: ", "survives server restarts."),
], size=13, gap=8, h=4.9)
notes(s, """
Nithillakrishi. Because the server keeps every op, history is almost free. The History tab requests the log, then
replays ops into a separate board object for the preview, so the real replica keeps receiving live ops while you scrub.
The canvas shows a read-only preview badge. Restore to here sends a replace op, which is itself a normal ordered op,
so everyone converges on the restored version, and it stays in the history too.
""")

# 25. network inspector ------------------------------------------------------------------------
s = content_slide("Network Inspector & live HUD", "N", "Every frame this client sends or receives, decoded")
picture(s, "network_dark.png", 0.6, 1.55, h=5.35, crop=(0.0, 0.0, 0.0, 0.0))
notes(s, """
Nithillakrishi. The Network tab is aimed at a Computer Networks evaluation. The header shows the TCP endpoint and the
current message and byte rates. The HUD has RTT, pending ops, total bytes and live charts of messages and bytes per
second, with a switch to turn live-stroke batching off and see the traffic jump. The inspector lists every frame with
time, direction, type, size and seq, and decodes the JSON payload of the selected frame. Chaos-affected frames are tagged.
""")
for i, (h, d) in enumerate([("Header", "TCP endpoint, msg/s, B/s"), ("Live HUD", "RTT, pending, totals, charts"),
                            ("Batching switch", "compare 30 Hz vs per-event"), ("Inspector", "time, dir, type, size, seq, chaos"),
                            ("Decoded JSON", "of the selected frame")]):
    y = 1.6 + i * 1.05
    box(s, 9.45, y, 3.28, 0.9, fill=CARD, radius=0.1, align=PP_ALIGN.LEFT,
        label=[[(h, {"bold": True, "size": 14, "color": "059669"})], [(d, {"size": 12, "color": TEXT2})]])

# 26. badge, toasts, dark mode ------------------------------------------------------------------
s = content_slide("Consistency badge, toasts & dark mode", "N")
picture(s, "dark.png", 0.6, 1.35, w=7.6)
_p, (px, py, pw, ph) = picture(s, "toast.png", 8.5, 1.35, w=4.23, crop=(0.17, 0.045, 0.5, 0.8))
caption(s, 8.5, py + ph + 0.08, 4.23, "Toasts stack without blocking work")
badges = [("In sync · seq N", GREEN, "last HASH matched"), ("Syncing · k pending", AMBER, "own ops not yet echoed"),
          ("Healing · resyncing", CYAN, "gap or mismatch being fixed"), ("Offline · edits queued", RED, "reconnecting")]
for i, (b, col, d) in enumerate(badges):
    y = 4.05 + i * 0.66
    box(s, 8.5, y, 2.2, 0.46, fill=col, radius=0.23, label=b, size=11, bold=True, color="FFFFFF")
    text(s, 10.85, y + 0.1, 1.9, 0.4, d, size=11, color=TEXT2)
caption(s, 0.6, 6.25, 7.6, "Dark mode: UI and board remap together; stickies and PDF pages keep their colours")
notes(s, """
Nithillakrishi. The badge in the top bar is driven by the controller's sync status: green means the last HASH
checkpoint matched our replica; amber means our own ops are waiting for their echo; blue means we are healing after a
gap or mismatch; red means offline with edits queued. Notifications such as joins, exports and reconnects are toasts,
so they never block drawing. Dark mode is a full theme: Qt stylesheet, painted icons and the board ink are remapped.
""")

# 27. testing -----------------------------------------------------------------------------------
s = content_slide("Testing", None, "pytest + pytest-asyncio, real server and real client in the loop")
stat(s, 0.6, 1.6, 2.9, "276", "tests, all passing (≈40 s)", big_size=40)
stat(s, 3.7, 1.6, 2.9, "14", "test modules: protocol → UI", big_size=40, color=VIOLET)
stat(s, 6.8, 1.6, 2.9, "6 bots", "random concurrent edits + random disconnects converge", big_size=32, color=GREEN)
stat(s, 9.9, 1.6, 2.83, "8 bots", "stress run: 1,771 ops, ALL REPLICAS CONVERGED", big_size=32, color=CYAN)
layers = [("Unit", "protocol framing & partial reads, model apply/validate, geometry, undo rules, auth"),
          ("Server", "room pipeline, permissions, rate limit, persistence crash cases, multi-part"),
          ("Integration", "bots over real TCP: late join, resume, RESYNC, HASH, kick, passwords"),
          ("Client", "NetClient state machine, controller ordering, canvas tools, UI smoke, end-to-end")]
for i, (h, d) in enumerate(layers):
    y = 3.3 + i * 0.75
    box(s, 0.6, y, 2.2, 0.62, fill=INDIGO, radius=0.08, label=h, size=13, bold=True, color="FFFFFF")
    text(s, 3.0, y + 0.17, 9.7, 0.4, d, size=13, color=TEXT2)
mono_box(s, 0.6, 6.35, 12.13, 0.5, ["$ python -m lanboard.tools.bot --port 47811 --room PERF8 --bots 8 --rate 20 --seconds 12   →   ALL REPLICAS CONVERGED"], size=11)
notes(s, """
Any member (suggest Yashvanth). 276 tests pass in about 40 seconds. They cover each layer: the codec including split
and merged reads, the model and undo rules, the server room pipeline and persistence crash cases, integration tests
with headless bots over real TCP, and client tests that drive the real NetClient, controller and widgets offscreen.
The convergence test runs six bots editing at random while randomly disconnecting and resuming, and asserts every
replica hashes to the server's board. The stress tool prints ALL REPLICAS CONVERGED at the end of each run.
""")

# 28. challenges -----------------------------------------------------------------------------------
s = content_slide("Challenges & lessons")
ch_rows = [("Half-open TCP", "A pulled cable sends no FIN: added PING + 12 s idle timeout on both sides."),
           ("Nagle + delayed ACK", "Small frames stalled up to ~200 ms: TCP_NODELAY on both ends."),
           ("Big boards", "A SNAPSHOT can exceed one frame: multi-part transfer, applied only when complete."),
           ("Undo vs. others", "Naive undo erased others' work: inverse ops filtered against the live board."),
           ("Qt + asyncio", "Avoided threads: QTcpSocket on the Qt loop, asyncio only in the server."),
           ("Demo networks", "Campus Wi-Fi isolates clients: demo on a hotspot or a switch.")]
for i, (h, d) in enumerate(ch_rows):
    c, r = i % 2, i // 2
    x, y = 0.6 + c * 6.12, 1.5 + r * 1.75
    box(s, x, y, 5.95, 1.55, fill=CARD, radius=0.14)
    text(s, x + 0.25, y + 0.2, 5.4, 0.4, h, size=17, bold=True, color=INDIGO)
    text(s, x + 0.25, y + 0.68, 5.4, 0.8, d, size=13, color=TEXT2)
notes(s, """
All members, one line each. The biggest lessons were networking ones: TCP does not tell you when a cable is pulled,
so a heartbeat and idle timeout are essential; Nagle's algorithm hurts small interactive frames; message framing and
multi-part transfers are needed because TCP has no boundaries and frames must be bounded. On the client, the hard part
was undo with several people, and keeping Qt single-threaded. For the demo, campus Wi-Fi often blocks client-to-client
traffic, so we bring a hotspot.
""")

# 29. demo plan -------------------------------------------------------------------------------------
s = content_slide("Live demo plan (internet off)")
steps = [("1", "Network", "Phone hotspot or switch; Wi-Fi internet off; note the server laptop's IP"),
         ("2", "Server", "lanboard-server --dashboard (allow the firewall prompt; Windows: Private network)"),
         ("3", "Join", "3 laptops: pick the server under Nearby servers, or type IP:5050 + room code"),
         ("4", "Draw", "pen, shapes, sticky votes, PDF slide, chat, roles, lock, password room"),
         ("5", "Break it", "Chaos Lab: latency, drops, duplicates; Cut the cable; keep drawing offline"),
         ("6", "Recover", "badge returns to In sync; History time-lapse; restart server, rejoin, board intact")]
for i, (n, h, d) in enumerate(steps):
    y = 1.45 + i * 0.88
    box(s, 0.6, y, 0.62, 0.62, fill=INDIGO if i < 4 else (RED if i == 4 else GREEN), shape=MSO_SHAPE.OVAL,
        label=n, size=16, bold=True, color="FFFFFF")
    text(s, 1.45, y + 0.1, 1.9, 0.45, h, size=17, bold=True)
    text(s, 3.3, y + 0.13, 9.4, 0.45, d, size=14, color=TEXT2)
box(s, 0.6, 6.7, 12.13, 0.3, fill=None, label=None)
text(s, 0.6, 6.72, 12.1, 0.3, "Fallback if discovery fails: type the IP shown by the server and the room code. Discovery is a convenience; TCP join always works.",
     size=12, color=MUTED)
notes(s, """
All members. The in-person demo runbook, detailed in PRESENTER_GUIDE.md. We use a hotspot or a switch with internet
disabled. Start the server with the dashboard and accept the firewall prompt. Three laptops join through discovery or
by typing the address. We show drawing and collaboration features, then break the network on purpose with the
Chaos Lab and the cut-the-cable button, then show recovery, the time-lapse, and a server restart with the board intact.
""")

# 30. thank you -------------------------------------------------------------------------------------
s = prs.slides.add_slide(BLANK)
SLIDE_NO[0] += 1
bg = box(s, 0, 0, SW, SH, fill=INDIGO, shape=MSO_SHAPE.RECTANGLE, name="Background")
gradient(bg, "1E1B4B", "4C1D95", angle=315)
text(s, 0.8, 2.2, 11.7, 1.2, "Thank you", size=60, bold=True, color="FFFFFF")
text(s, 0.8, 3.35, 11.7, 0.6, "Questions?", size=28, color="C7D2FE")
text(s, 0.8, 4.3, 11.7, 0.5, "One server-ordered op log  ·  framed TCP  ·  recovery you can watch", size=16, color="A5B4FC")
for i, k in enumerate("YJSN"):
    name, roll, area, col = TEAM[k]
    x = 0.8 + i * 3.0
    box(s, x, 5.5, 0.36, 0.36, fill=col, shape=MSO_SHAPE.OVAL)
    text(s, x + 0.5, 5.47, 2.4, 0.3, name, size=13, bold=True, color="FFFFFF")
    text(s, x + 0.5, 5.77, 2.4, 0.3, area, size=11, color="C7D2FE")
notes(s, """
Close and take questions. Each member answers questions on their own area; the likely viva questions and short
answers are in docs/PRESENTER_GUIDE.md.
""")

# ----------------------------------------------------------------------------- theme fonts + save
def set_theme_fonts(prs, latin=FONT):
    """Point the theme's major/minor fonts at Inter (PowerPoint falls back to its default if missing)."""
    master = prs.slide_masters[0]
    for rel in master.part.rels.values():
        if rel.reltype.endswith("/theme"):
            theme = rel.target_part
            root = etree.fromstring(theme.blob)
            ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            for tag in ("a:majorFont", "a:minorFont"):
                el = root.find(f".//{tag}/a:latin", ns)
                if el is not None:
                    el.set("typeface", latin)
            theme._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


set_theme_fonts(prs)
prs.core_properties.title = "LANBoard: CN Lab Project 2026"
prs.core_properties.author = "Yashvanth S, Jeevanandham T, Akula Sai Teja, Nithillakrishi P Y"
prs.save(OUT)
print("saved", OUT, len(prs.slides), "slides")
