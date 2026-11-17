// LANBoard 10-slide deck, light, assertion-evidence style:
// a headline that states one point, then the evidence (real diagram / screenshot)
// with paper-style figure callouts. The explanation itself is in the speaker notes.
const path = require("path");
const fs = require("fs");
const pptxgen = require("pptxgenjs");
const { applyTheme } = require(process.env.SKILL + "/scripts/apply_theme.js");

const SH = process.env.HOME + "/Desktop/LANBoard_screenshots/all/";
const HERE = __dirname;
const OUT = process.argv[2];
const NOTES = JSON.parse(fs.readFileSync(path.join(HERE, "notes.json"), "utf8"));

const INK = "1C1C1E", BODY = "3A3A3C", GREY = "8E8E93", HAIR = "D1D1D6", WHITE = "FFFFFF",
      BLUE = "1F57C3", BLUE_BG = "EEF3FC";
const HEAD = "Cambria", SANS = "Calibri", MONO = "Consolas";

const THEME = { name: "LANBoard Paper", headFontFace: HEAD, bodyFontFace: SANS,
  colors: { dk1: INK, lt1: WHITE, dk2: BODY, lt2: "F4F4F5", accent1: BLUE, accent2: "B45309", accent3: "15803D",
            accent4: "B91C1C", accent5: GREY, accent6: "0E7490", hlink: BLUE, folHlink: BODY } };

(async () => {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";                         // 13.333 x 7.5 in
  pres.theme = { headFontFace: HEAD, bodyFontFace: SANS };
  pres.title = "LANBoard: a LAN-only collaborative whiteboard";
  pres.author = "LANBoard team";
  const C = pres.SchemeColor;

  pres.defineSlideMaster({ title: "PAGE", background: { color: WHITE },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.45, w: 12.1, h: 1.0, fontFace: HEAD,
        fontSize: 30, bold: false, color: C.text1, align: "left", valign: "top", margin: 0 }, text: "" } },
      { image: { path: path.join(HERE, "logo.png"), x: 0.6, y: 6.98, w: 0.24, h: 0.24 } },
      { text: { text: "LANBoard  ·  CN Lab Project 2026", options: { x: 0.92, y: 6.98, w: 5, h: 0.24, fontFace: SANS, fontSize: 10, color: GREY, margin: 0, valign: "middle" } } },
    ],
    slideNumber: { x: 12.3, y: 6.98, w: 0.4, h: 0.24, fontFace: SANS, fontSize: 10, color: GREY, align: "right" } });

  const T = (s, t, o) => s.addText(t, { margin: 0, isTextBox: true, fontFace: SANS, color: BODY, ...o });
  const who = (s, name) => T(s, name, { x: 8.2, y: 6.98, w: 4.0, h: 0.24, fontSize: 10, color: GREY, align: "right", valign: "middle" });
  const line = (s, x1, y1, x2, y2, color = "6B6B70", width = 0.75) => {
    const flipH = x2 < x1, flipV = y2 < y1;
    s.addShape(pres.shapes.LINE, { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.max(Math.abs(x2 - x1), 0.001),
      h: Math.max(Math.abs(y2 - y1), 0.001), flipH, flipV, line: { color, width } });
  };
  const figure = (s, file, x, y, w, h) => { s.addImage({ path: file, x, y, w, h, line: { color: HAIR, width: 0.75 } }); return { x, y, w, h }; };
  // paper-style callout: a dot on the figure, a thin leader to a label in the side column
  function callout(s, img, fx, fy, lx, ly, label, lw = 3.3) {
    const px = img.x + img.w * fx, py = img.y + img.h * fy;
    s.addShape(pres.shapes.OVAL, { x: px - 0.06, y: py - 0.06, w: 0.12, h: 0.12, fill: { color: INK }, line: { color: WHITE, width: 1 } });
    line(s, px, py, lx - 0.08, ly + 0.16);
    T(s, label, { x: lx, y: ly, w: lw, h: 0.6, fontSize: 14, color: INK, valign: "top" });
  }
  function key(s, x, y, w, items) {
    T(s, "In this picture", { x, y, w, h: 0.3, fontSize: 12, color: GREY });
    items.forEach(([where, what], i) => {
      const yy = y + 0.45 + i * 0.86;
      s.addShape(pres.shapes.LINE, { x, y: yy - 0.08, w, h: 0, line: { color: HAIR, width: 0.75 } });
      T(s, where, { x, y: yy, w, h: 0.28, fontSize: 12, bold: true, color: INK });
      T(s, what, { x, y: yy + 0.29, w, h: 0.5, fontSize: 13, color: BODY, valign: "top" });
    });
  }
  const caption = (s, t, x, y, w) => T(s, t, { x, y, w, h: 0.3, fontSize: 11, italic: true, color: GREY });
  const notes = (s, n) => s.addNotes(NOTES[String(n)]);
  const title = (s, t) => s.addText(t, { placeholder: "title" });

  // 1 ── title ────────────────────────────────────────────────────────────────
  let s = pres.addSlide({ masterName: "PAGE" });
  s.addImage({ path: path.join(HERE, "logo.png"), x: 0.6, y: 0.75, w: 0.8, h: 0.8 });
  T(s, "LANBoard", { x: 0.6, y: 1.85, w: 5.4, h: 1.0, fontFace: HEAD, fontSize: 54, color: INK });
  T(s, "A collaborative whiteboard that runs on a local network, with no internet.", { x: 0.6, y: 2.95, w: 4.9, h: 0.9, fontSize: 18, color: BODY });
  T(s, "Computer Networks Lab · Project 2026", { x: 0.6, y: 3.95, w: 4.9, h: 0.3, fontSize: 13, color: GREY });
  const team = [["Yashvanth S", "CS24I1029", "Server core and state"], ["Jeevanandham T", "CS24I1035", "Protocol and networking"],
                ["Akula Sai Teja", "CS24I1054", "Canvas and drawing tools"], ["Nithillakrishi P Y", "CS24I1039", "UI and collaboration"]];
  s.addTable(team.map(([n, r, a]) => [{ text: n, options: { bold: true, color: INK } }, { text: r, options: { fontFace: MONO, color: GREY, fontSize: 11 } }, a]),
    { x: 0.6, y: 4.6, w: 5.2, colW: [1.75, 1.15, 2.3], fontFace: SANS, fontSize: 13, color: BODY, rowH: 0.42, valign: "middle",
      border: [{ type: "none" }, { type: "none" }, { type: "solid", pt: 0.75, color: HAIR }, { type: "none" }], margin: [0, 0.05, 0, 0] });
  figure(s, SH + "light.png", 6.3, 1.0, 6.43, 6.43 * 1800 / 2880);
  caption(s, "The client during a six-person session (light theme).", 6.3, 1.0 + 6.43 * 1800 / 2880 + 0.12, 6.43);
  notes(s, 1);

  // 2 ── the brief ─────────────────────────────────────────────────────────────
  s = pres.addSlide({ masterName: "PAGE" });
  title(s, "The problem and what we delivered");
  who(s, "Yashvanth S");
  T(s, "The brief asks for one shared canvas over a LAN. All five core modules are implemented.", { x: 0.6, y: 1.35, w: 12, h: 0.4, fontSize: 17 });
  const H = (t) => ({ text: t, options: { bold: true, color: INK, fontSize: 12 } });
  const rows2 = [[H("Core module"), H("What it means in LANBoard"), H("Where")],
    ["1  Multi-user drawing", "pen, eraser, colours; clear with permission; save and restore", "canvas/, server/room.py"],
    ["2  Sessions", "host or join with the LAN address and a room code; join/leave notices", "server/, ui/connect_dialog.py"],
    ["3  Real-time events", "one global order, duplicates dropped, gaps repaired, reconnects", "room.py, controller.py, net.py"],
    ["4  Group chat", "in order, with sender name and timestamp", "room.py, ui/chat.py"],
    ["5  State and recovery", "snapshot for late joiners, resume, per-user undo/redo", "persistence.py, common/undo.py"]];
  s.addTable(rows2.map((r, i) => i === 0 ? r : [{ text: r[0], options: { bold: true, color: INK } }, r[1], { text: r[2], options: { fontFace: MONO, fontSize: 11, color: GREY } }]),
    { x: 0.6, y: 2.05, w: 12.1, colW: [2.6, 6.2, 3.3], fontSize: 14, color: BODY, rowH: 0.56, valign: "middle",
      border: [{ type: "none" }, { type: "none" }, { type: "solid", pt: 0.75, color: HAIR }, { type: "none" }] });
  T(s, [{ text: "Beyond the brief: ", options: { bold: true, color: INK } },
        { text: "shapes and arrows, sticky notes, images and PDF pages, export, roles and lock, password rooms, time-lapse history, presentation mode, offline editing, LAN discovery, Chaos Lab." }],
    { x: 0.6, y: 5.75, w: 12.1, h: 0.7, fontSize: 14 });
  notes(s, 2);

  // 3 ── architecture ─────────────────────────────────────────────────────────
  s = pres.addSlide({ masterName: "PAGE" });
  title(s, "Architecture: one server, many clients, one LAN");
  who(s, "Yashvanth S");
  let img = figure(s, SH + "diagram_01_deployment.png", 0.6, 1.4, 8.6, 8.6 * 1412 / 2400);
  caption(s, "Fig. 1  Deployment. Solid lines are TCP, dashed lines are UDP discovery.", 0.6, img.y + img.h + 0.1, 8.6);
  T(s, "TCP 5050", { x: 9.75, y: 1.6, w: 3, h: 0.5, fontFace: HEAD, fontSize: 24, color: INK });
  T(s, "every drawing op, chat message and state update", { x: 9.75, y: 2.1, w: 2.95, h: 0.6, fontSize: 14 });
  T(s, "UDP 5051", { x: 9.75, y: 3.05, w: 3, h: 0.5, fontFace: HEAD, fontSize: 24, color: INK });
  T(s, "only to find servers; the address can be typed instead", { x: 9.75, y: 3.55, w: 2.95, h: 0.6, fontSize: 14 });
  T(s, "No internet", { x: 9.75, y: 4.5, w: 3, h: 0.5, fontFace: HEAD, fontSize: 24, color: INK });
  T(s, "a phone hotspot or a switch is enough", { x: 9.75, y: 5.0, w: 2.95, h: 0.6, fontSize: 14 });
  notes(s, 3);

  // 4 ── server core ──────────────────────────────────────────────────────────
  s = pres.addSlide({ masterName: "PAGE" });
  title(s, "Server core: one order for every edit");
  who(s, "Yashvanth S");
  T(s, "Room.submit, for every edit", { x: 0.6, y: 1.45, w: 3.4, h: 0.3, fontFace: MONO, fontSize: 12, color: GREY });
  const steps = ["Is the user in the room?", "Allowed by their role?", "Under 120 ops/s?", "Valid op?", "Seen this op_id before?", "seq = seq + 1, apply", "Append to the op log", "Broadcast to everyone"];
  steps.forEach((t, i) => {
    const y = 1.9 + i * 0.56, key = i >= 5;
    T(s, String(i + 1), { x: 0.6, y, w: 0.3, h: 0.4, fontSize: 14, color: key ? BLUE : GREY, bold: key });
    T(s, t, { x: 0.95, y, w: 3.2, h: 0.4, fontSize: 15, color: key ? INK : BODY, bold: key });
  });
  { const w = 6.6, h = w * 1836 / 2400; img = figure(s, SH + "diagram_05_op_ordering.png", 5.2, 1.35, w, h); }
  caption(s, "Fig. 2  Alice and Bob edit the same note. The server's order decides.", 5.2, img.y + img.h + 0.1, 6.6);
  notes(s, 4);

  // 5 ── protocol ─────────────────────────────────────────────────────────────
  s = pres.addSlide({ masterName: "PAGE" });
  title(s, "Protocol: our own message format over TCP");
  who(s, "Jeevanandham T");
  T(s, "TCP delivers a stream of bytes with no boundaries, so every message starts with its own length.", { x: 0.6, y: 1.35, w: 12, h: 0.4, fontSize: 17 });
  T(s, "The first message a client sends, exactly as it goes on the wire (73 bytes):", { x: 0.6, y: 2.15, w: 12, h: 0.35, fontSize: 14, color: GREY });
  const F = [{ x: 0.6, w: 3.0, t: "00 00 00 45", lab: "length, 4 bytes", note: "0x45 = 69 = 1 + 68" },
             { x: 3.75, w: 1.1, t: "01", lab: "type, 1 byte", note: "1 = HELLO" },
             { x: 5.0, w: 7.7, t: '{"proto":1,"app":"lanboard","username":"alice","client_id":"3f2a9c"}', lab: "payload, 68 bytes", note: "UTF-8 JSON" }];
  F.forEach((f, i) => {
    s.addShape(pres.shapes.RECTANGLE, { x: f.x, y: 2.65, w: f.w, h: 0.95, fill: { color: i === 1 ? BLUE_BG : WHITE }, line: { color: i === 1 ? BLUE : INK, width: 1 } });
    T(s, f.t, { x: f.x + 0.12, y: 2.65, w: f.w - 0.24, h: 0.95, fontFace: MONO, fontSize: i === 2 ? 13 : 24, color: INK, valign: "middle", align: i === 2 ? "left" : "center" });
    T(s, f.lab, { x: f.x, y: 3.72, w: f.w, h: 0.3, fontSize: 13, bold: true, color: i === 1 ? BLUE : INK });
    T(s, f.note, { x: f.x, y: 4.02, w: f.w, h: 0.3, fontFace: MONO, fontSize: 12, color: GREY });
  });
  s.addShape(pres.shapes.LINE, { x: 0.6, y: 4.75, w: 12.1, h: 0, line: { color: HAIR, width: 0.75 } });
  T(s, "Joining a room", { x: 0.6, y: 5.0, w: 3, h: 0.35, fontSize: 14, bold: true, color: INK });
  T(s, [{ text: "HELLO  →  CHALLENGE  →  JOIN  →  JOINED  →  " }, { text: "SNAPSHOT", options: { color: BLUE, bold: true } }, { text: "  →  live OPs" }],
    { x: 0.6, y: 5.42, w: 12.1, h: 0.4, fontFace: MONO, fontSize: 17, color: INK });
  T(s, "A newcomer receives the whole board before any live edit, so it never applies an op to a board it does not have.",
    { x: 0.6, y: 5.95, w: 12.1, h: 0.4, fontSize: 14 });
  notes(s, 5);

  // 6 ── reliability ──────────────────────────────────────────────────────────
  s = pres.addSlide({ masterName: "PAGE" });
  title(s, "Reliability and recovery");
  who(s, "Jeevanandham T");
  const rows6 = [[H("What goes wrong"), H("How the client notices"), H("What happens")],
    ["duplicate", "seq already applied", "dropped"], ["gap or delay", "seq skips ahead", "RESYNC after 400 ms"],
    ["replicas drift", "hash check every 25 ops", "fresh snapshot"], ["pulled cable", "12 s of silence", "reconnect, resume"],
    ["offline edits", "ops still pending", "resent, server de-dups"], ["server crash", "server restarts", "log + snapshot replay"]];
  s.addTable(rows6.map((r, i) => i === 0 ? r : [{ text: r[0], options: { bold: true, color: INK } }, r[1], { text: r[2], options: { color: INK } }]),
    { x: 0.6, y: 1.5, w: 6.0, colW: [1.65, 2.2, 2.15], fontSize: 13, color: BODY, rowH: 0.56, valign: "middle",
      border: [{ type: "none" }, { type: "none" }, { type: "solid", pt: 0.75, color: HAIR }, { type: "none" }] });
  { const w = 5.75, h = w * 1659 / 2400; img = figure(s, SH + "diagram_06_gap_and_hash.png", 6.95, 1.5, w, h); }
  caption(s, "Fig. 3  Left: a missing op is repaired with RESYNC. Right: a hash mismatch heals with a snapshot.", 6.95, img.y + img.h + 0.1, 5.75);
  notes(s, 6);

  // 7 ── canvas ───────────────────────────────────────────────────────────────
  s = pres.addSlide({ masterName: "PAGE" });
  title(s, "Canvas and drawing tools");
  who(s, "Akula Sai Teja");
  { const h = 5.1, w = h * 2073 / 1634; img = figure(s, path.join(HERE, "light_canvas.png"), 0.6, 1.45, w, h); }
  caption(s, "Fig. 4  The shared canvas during a session.", 0.6, img.y + img.h + 0.1, 6.4);
  key(s, 8.3, 1.45, 4.4, [
    ["Left edge", "The tool palette. Every tool builds an op; the canvas never edits the board itself."],
    ["Bottom left", "A live stroke in progress, previewed to everyone every 33 ms."],
    ["Middle", "Shapes, text and sticky notes: each is an object with its own id."],
    ["Right", "An imported PDF page, uploaded once and shared by its SHA-256 hash."]]);
  T(s, [{ text: "121 → 26 ", options: { fontFace: HEAD, fontSize: 22, color: INK } }, { text: "points in a test stroke after RDP simplification", options: { fontSize: 13 } }],
    { x: 8.3, y: 5.55, w: 4.4, h: 0.8, valign: "top" });
  notes(s, 7);

  // 8 ── undo ─────────────────────────────────────────────────────────────────
  s = pres.addSlide({ masterName: "PAGE" });
  title(s, "Undo and redo with many people editing");
  who(s, "Akula Sai Teja");
  T(s, "Undo is per user. It does not rewind the board: it sends the inverse of your own last action as a new op.", { x: 0.6, y: 1.35, w: 12, h: 0.7, fontSize: 17 });
  T(s, "The server's op log for one sticky note", { x: 0.6, y: 2.35, w: 6, h: 0.3, fontSize: 13, color: GREY });
  const log = [["seq 50", "Asha moves the note", "position: A → B"], ["seq 51", "Ravi recolours it", "colour: yellow → red"], ["seq 52", "Asha presses Undo", "position: B → A  (the inverse)"]];
  log.forEach(([q, a, d], i) => {
    const x = 0.6 + i * 4.1, y = 2.75, hi = i === 2;
    s.addShape(pres.shapes.RECTANGLE, { x, y, w: 3.8, h: 1.45, fill: { color: hi ? BLUE_BG : WHITE }, line: { color: hi ? BLUE : INK, width: 1 } });
    T(s, q, { x: x + 0.22, y: y + 0.18, w: 3.4, h: 0.32, fontFace: MONO, fontSize: 13, color: hi ? BLUE : GREY, bold: hi });
    T(s, a, { x: x + 0.22, y: y + 0.52, w: 3.4, h: 0.4, fontSize: 18, color: INK, bold: true });
    T(s, d, { x: x + 0.22, y: y + 0.95, w: 3.4, h: 0.35, fontFace: MONO, fontSize: 12, color: BODY });
    if (i < 2) T(s, "→", { x: x + 3.8, y, w: 0.3, h: 1.45, fontSize: 18, color: GREY, align: "center", valign: "middle" });
  });
  T(s, "On every screen the note moves back to A and stays red. Asha's undo only touched the key she changed.",
    { x: 0.6, y: 4.6, w: 12.1, h: 0.45, fontSize: 17, color: INK });
  T(s, "Other rules: if someone erased the object, the undo step is skipped. A batch, such as moving a selection, is one step. Clear can be undone.",
    { x: 0.6, y: 5.25, w: 12.1, h: 0.7, fontSize: 14 });
  notes(s, 8);

  // 9 ── UI ───────────────────────────────────────────────────────────────────
  s = pres.addSlide({ masterName: "PAGE" });
  title(s, "UI and collaboration features");
  who(s, "Nithillakrishi P Y");
  { const w = 7.85, h = w * 1800 / 2880; img = figure(s, SH + "chat.png", 0.6, 1.45, w, h); }
  caption(s, "Fig. 5  The main window with the chat panel open.", 0.6, img.y + img.h + 0.1, 7.85);
  key(s, 8.85, 1.45, 3.85, [
    ["Top bar", "Room name, the sync badge, who is here, and the owner's lock and present buttons."],
    ["Right panel", "People, chat, time-lapse history and the network inspector."],
    ["Chat", "Messages in server order, with each sender's name and time."],
    ["Sync badge", "In sync, syncing, healing, mismatch or offline."]]);
  notes(s, 9);

  // 10 ── close ───────────────────────────────────────────────────────────────
  s = pres.addSlide({ masterName: "PAGE" });
  title(s, "Performance, testing and the live demo");
  who(s, "Nithillakrishi P Y");
  const nums = [["318", "automated tests, passing on Windows, macOS and Linux"], ["63%", "fewer bytes for live strokes, by batching"],
                ["73%", "smaller committed strokes after RDP"], ["< 1 ms", "round trip to the server on loopback"]];
  nums.forEach(([b, t], i) => {
    const x = 0.6 + i * 3.08;
    T(s, b, { x, y: 1.55, w: 2.9, h: 0.9, fontFace: HEAD, fontSize: 44, color: i === 0 ? BLUE : INK });
    T(s, t, { x, y: 2.5, w: 2.75, h: 0.7, fontSize: 14 });
  });
  s.addShape(pres.shapes.LINE, { x: 0.6, y: 3.55, w: 12.1, h: 0, line: { color: HAIR, width: 0.75 } });
  T(s, "The live demo, with the internet switched off", { x: 0.6, y: 3.8, w: 8, h: 0.35, fontSize: 14, bold: true, color: INK });
  const demo = ["Start a phone hotspot", "Start the server, or Host mode", "Three laptops join from Nearby servers", "Draw, chat, lock, password room", "Cut the cable in Chaos Lab", "Everyone is back in sync"];
  demo.forEach((t, i) => {
    const col = i % 3, row = Math.floor(i / 3), x = 0.6 + col * 4.05, y = 4.35 + row * 0.62;
    T(s, String(i + 1), { x, y, w: 0.3, h: 0.4, fontSize: 15, color: BLUE, bold: true });
    T(s, t, { x: x + 0.35, y, w: 3.6, h: 0.4, fontSize: 15, color: INK });
  });
  T(s, "Thank you. Questions?", { x: 0.6, y: 5.85, w: 12.1, h: 0.75, fontFace: HEAD, fontSize: 30, color: INK });
  notes(s, 10);

  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("wrote", OUT);
})();
