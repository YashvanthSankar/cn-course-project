# CLIENT_API — internal contracts between client modules

This is the contract the client modules are written against. Change it only with care, because every module depends on it.
The wire protocol is in `src/lanboard/common/messages.py` (docstring = authoritative message catalogue).

```
NetClient (client/net.py)  ──message(int,dict)──▶  SessionController (client/controller.py)
      ▲  send(type, payload)                          │  signals (board, chat, presence…)
      └───────────────────────────────────────────────┤
                                                       ▼
                         CanvasView (client/canvas/view.py)    MainWindow & panels (client/ui/*)
```

## NetClient (`client/net.py`): transport, handshake, reconnect, chaos, metrics
```python
class NetClient(QObject):
    state_changed = Signal(str)       # "idle" | "connecting" | "authenticating" | "joined" | "reconnecting" | "closed"
    joined = Signal(dict)             # JOINED payload (also emitted after a successful resume, resumed=True)
    message = Signal(int, dict)       # every other inbound frame, AFTER the chaos injector
    error = Signal(str, str)          # (code, msg); fatal codes stop auto-reconnect
    password_required = Signal(str, bool)  # (room_code, wrong_previous_attempt) -> UI asks, then provide_password()
    rtt_changed = Signal(float)       # ms, per PONG
    frame_logged = Signal(dict)       # {t, dir:"in"|"out", type:int, name:str, size:int, payload:dict, chaos:str|None}
    stats = Signal(dict)              # 1 Hz: {msgs_in, msgs_out, bytes_in, bytes_out (per second), rtt_ms, rtt_hist:list[float], total_in, total_out}

    last_seq_provider: Callable[[], int]      # set by controller, used when resuming
    batching_enabled: bool = True             # controller reads it for live-stroke batching
    chaos: FaultInjector                      # latency_ms, jitter_ms, dup_pct, drop_pct, enabled, cut_link()
    user_id: str | None; username: str; room_code: str; resume_token: str | None; host: str; port: int

    def connect_to(self, host, port, username, room, create=False, password=None, room_name=None) -> None
    def provide_password(self, password: str) -> None
    def send(self, t: MsgType, payload: dict) -> bool   # False when not joined (caller decides to queue)
    def is_joined(self) -> bool
    def close(self) -> None                             # sends LEAVE, no auto-reconnect
```
* The handshake is handled internally: HELLO, then CHALLENGE, then JOIN (with an auth retry on AUTH_REQUIRED/BAD_PASSWORD), then JOINED.
* On socket loss it auto-reconnects with backoff and jitter, and sends JOIN with `resume_token` and `last_seq_provider()`.
* PING runs every `PING_INTERVAL` and RTT comes from PONG. `TCP_NODELAY` is set through `LowDelayOption`.
* The chaos injector only affects **inbound** frames before `message` is emitted. Drop applies to OP frames only. Duplicate re-emits. Latency and jitter delay with a QTimer, so jitter can reorder frames. `cut_link()` aborts the socket, which triggers a reconnect.

`DiscoveryBrowser` (`client/discovery.py`) has `servers_changed = Signal(list)` with entries `{server_id, name, host, port, proto, rooms:[{code,name,users,protected,locked}], last_seen}`, plus `start()`, `stop()` and `probe()`. It uses a UDP broadcast to `DISCOVERY_PORT` with `{"magic": DISCOVERY_MAGIC}`.

## SessionController (`client/controller.py`): replica, sync rules, undo, presence
```python
class SessionController(QObject):
    reset = Signal()                        # view board replaced wholesale: re-render everything
    objects_changed = Signal(object)        # set[str] of object ids changed on the view board
    participants_changed = Signal()
    chat_added = Signal(dict); chat_reset = Signal(list)
    room_changed = Signal(dict)             # RoomInfo
    me_changed = Signal(dict)               # my Participant (role, hand...)
    cursor_moved = Signal(str, float, float)        # user_id, x, y (scene coords)
    live_stroke = Signal(str, dict)         # user_id, {id, color, width, pts(new points only), tool}
    live_stroke_end = Signal(str, str)      # user_id, stroke id
    laser = Signal(str, list)               # user_id, flat pts
    viewport = Signal(str, dict)            # user_id, {x, y, w, h}
    signal_received = Signal(str, str)      # user_id, kind ("hand_up","hand_down","ping:x,y")
    sync_status = Signal(dict)              # {state:"synced"|"pending"|"healing"|"offline"|"mismatch", seq, hash, pending}
    undo_state = Signal(bool, bool)         # can_undo, can_redo
    history_ready = Signal(dict)            # HISTORY payload
    saves_ready = Signal(list)
    blob_ready = Signal(str)                # sha256 hex; bytes available via blob_bytes()
    blob_progress = Signal(str, int, int)   # hash, chunks_done, chunks_total
    notice = Signal(str, str)               # (level "info"|"warn"|"error", text) for status bar / toasts
    connection_state = Signal(str)          # mirrors NetClient.state_changed

    net: NetClient
    board: Board            # confirmed replica (server order only)
    view: Board             # board + optimistic pending ops; RENDER THIS
    participants: dict[str, dict]; me: dict | None; room: dict | None; chat: list[dict]

    def color_of(self, user_id) -> str; def name_of(self, user_id) -> str
    def can_edit(self) -> bool; def is_owner(self) -> bool; def can_clear(self) -> bool

    def new_id(self) -> str                                 # "<user_id>-<n>"
    def next_z(self) -> int
    def submit(self, op: dict, undoable: bool = True) -> str | None   # returns op_id
    def add_object(self, obj: dict) -> None                 # fills id/z/owner if missing
    def update_object(self, oid: str, changes: dict) -> None   # computes prev from view
    def update_many(self, changes: dict[str, dict]) -> None     # one batch op, one undo step
    def delete_objects(self, ids: list[str]) -> None
    def clear_board(self) -> None
    def undo(self) -> None; def redo(self) -> None

    def send_cursor(self, x, y) -> None                     # throttled to 20 Hz
    def send_live(self, stroke_id, color, width, new_pts: list, tool="pen") -> None  # batched ~30 Hz
    def end_live(self, stroke_id) -> None
    def send_laser(self, pts: list) -> None
    def send_viewport(self, rect: dict) -> None
    def send_signal(self, kind: str) -> None
    def send_chat(self, text: str) -> None
    def set_role(self, user_id, role); def kick(self, user_id)
    def set_room(self, **settings)                          # locked, name, editors_can_clear, password(str|None), presenter
    def save(self, name); def list_saves(self); def load(self, name); def request_history(self)
    def put_blob(self, data: bytes, mime: str) -> str       # uploads (chunked) and returns sha256 hex
    def blob_bytes(self, h: str) -> bytes | None            # local cache, None => requested, blob_ready fires later
```

## CanvasView (`client/canvas/view.py`): rendering & tools
```python
class CanvasView(QGraphicsView):
    tool_changed = Signal(str); zoom_changed = Signal(float); hover_info = Signal(str)
    def __init__(self, controller: SessionController, parent=None)
    def set_tool(self, name)   # select pan pen highlighter eraser line arrow rect ellipse text sticky laser
    def set_color(self, hex); def set_width(self, w: int); def set_fill(self, hex | None)
    def set_smart_ink(self, on: bool)
    def set_follow(self, user_id | None)        # presentation mode: follow someone's viewport
    def set_spotlight(self, user_id | None)     # authorship lens
    def set_presenting(self, on: bool)          # broadcast my viewport
    def insert_image_file(self, path); def import_pdf(self, path)
    def export(self, path)                       # .png/.svg/.pdf by extension
    def zoom_fit(self); def zoom_reset(self)
    def show_preview_board(self, board | None)   # time-lapse: render a read-only Board instead of live view
    def delete_selection(self); def select_all(self)
```
