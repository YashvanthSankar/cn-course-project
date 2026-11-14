#!/usr/bin/env python3
"""Build the Technical Documentation PDF from its markdown source.

    docs/TECHNICAL_DOCUMENTATION.md  ->  styled print HTML  ->  docs/LANBoard_Technical_Documentation.pdf

Build requirements
------------------
* Python 3.10 or newer with the ``markdown`` package. Keep it out of the
  project's ``.venv`` (the app does not need it), for example::

      uv venv /tmp/docvenv
      uv pip install --python /tmp/docvenv markdown pypdfium2
      /tmp/docvenv/bin/python docs/build/build_docs.py

* ``pypdfium2`` (optional): used to find the page of every heading, so the
  table of contents shows page numbers. Without it the TOC has no numbers.
* Google Chrome, run headless to print the HTML to PDF. The default is the
  macOS path; pass ``--chrome PATH`` or set ``$CHROME`` elsewhere.
* No network access is needed. Fonts: Inter and JetBrains Mono come from
  ``src/lanboard/client/assets/fonts``; the body face is Charter (bundled with
  macOS), falling back to Georgia / Times.

What the script does
--------------------
1. **Verifies the numbers in the document against the code** (unless
   ``--no-verify``): every ``MsgType`` appears in the message catalogue with
   the right code; every constant in Appendix A has the value it has in the
   source; the worked-example hex dump is exactly what
   ``lanboard.common.protocol.encode`` produces; the traffic figures in the
   performance section are recomputed. Only ``lanboard.common`` is imported
   (pure Python, no Qt).
2. Converts the markdown to HTML. Figures written as ``![caption](path)`` on a
   line of their own become numbered ``<figure>`` blocks; SVG diagrams are
   inlined (so they use the document fonts), screenshots are linked by file
   URL. ``[[TOC]]`` becomes the table of contents.
3. Prints the HTML to PDF with headless Chrome. If pypdfium2 is available it
   prints twice: the first pass locates each heading's page, the second puts
   those page numbers into the TOC (fixed-width column, so pagination does
   not change between the passes).

Usage::

    python docs/build/build_docs.py [--chrome PATH] [--html OUT.html] [--no-verify]
"""

from __future__ import annotations

import argparse
import ast
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent
ROOT = DOCS.parent
SRC = ROOT / "src"
SOURCE_MD = DOCS / "TECHNICAL_DOCUMENTATION.md"
STYLE = HERE / "doc_style.css"
OUT_PDF = DOCS / "LANBoard_Technical_Documentation.pdf"
FONTS = SRC / "lanboard" / "client" / "assets" / "fonts"
DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

FIG_LINE = re.compile(r'^!\[(?P<cap>.*)\]\((?P<src>[^)\s]+)(?:\s+"(?P<cls>[^"]*)")?\)\s*$')
HEADING = re.compile(r'<h(?P<lvl>[23]) id="(?P<id>[^"]+)">(?P<body>.*?)</h(?P=lvl)>', re.S)
SECNUM = re.compile(r"^((?:Appendix\s+)?[A-Z0-9]+(?:\.[0-9]+)*\.?)\s+(.*)$", re.S)


# =============================================================================
# 1. verification against the code
# =============================================================================

def hexdump(data: bytes) -> str:
    """Classic 16-bytes-per-line dump: offset, hex bytes, printable ASCII."""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hx = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:04x}  {hx:<47}  {asc}")
    return "\n".join(lines)


def worked_examples() -> dict[str, str]:
    """The frames shown in the 'Worked examples' section, computed by the real encoder."""
    from lanboard.common.messages import MsgType
    from lanboard.common.protocol import encode
    hello = encode(MsgType.HELLO, {"proto": 1, "app": "lanboard", "username": "alice",
                                   "client_id": "3f2a9c"})
    sub_payload = {"op_id": "3f2a9c:7", "op": {"k": "add", "obj": {
        "id": "u3-3f2a7", "type": "stroke", "z": 4, "style": {"color": "#e6194b", "width": 4},
        "geom": {"pts": [10, 20, 14, 26, 21, 30]}}}}
    sub = encode(MsgType.OP_SUBMIT, sub_payload)
    op_payload = {"seq": 42, "user_id": "u3", "op_id": "3f2a9c:7", "ts": 1762400000.123, "op": {
        "k": "add", "obj": {"id": "u3-3f2a7", "type": "stroke", "z": 4, "owner": "u3",
                            "style": {"color": "#e6194b", "width": 4},
                            "geom": {"pts": [10, 20, 14, 26, 21, 30]}}}}
    op = encode(MsgType.OP, op_payload)
    return {
        "hello_dump": hexdump(hello),
        "hello_len": str(len(hello)),
        "submit_header": hexdump(sub[:5]).split("  ")[1].strip(),
        "submit_json": sub[5:].decode("utf-8"),
        "submit_len": str(len(sub)),
        "op_header": hexdump(op[:5]).split("  ")[1].strip(),
        "op_len": str(len(op)),
    }


def traffic_numbers() -> dict[str, int]:
    """Recompute the traffic-minimisation figures of the performance section.

    A one-second pen stroke sampled at 120 Hz (121 points along a sine wave
    with a little hand jitter, fixed seed) is sent (a) as one STROKE_LIVE per
    mouse event and (b) batched at the 33 ms flush interval (4 events per
    frame); it is committed (c) raw and (d) after RDP simplification with the
    client's 0.6 px tolerance and 0.1 rounding. Cursor frames: 120 Hz vs the
    20 Hz throttle.
    """
    import json
    import math
    import random

    from lanboard.common import geometry as G
    from lanboard.common.messages import MsgType
    from lanboard.common.protocol import encode

    rng = random.Random(1)
    pts: list[float] = []
    for i in range(121):
        t = i / 120
        x, y = 200 + 400 * t, 300 + 80 * math.sin(t * 2 * math.pi)
        pts += [round(x + rng.uniform(-.3, .3), 2), round(y + rng.uniform(-.3, .3), 2)]
    base = {"id": "u3-3f2a7", "color": "#e6194b", "width": 4.0, "tool": "pen"}
    unbatched = [encode(MsgType.STROKE_LIVE, {**base, "pts": pts[i:i + 2]}) for i in range(0, len(pts), 2)]
    batched = [encode(MsgType.STROKE_LIVE, {**base, "pts": pts[i:i + 8]}) for i in range(0, len(pts), 8)]
    raw = {"op_id": "3f2a9c:7", "op": {"k": "add", "obj": {
        "id": "u3-3f2a7", "type": "stroke", "z": 4,
        "style": {"color": "#e6194b", "width": 4, "opacity": 1.0},
        "geom": {"pts": pts}, "off": [0, 0], "ts": 1762400000.1}}}
    simp = G.round_pts(G.rdp(pts, 0.6), 1)
    small = json.loads(json.dumps(raw))
    small["op"]["obj"]["geom"]["pts"] = simp
    cursor = len(encode(MsgType.CURSOR, {"x": 512.3, "y": 288.9}))
    return {
        "live_frames_unbatched": len(unbatched),
        "live_bytes_unbatched": sum(map(len, unbatched)),
        "live_frames_batched": len(batched),
        "live_bytes_batched": sum(map(len, batched)),
        "commit_points_raw": len(pts) // 2,
        "commit_bytes_raw": len(encode(MsgType.OP_SUBMIT, raw)),
        "commit_points_rdp": len(simp) // 2,
        "commit_bytes_rdp": len(encode(MsgType.OP_SUBMIT, small)),
        "cursor_frame": cursor,
        "cursor_bps_120": cursor * 120,
        "cursor_bps_20": cursor * 20,
    }


def source_constants(path: Path) -> dict[str, object]:
    """Module- and class-level ``NAME = <literal>`` assignments of a source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, object] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError, TypeError):
                continue
            out.setdefault(node.targets[0].id, value)
    return out


def verify(md: str) -> list[str]:
    """Return a list of mismatches between the document and the code (empty = all good)."""
    sys.path.insert(0, str(SRC))
    from lanboard.common import config as C
    from lanboard.common.messages import MsgType

    errors: list[str] = []

    # --- message catalogue: every type, right code, nothing invented ----------
    rows = dict((name, int(code, 16)) for code, name in
                re.findall(r"^\|\s*`0x([0-9A-F]{2})`\s*\|\s*`([A-Z_]+)`\s*\|", md, re.M))
    for m in MsgType:
        if m.name not in rows:
            errors.append(f"catalogue: {m.name} (0x{m.value:02X}) missing")
        elif rows[m.name] != m.value:
            errors.append(f"catalogue: {m.name} listed as 0x{rows[m.name]:02X}, code says 0x{m.value:02X}")
    for name in rows:
        if name not in MsgType.__members__:
            errors.append(f"catalogue: {name} is not a MsgType")

    # --- Appendix A constants -------------------------------------------------
    appendix = md.split("## Appendix A.", 1)[-1].split("\n## ", 1)[0]
    cache: dict[str, dict[str, object]] = {}
    pattern = re.compile(r"^\|\s*`([A-Z_][A-Z0-9_]*)`\s*\|\s*`([^`]+)`[^|]*\|\s*`([a-z_/]+\.py)`", re.M)
    found = 0
    for name, shown, rel in pattern.findall(appendix):
        found += 1
        try:
            shown_v = ast.literal_eval(shown)
        except (ValueError, SyntaxError):
            errors.append(f"appendix A: cannot parse value of {name}: {shown!r}")
            continue
        if rel == "common/config.py":
            actual = getattr(C, name, KeyError)
        else:
            path = SRC / "lanboard" / rel
            if rel not in cache:
                cache[rel] = source_constants(path) if path.exists() else {}
            actual = cache[rel].get(name, KeyError)
        if actual is KeyError:
            errors.append(f"appendix A: {name} not found in {rel}")
        elif actual != shown_v:
            errors.append(f"appendix A: {name} = {shown_v!r} in the doc, {actual!r} in {rel}")
    if found < 20:
        errors.append(f"appendix A: only {found} constant rows parsed (table format changed?)")

    # --- worked examples --------------------------------------------------------
    for key, text in worked_examples().items():
        if text not in md:
            errors.append(f"worked example: {key} does not match the encoder output:\n{text}")

    # --- traffic figures --------------------------------------------------------
    for key, value in traffic_numbers().items():
        if f"{value:,}" not in md:
            errors.append(f"performance: {key} = {value:,} not found in the document")
    return errors


# =============================================================================
# 2. markdown -> HTML
# =============================================================================

def inline_md(text: str) -> str:
    """Tiny inline formatter for captions: escape, then `code` and **bold**."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)


def svg_markup(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return re.sub(r"^<\?xml[^>]*>\s*", "", text)


def font_faces() -> str:
    faces = [("Inter", "Inter-Regular.ttf", 400), ("Inter", "Inter-Medium.ttf", 500),
             ("Inter", "Inter-SemiBold.ttf", 600), ("Inter", "Inter-Bold.ttf", 700),
             ("JetBrains Mono", "JetBrainsMono-Regular.ttf", 400)]
    css = []
    for family, fname, weight in faces:
        f = FONTS / fname
        if f.exists():
            css.append(f"@font-face {{ font-family: '{family}'; src: url('{f.as_uri()}'); "
                       f"font-weight: {weight}; font-style: normal; }}")
    return "\n".join(css)


def convert(md_text: str) -> tuple[str, list[dict]]:
    """Return (body HTML without TOC, heading list)."""
    import markdown

    figures: list[str] = []
    lines = []
    n_fig = 0
    for line in md_text.splitlines():
        m = FIG_LINE.match(line)
        if m:
            n_fig += 1
            src = (DOCS / m["src"]).resolve()
            if not src.exists():
                raise SystemExit(f"figure not found: {m['src']}")
            cls = f' class="{m["cls"]}"' if m["cls"] else ""
            media = svg_markup(src) if src.suffix == ".svg" else \
                f'<img src="{src.as_uri()}" alt="{html.escape(m["cap"])}">'
            figures.append(f'<figure id="fig-{n_fig}"{cls}>{media}<figcaption><b>Figure {n_fig}.</b> '
                           f'{inline_md(m["cap"])}</figcaption></figure>')
            lines.append(f"@@FIG{len(figures) - 1}@@")
        else:
            lines.append(line)
    md_text = "\n".join(lines)

    body = markdown.markdown(md_text, extensions=["extra", "toc", "sane_lists"],
                             extension_configs={"toc": {"permalink": False}}, output_format="html")
    for i, fig in enumerate(figures):
        body = body.replace(f"<p>@@FIG{i}@@</p>", fig)
    body = body.replace("@@LOGO@@", svg_markup(DOCS / "assets" / "diagrams" / "00_logo.svg"))
    hero = DOCS / "assets" / "screens" / "dark.png"
    body = body.replace("@@HERO@@", f'<img src="{hero.as_uri()}" alt="LANBoard main window">')

    headings = []

    def number(m: re.Match) -> str:
        lvl, hid, inner = m["lvl"], m["id"], m["body"].strip()
        sm = SECNUM.match(inner)
        num, title = (sm.group(1).rstrip("."), sm.group(2)) if sm else ("", inner)
        headings.append({"lvl": int(lvl), "id": hid, "num": num, "title": title})
        label = f'<span class="secnum">{num}</span>' if num else ""
        return f'<h{lvl} id="{hid}">{label}{title}</h{lvl}>'

    body = HEADING.sub(number, body)
    return body, headings


def toc_html(headings: list[dict], pages: dict[str, int] | None) -> str:
    items = ['<nav class="toc"><h2 id="contents">Contents</h2><ol>']
    for h in headings:
        if h["id"] == "contents":
            continue
        num = h["num"].replace("Appendix ", "")
        page = str(pages.get(h["id"], "")) if pages else ""
        title = re.sub(r"<[^>]+>", "", h["title"])
        items.append(f'<li class="lvl{h["lvl"]}"><a href="#{h["id"]}"><span class="num">{num}</span>'
                     f'<span class="title">{title}</span><span class="dots"></span>'
                     f'<span class="page">{page}</span></a></li>')
    items.append('</ol><span style="color:#fff;font-size:1px">TOCENDMARK</span></nav>')
    return "\n".join(items)


def page_html(body: str, toc: str) -> str:
    css = STYLE.read_text(encoding="utf-8")
    body = body.replace("<p>[[TOC]]</p>", toc)
    return (f'<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            f"<title>LANBoard: Technical Documentation</title>"
            f"<style>\n{font_faces()}\n{css}\n</style></head><body>\n{body}\n</body></html>\n")


# =============================================================================
# 3. HTML -> PDF
# =============================================================================

def print_pdf(chrome: str, html_path: Path, pdf_path: Path) -> None:
    cmd = [chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
           "--run-all-compositor-stages-before-draw", "--virtual-time-budget=15000",
           f"--print-to-pdf={pdf_path}", html_path.as_uri()]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if res.returncode != 0 or not pdf_path.exists():
        raise SystemExit(f"Chrome failed ({res.returncode}):\n{res.stderr[-2000:]}")


def heading_pages(pdf_path: Path, headings: list[dict]) -> dict[str, int] | None:
    """Find the (1-based) page of each heading by searching the PDF text in order."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        print("pypdfium2 not installed: the table of contents will have no page numbers")
        return None
    pdf = pdfium.PdfDocument(str(pdf_path))
    texts = []
    for i in range(len(pdf)):
        tp = pdf[i].get_textpage()
        texts.append(re.sub(r"\s+", "", tp.get_text_range()))
    pages: dict[str, int] = {}
    # skip the cover and the contents page(s): search after the page with the end marker
    start = next((i + 1 for i, t in enumerate(texts) if "TOCENDMARK" in t), 2)
    for h in headings:
        if h["id"] == "contents":
            continue
        title = re.sub(r"<[^>]+>", "", h["title"])
        key = re.sub(r"\s+", "", html.unescape(h["num"].replace("Appendix", "") + title))
        for p in range(start, len(texts)):
            if key and key in texts[p]:
                pages[h["id"]] = p + 1
                start = p
                break
    return pages


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--chrome", default=os.environ.get("CHROME", DEFAULT_CHROME))
    ap.add_argument("--html", type=Path, help="also keep the print HTML here")
    ap.add_argument("--no-verify", action="store_true", help="skip the checks against the code")
    ap.add_argument("--out", type=Path, default=OUT_PDF)
    args = ap.parse_args()

    md_text = SOURCE_MD.read_text(encoding="utf-8")
    if not args.no_verify:
        errors = verify(md_text)
        if errors:
            print("The document disagrees with the code:", file=sys.stderr)
            for e in errors:
                print("  -", e, file=sys.stderr)
            return 1
        print("verified: message catalogue, constants, worked examples, traffic figures")

    body, headings = convert(md_text)
    with tempfile.TemporaryDirectory(prefix="lanboard-doc-") as tmp:
        tmpd = Path(tmp)
        html_path, pdf_path = tmpd / "doc.html", tmpd / "doc.pdf"
        html_path.write_text(page_html(body, toc_html(headings, {})), encoding="utf-8")
        print_pdf(args.chrome, html_path, pdf_path)
        pages = heading_pages(pdf_path, headings)
        if pages:
            missing = [h["num"] + " " + h["title"] for h in headings
                       if h["id"] != "contents" and h["id"] not in pages]
            if missing:
                print("warning: page not found for:", "; ".join(missing))
            html_path.write_text(page_html(body, toc_html(headings, pages)), encoding="utf-8")
            print_pdf(args.chrome, html_path, pdf_path)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf_path, args.out)
        if args.html:
            shutil.copyfile(html_path, args.html)
    try:
        import pypdfium2 as pdfium
        n = len(pdfium.PdfDocument(str(args.out)))
        print(f"wrote {args.out} ({n} pages)")
    except ImportError:
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
