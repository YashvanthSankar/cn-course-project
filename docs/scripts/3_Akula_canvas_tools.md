# Explanation video script: Akula Sai Teja

- **Name:** Akula Sai Teja
- **Roll no.:** CS24I1054
- **Area:** Canvas and drawing tools (including undo and redo)
- **Spoken length:** 499 words, about 3 min 50 s at 130 words per minute (cue lines, this header and the viva list are not counted)
- **Slides covered (10-slide deck, `docs/LANBoard_Presentation.pptx`):** "Canvas and drawing tools", "Undo and redo with many people editing"
- **No live demo in the video.** Show the slides and the source code only. Keep your face and name visible.

Lines in `[brackets]` are cues for the screen and are not spoken. Everything else is spoken. Function names and line numbers were checked against the code on 2026-11-17. If the code moves, search for the function name. Change the wording to your own wherever you like, and add what you personally did or tested.

---

`[ON CAMERA: face and name visible]`

Hello, I am Akula Sai Teja, roll number CS24I1054. My part is the canvas and the drawing tools, including undo and redo.

---

`[SLIDE: Canvas and drawing tools]`

The canvas uses QGraphicsScene, Qt's ready-made drawing surface. It already handles zoom, pan, export and hit-testing, or finding what you clicked. The board is a set of objects, each with an id, type, style and geometry, meaning its position and shape.

Every tool produces ops, and an op is one small edit message. A tool never changes the board directly. The controller shows the op at once and sends it to the server.

---

`[SHOW CODE: src/lanboard/client/canvas/tools.py → PenTool.press (line 122), PenTool.move (line 133), PenTool.release (line 188)]`

On press, the pen asks the controller for a new object id. The id joins the user id and a counter, so it is unique without asking the server.

While you draw, points appear on your screen and go to send_live. Every 33 milliseconds, the controller sends them as one STROKE_LIVE preview, which the server passes on but never saves.

On release, it builds one stroke object and calls add_object, so one stroke is one add op and one undo step.

---

`[SHOW CODE: src/lanboard/client/canvas/tools.py → PenTool._simplified (line 206); src/lanboard/common/geometry.py → rdp (line 80)]`

Before saving, _simplified runs the Ramer-Douglas-Peucker algorithm, which removes points that hardly change the shape. It keeps the end points and any point over 0.6 screen pixels from the simplified line, then rounds to one decimal. In our test stroke, 121 points become 26, and the op drops from 1,851 bytes to 499.

Smart Ink is a pen mode. Hold the pen still for 450 milliseconds and recognize_shape can replace the stroke with a clean line, arrow, rectangle or ellipse.

---

`[SHOW CODE: src/lanboard/client/canvas/scene.py → CanvasScene.sync (line 158)]`

CanvasScene keeps one graphics item, the thing Qt draws, per object id. The controller reports only the changed ids, and sync rebuilds just those.

Images and PDF pages are uploaded once, in 192 KiB pieces, named by their SHA-256 hash, like a fingerprint. The board object carries only the hash. A whole PDF is one batch op, so it is one undo step.

---

`[SLIDE: Undo and redo with many people editing]`

Undo is per user and only touches your own work, without rewinding the board. It sends the inverse, meaning the opposite, of your last action as a new op, which the server orders like any other edit.

---

`[SHOW CODE: src/lanboard/common/undo.py → inverse (line 34), applicable (line 62), UndoManager.undo (line 121)]`

inverse gives the opposite op. Add becomes delete, delete becomes restore, and an update swaps its new and previous values. A batch is reversed part by part.

applicable then checks the inverse against the current board. If someone already erased the object I added, undo skips to my next entry. For an update, it only changes back keys that still hold my value.

Asha moves a sticky note. That edit gets sequence number, or seq, 50. Ravi changes its colour, seq 51. Asha presses Undo. Her client sends an update setting only the position back, which gets seq 52. On Ravi's screen, the note moves back but keeps Ravi's colour.

A new action clears the redo stack, and Clear can be undone too.

---

`[ON CAMERA]`

That is the canvas and drawing tools. Next, Nithillakrishi P Y will explain the user interface and the collaboration features.

---

## If asked in the viva (not read aloud)

1. **What does undo do if someone else changed the same object?** `applicable` checks the inverse against the current board, and for an update it only changes back the keys that still hold our value, so the other person's later change stays. If our object was already erased, that entry is skipped and the next one is tried.
2. **Why is undo a new op and not a rewind?** A rewind would make every client roll back the shared log. An inverse op gets its own seq from the server like any other op, so every copy of the board applies it in the same order and stays the same.
3. **Can I undo someone else's stroke?** No. Each client's `UndoManager` only records the ops that this user sent, so another user's work is never on your stack.
4. **How do you keep stroke ops small?** Live points are sent together every 33 ms and never stored. The final stroke is simplified with RDP at 0.6 screen pixels, rounded to 0.1 and sent as a flat `[x, y, ...]` list, so the one-second test stroke goes from 121 points (1,851 bytes) to 26 points (499 bytes).
5. **How can a client create object ids without the server?** `new_id` joins the user id, which the server never reuses, a short tag from the client id and a local counter, for example `u3-3f2a7`. So it is unique without waiting for a reply from the server.
6. **How are images and PDFs shared?** The bytes are uploaded once, named by their SHA-256 hash, in 192 KiB `BLOB_PUT` chunks, and the server skips a file it already has. The image object only carries the hash, other clients fetch the bytes with `BLOB_GET`, and a PDF becomes one image per page sent as one batch.
7. **Why doesn't the whole canvas redraw on each op?** `Board.apply` returns the ids it changed and the controller passes on only those. So `CanvasScene.sync` rebuilds just those `QGraphicsItem`s.
