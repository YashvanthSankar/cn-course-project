# Explanation video script: Akula Sai Teja

- **Name:** Akula Sai Teja
- **Roll no.:** CS24I1054
- **Area:** Canvas and drawing tools (including undo and redo)
- **Spoken length:** 493 words, about 3 min 48 s at 130 words per minute (cue lines, this header and the viva list are not counted)
- **Slides covered (10-slide deck, `docs/scripts/gamma_10_slides.md`):** "Canvas and drawing tools", "Undo and redo with many people editing"
- **No live demo in the video.** Show the slides and the source code only. Keep your face and name visible.

Lines in `[brackets]` are cues for the screen and are not spoken. Everything else is spoken. Function names and line numbers were checked against the code on 2026-11-17. If the code moves, search for the function name. Change the wording to your own wherever you like, and add what you personally did or tested.

---

`[ON CAMERA: face and name visible]`

Hello, I am Akula Sai Teja, roll number CS24I1054. My part of the project is the canvas and the drawing tools, including undo and redo.

---

`[SLIDE: Canvas and drawing tools]`

The canvas is built on Qt's QGraphicsScene, which already gives us hit-testing, zoom, pan and export. The board is a set of objects, each with an id, a type, a style and a geometry.

The main rule is that every tool produces ops. A tool never changes the board directly. It builds an op and gives it to the controller, which applies it on screen at once and sends it to the server.

---

`[SHOW CODE: src/lanboard/client/canvas/tools.py → PenTool.press (line 122), PenTool.move (line 133), PenTool.release (line 188)]`

Here is the pen. On press, it asks the controller for a new object id. The id contains the user id and a counter, so it is unique without asking the server.

While the mouse moves, points are drawn locally and passed to send_live. The controller sends them as one STROKE_LIVE preview every 33 milliseconds. The preview is relayed but never stored.

On release, the tool builds one stroke object and calls add_object. So one stroke is one add op and one undo step.

---

`[SHOW CODE: src/lanboard/client/canvas/tools.py → PenTool._simplified (line 206); src/lanboard/common/geometry.py → rdp (line 80)]`

Before the commit, _simplified runs the Ramer–Douglas–Peucker algorithm. It keeps the end points and every point further than 0.6 screen pixels from the simplified line, then rounds to one decimal place. For our test stroke, 121 points become 26, and the op shrinks from 1,851 bytes to 499.

Smart Ink is a pen mode: if you hold the pen still for 450 milliseconds, recognize_shape can replace the stroke with a clean line, arrow, rectangle or ellipse.

---

`[SHOW CODE: src/lanboard/client/canvas/scene.py → CanvasScene.sync (line 158)]`

CanvasScene keeps one graphics item per object id. The controller reports only the ids that changed, and sync rebuilds just those items.

Images and PDF pages are uploaded once, by SHA-256 hash, in 192 KiB chunks, and the object carries only the hash. A whole PDF goes out as one batch op, so it is one undo step.

---

`[SLIDE: Undo and redo with many people editing]`

Undo is per user and only touches your own work. It does not rewind the board. It sends the inverse of your last action as a new op, and the server orders it like any other edit.

---

`[SHOW CODE: src/lanboard/common/undo.py → inverse (line 34), applicable (line 62), UndoManager.undo (line 121)]`

inverse gives the opposite op. Add becomes delete, delete becomes restore, and an update swaps its new and previous values. A batch is inverted part by part.

Then applicable checks the inverse against the current board. If someone already erased the object I added, undo skips to my next entry. For an update, it only reverts keys that still hold my value.

Asha moves a sticky note, seq 50. Ravi changes its colour, seq 51. Asha presses Undo. Her client sends an update that sets only the position back, and the server gives it seq 52. On Ravi's screen, the note moves back and keeps Ravi's colour.

A new action clears the redo stack, and clear is undoable too.

---

`[ON CAMERA]`

That is the canvas and the drawing tools. Next, Nithillakrishi P Y will explain the user interface and the collaboration features.

---

## If asked in the viva (not read aloud)

1. **What does undo do if someone else changed the same object?** `applicable` filters the inverse against the current board. For an update it reverts only the keys that still hold our value, so the other user's later change stays. If our object was already erased, the entry is skipped and the next one is tried.
2. **Why is undo a new op and not a rewind?** A rewind would need every client to roll back the shared log. An inverse op gets its own seq from the server like any other op, so every replica applies it in the same order and stays identical.
3. **Can I undo someone else's stroke?** No. Each client's `UndoManager` records only the ops that this user submitted, so another user's work is never on your stack.
4. **How do you keep stroke ops small?** Live points are batched every 33 ms and never stored. The committed stroke is simplified with RDP at 0.6 screen pixels, rounded to 0.1 and sent as a flat `[x, y, ...]` list. For the one-second test stroke, 121 points (1,851 bytes) become 26 points (499 bytes).
5. **How can a client create object ids without the server?** `new_id` combines the user id, which the server never reuses, a short tag from the client id and a local counter, for example `u3-3f2a7`. That is unique without a round trip.
6. **How are images and PDFs shared?** The bytes are uploaded once as a SHA-256 content-addressed blob in 192 KiB `BLOB_PUT` chunks, and the server skips a blob it already has. The image object only carries the hash, and other clients fetch the bytes with `BLOB_GET`. A PDF becomes one image per page, submitted as one batch.
7. **Why doesn't the whole canvas redraw on each op?** `Board.apply` returns the ids it changed and the controller emits only those, so `CanvasScene.sync` rebuilds just those `QGraphicsItem`s.
