# Explanation video script: Nithillakrishi P Y

- **Name:** Nithillakrishi P Y
- **Roll no.:** CS24I1039
- **Area:** UI and collaboration features
- **Spoken length:** 494 words, about 3 min 48 s at 130 words per minute (cue lines, header and viva list excluded)
- **Slides covered:** "UI and collaboration features", then "Performance, testing and the live demo" (this closes the whole video)
- **Deck:** the 10-slide deck in `docs/scripts/gamma_10_slides.md`

Lines in `[brackets]` are screen cues and are not read aloud. Face and name stay visible the whole time. There is no live demo in the video.

---

[CAMERA: face and name visible. Slide "UI and collaboration features" on screen.]

Hello. I am Nithillakrishi P Y, roll number CS24I1039. My part of the project is the UI and the collaboration features. I will also close the video for the team.

[SLIDE: UI and collaboration features]

The window never touches the socket or the board model. It only calls the session controller and reacts to its signals.

[SHOW CODE: src/lanboard/client/ui/connect_dialog.py → ConnectDialog._launch_server (line 890), then ui/server_launcher.py → ServerProcess.start (line 196)]

The connect screen has two modes. In Join, Nearby servers comes from UDP discovery, and recent rooms are remembered. Host mode can start a server on this laptop. It runs the server as a separate process, waits until the TCP port accepts, then connects to it.

[SHOW CODE: src/lanboard/client/ui/main_window.py → MainWindow._update_permissions (lines 744–765)]

This method greys out what your role cannot do. A viewer loses the drawing tools. Only the owner gets Lock and Room settings, and in People the owner can make someone an editor or viewer, or kick them. The server still checks every op, so a modified client gets OP_REJECT.

[SHOW CODE: src/lanboard/server/room.py → Room.on_chat_send (line 711)]

Chat goes through the server. The client sends CHAT_SEND. Here the server adds an id, the sender's name and a timestamp, then broadcasts CHAT to everyone. So every client shows the same order. Joins, leaves and role changes arrive as system lines.

[SHOW CODE: src/lanboard/client/ui/history.py → HistoryTimeline.board_at (line 80), then HistoryPanel.restore_here (line 268)]

Time-lapse reuses the server's op log. HISTORY_REQ returns a base board and every op since. HistoryTimeline replays them with the same Board.apply the replicas use, into a separate board. The canvas shows it read-only while the live board keeps syncing. Restore to here sends a normal replace op, accepted only from the owner.

[SLIDE: UI and collaboration features, point at "Network panel" and "Sync badge"]

The Network tab has a live HUD with RTT, pending ops, bytes and messages per second. Its inspector lists every frame sent and received, with the decoded JSON. The sync badge shows the controller's state. In sync means nothing is pending. Syncing means our ops await their echo. Healing repairs a gap. Mismatch means a hash check failed, so a fresh snapshot is fetched. Offline counts queued edits.

[SHOW CODE: packaging/lanboard.spec, lines 30–32 (commit c2d68e8)]

One commit is from my own account: "Fix Windows executable Qt library conflict". Qt on Windows uses the ICU library that ships with Windows. Building with Python 3.14, PyInstaller could bundle a different icuuc.dll that Qt cannot use, and the client would not start. The fix drops that file from both builds, on Windows only.

[SLIDE: Performance, testing and the live demo]

To close. By our encoder's numbers, batching live strokes saves about 63 percent of the bytes, and simplifying a finished stroke saves 73 percent. Round-trip time on loopback is under one millisecond. Eight bots made about 1,750 ops and every replica converged. Our 318 automated tests pass on Windows, macOS and Linux.

For the live demo the internet is off. We start a phone hotspot, then start the server, or use Host mode in the app. Three laptops join from Nearby servers. We draw, chat, lock the board and use a password room. Then we cut the cable in the Chaos Lab and watch everyone return to In sync.

That is LANBoard. Thank you from all four of us.

[END]

---

## If asked in the viva (not read aloud)

1. **How is chat ordered and timestamped?** The server gives each message an id and a timestamp in `Room.on_chat_send` and broadcasts CHAT to everyone, so every client shows the same order. A newcomer gets the last 200 messages in `JOINED.chat`.
2. **If I hack the client to enable the pen as a viewer, what happens?** The server rejects the op with OP_REJECT FORBIDDEN. The controller drops it from `pending` and rebuilds the view, so the local change disappears and a toast says why.
3. **How does follow-the-presenter work?** The presenter's canvas sends VIEWPORT {x, y, w, h}. The server relays it only from the owner or the presenter the owner picked, and followers animate their view to that rectangle. It is never logged, and panning yourself stops following.
4. **How does time-lapse not break live sync?** The preview is a separate `Board` built from the HISTORY payload. The confirmed replica keeps applying live OPs underneath, and "Back to live" just hands the canvas its live board again.
5. **What does the green badge mean exactly?** `_emit_sync` reports "synced" only when the client is joined, has no pending ops, no gap being healed and no HASH mismatch. A HASH checkpoint every 25 ops that does not match turns it to Mismatch and triggers a fresh snapshot.
6. **How does the Inspector cope with heavy traffic?** The slot only appends to a list; a 100 ms timer moves frames into the model in one batch, and the model keeps a 2000-frame ring buffer. The "Hide cursor / live / ping" switch filters out the high-rate presence and liveness frames.
7. **Why toasts instead of dialogs, and how does the UI work offline?** Joins, exports and reconnects should not interrupt drawing, so only decisions are modal. Inter and JetBrains Mono are bundled and registered with `QFontDatabase`, and every icon is painted with `QPainter`, so nothing is fetched from the internet.
