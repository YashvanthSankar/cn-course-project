# Explanation video script: Nithillakrishi P Y

- **Name:** Nithillakrishi P Y
- **Roll no.:** CS24I1039
- **Area:** UI and collaboration features
- **Spoken length:** 498 words, about 3 min 50 s at 130 words per minute (cue lines, header and viva list excluded)
- **Slides covered:** "UI and collaboration features", then "Performance, testing and the live demo" (this closes the whole video)
- **Deck:** the 10-slide deck in `docs/LANBoard_Presentation.pptx`

Lines in `[brackets]` are screen cues and are not read aloud. Face and name stay visible the whole time. There is no live demo in the video.

---

[CAMERA: face and name visible. Slide "UI and collaboration features" on screen.]

Hello. I am Nithillakrishi P Y, roll number CS24I1039. My part is the UI and the collaboration features, and I will close the video.

[SLIDE: UI and collaboration features]

The window never touches the network socket or the board data. It only calls the session controller and listens for its signals, Qt's way of saying something changed.

[SHOW CODE: src/lanboard/client/ui/connect_dialog.py → ConnectDialog._launch_server (line 890), then ui/server_launcher.py → ServerProcess.start (line 196)]

The connect screen has two modes. In Join, Nearby servers are found by UDP discovery on the LAN, and recent rooms are remembered. Host mode starts a server on this laptop as a separate process, meaning a second program beside the app. Once its TCP port opens, the app connects.

[SHOW CODE: src/lanboard/client/ui/main_window.py → MainWindow._update_permissions (lines 744–765)]

This method greys out what your role cannot do. A viewer loses the drawing tools. Only the owner gets Lock, Room settings and People controls to change roles or kick someone. The server still checks every op, meaning every edit, so a modified client gets OP_REJECT.

[SHOW CODE: src/lanboard/server/room.py → Room.on_chat_send (line 711)]

For chat, the client sends CHAT_SEND, and here the server adds an id, the sender's name and a timestamp, then broadcasts CHAT to everyone. So every client shows the same order. Joins, leaves and role changes appear as system lines.

[SHOW CODE: src/lanboard/client/ui/history.py → HistoryTimeline.board_at (line 80), then HistoryPanel.restore_here (line 268)]

Time-lapse reuses the server's op log. HISTORY_REQ returns a starting board and every op since. HistoryTimeline replays them into a separate board, using the same Board.apply as each client's replica, its own copy. The canvas shows it read-only while the live board keeps syncing. Restore to here sends a normal replace op, accepted only from the owner.

[SLIDE: UI and collaboration features, point at "Network panel" and "Sync badge"]

The Network tab shows live numbers in a HUD: RTT, the round-trip time, pending ops, and bytes and messages per second. Its inspector lists every frame sent and received, decoded as JSON. The sync badge has five states. In sync means nothing is waiting. Syncing means our ops wait for the server's echo. Healing fixes a gap. Mismatch means a hash check failed, so a fresh snapshot is fetched. Offline counts queued edits.

[SHOW CODE: packaging/lanboard.spec, lines 30–32 (commit c2d68e8)]

One commit is from my own account: "Fix Windows executable Qt library conflict". On Windows, Qt uses the system's ICU library. With Python 3.14, our packaging tool PyInstaller could bundle a different icuuc.dll that Qt cannot use, and the client would not start. The fix drops that file from both builds, on Windows only.

[SLIDE: Performance, testing and the live demo]

To close. By our encoder's numbers, batching live strokes into groups saves about 63 percent of the bytes. Simplifying a finished stroke to fewer points saves 73 percent. Round-trip time on loopback, meaning the same machine, is under one millisecond. Eight bots made about 1,750 ops, and every replica converged to the same board. Our 318 automated tests pass on Windows, macOS and Linux.

For the live demo the internet is off. We start a phone hotspot, then the server or Host mode. Three laptops join from Nearby servers. We draw, chat, lock the board and use a password room. Then we cut the cable in the Chaos Lab and watch everyone return to In sync.

That is LANBoard. Thank you from all four of us.

[END]

---

## If asked in the viva (not read aloud)

1. **How is chat ordered and timestamped?** The server gives each message an id and a timestamp in `Room.on_chat_send`, then broadcasts CHAT to everyone, so every client shows the same order. Someone who joins later gets the last 200 messages in `JOINED.chat`.
2. **If I hack the client to enable the pen as a viewer, what happens?** The server refuses the op with OP_REJECT FORBIDDEN. The controller removes it from `pending` and rebuilds the view, so the local change disappears and a toast, a small pop-up message, says why.
3. **How does follow-the-presenter work?** The presenter's canvas sends VIEWPORT {x, y, w, h}, and the server passes it on only from the owner or the presenter the owner picked, so followers smoothly move their view to that rectangle. It is never logged, and if you pan yourself you stop following.
4. **How does time-lapse not break live sync?** The preview is a separate `Board` built from the HISTORY reply, while the confirmed replica keeps applying live OPs underneath. "Back to live" just gives the canvas its live board again.
5. **What does the green badge mean exactly?** `_emit_sync` reports "synced" only when the client is joined, has no pending ops, is not healing a gap and has no HASH mismatch. There is a HASH checkpoint every 25 ops, and if it does not match, the badge turns to Mismatch and a fresh snapshot is fetched.
6. **How does the Inspector cope with heavy traffic?** The slot, the function that receives the signal, only adds frames to a list, and a 100 ms timer moves them into the model in one batch, where a 2000-frame ring buffer drops the oldest ones. The "Hide cursor / live / ping" switch hides the very frequent presence and liveness frames.
7. **Why toasts instead of dialogs, and how does the UI work offline?** Joins, exports and reconnects should not interrupt drawing, so only real decisions open a modal dialog, one that blocks the window. Inter and JetBrains Mono are bundled and registered with `QFontDatabase`, and every icon is drawn with `QPainter`, so nothing is downloaded from the internet.
