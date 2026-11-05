"""Shared constants for server and client. This is the only place tunables live."""

from __future__ import annotations

# --- Network -----------------------------------------------------------------
PROTO_VERSION = 1                 # bump on any incompatible wire change
APP_ID = "lanboard"
TCP_PORT = 5050                   # default server port (TCP, framed protocol)
DISCOVERY_PORT = 5051             # UDP broadcast discovery
DISCOVERY_MAGIC = "LANBOARD_DISCOVER_V1"

MAX_FRAME = 8 * 1024 * 1024       # bytes, type byte + payload; larger frames => disconnect
READ_CHUNK = 64 * 1024

# --- Liveness ----------------------------------------------------------------
PING_INTERVAL = 3.0               # seconds between client PINGs
CLIENT_TIMEOUT = 12.0             # server drops a client silent for this long
SESSION_TTL = 600.0               # seconds a disconnected session can be resumed
RECONNECT_BACKOFF = (0.5, 1.0, 2.0, 4.0, 5.0)  # seconds, then stays at last value (+jitter)

# --- Traffic shaping ---------------------------------------------------------
LIVE_FLUSH_MS = 33                # stroke preview batching interval (~30 Hz)
CURSOR_MIN_INTERVAL_MS = 50       # cursor updates at most 20 Hz
SEND_QUEUE_MAX = 4000             # per-client outbound frames before "lagging" disconnect
OPS_PER_SEC_LIMIT = 120           # per-client committed-op rate limit (token bucket)
HASH_EVERY_OPS = 25               # server broadcasts a board hash every N ops

# --- History / persistence ---------------------------------------------------
HISTORY_MAX_OPS = 100_000         # ops kept in memory for resume + timeline
AUTOSAVE_EVERY_OPS = 50           # snapshot to disk after this many ops ...
AUTOSAVE_INTERVAL = 10.0          # ... or this many seconds, whichever first

# --- Content limits ----------------------------------------------------------
MAX_USERNAME = 24
MAX_ROOM_CODE = 32
MAX_CHAT_LEN = 2000
CHAT_HISTORY_MAX = 1000
MAX_TEXT_LEN = 5000
MAX_STROKE_POINTS = 20_000        # numbers in a stroke's flat [x0,y0,x1,y1,...] list
MAX_COORD = 1_000_000
MAX_OBJECTS = 50_000
MAX_BATCH_OPS = 500
MAX_BLOB = 8 * 1024 * 1024        # bytes per image blob
BLOB_CHUNK = 192 * 1024           # raw bytes per BLOB chunk frame (base64 inflates ~4/3)

# --- Presentation -------------------------------------------------------------
USER_COLORS = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4",
    "#f032e6", "#9a6324", "#469990", "#800000", "#808000", "#000075",
]

ROLES = ("owner", "editor", "viewer")
