"""Crash-safe on-disk storage for rooms.

Layout (one directory per room, under ``<data>/rooms/``)::

    <data>/                       default: per-user app-data dir, see default_data_dir()
      server.json                 server identity + user-id counter
      rooms/<safe-code>/
        room.json                 meta: code, name, salt/verifier, settings, sessions, counters
        snapshot.json             board snapshot {seq, objects}      (atomic replace)
        ops.jsonl                 append-only log of OpMsg since the snapshot (write-ahead log)
        history_base.json         board at the start of the durable history {seq, objects}
        history.jsonl             archive of every OpMsg after history_base, up to the snapshot
        chat.jsonl                append-only chat log
        saves/<safe-name>.json    named saves  (+ saves/_index.json for cheap listing)
        blobs/<sha256>            image bytes, content addressed (+ <sha256>.mime)

Durability model
----------------
* The **snapshot + log** pair is the classic write-ahead-log design used by
  databases: every accepted op is appended to ``ops.jsonl`` *before* it is
  broadcast, so a killed server can rebuild its board by loading the last
  snapshot and replaying every logged op with ``seq > snapshot.seq``.
* **History archive.** When the log is compacted into a new snapshot, the ops
  that leave ``ops.jsonl`` are first appended to ``history.jsonl``. So
  ``history_base.json`` + ``history.jsonl`` + ``ops.jsonl`` always hold the
  complete, gap-free op sequence from the base (seq 0 for a new room), and the
  time-lapse survives restarts. The history is capped (``HISTORY_MAX_OPS`` /
  ``HISTORY_MAX_BYTES``): the oldest ops are folded into ``history_base.json``,
  and ``history.jsonl`` is rewritten without them.
* Write order makes every crash point recoverable: history files first, then
  ``snapshot.json``, then the ``ops.jsonl`` truncation. The loader skips any
  record it already has (by seq), so an op found in two files is harmless.
* Whole-file writes (snapshot, room.json, saves, history rewrite) go to a
  temporary file in the same directory, are ``fsync``-ed and then
  ``os.replace``-d over the old file. ``os.replace`` is atomic on POSIX and
  Windows (``MoveFileEx`` with ``MOVEFILE_REPLACE_EXISTING``), so a reader or
  a restart after a crash sees either the complete old file or the complete
  new one, never a torn mix. On Windows the replace fails while *another*
  process (virus scanner, search indexer, backup tool) briefly holds the
  target open, so it is retried for a moment (:func:`replace_file`).
* Appends are ``flush``-ed to the OS on every line but not ``fsync``-ed: the
  page cache survives a process kill (the failure we care about on a LAN demo);
  only a power cut can lose the last few lines, and the loader tolerates a
  truncated final line. Before appending to a file whose last line was torn
  by a crash, a newline is written first, so the next record never gets glued
  onto the broken one (which would corrupt it too and leave a seq gap).

Threading: the event loop calls the cheap append methods directly; the slow
whole-file writers (``write_checkpoint``, ``write_save``, ``write_blob``...) are
called through ``asyncio.to_thread`` by the room so the loop never blocks on
disk. Each method touches its own files (the history files are only ever
touched by the room's single snapshot task), and saves/index updates are
guarded by a lock, so concurrent thread calls are safe.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Iterator

log = logging.getLogger("lanboard.persistence")

_SAFE_CHARS = re.compile(r"[^a-z0-9_-]+")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
                     *(f"lpt{i}" for i in range(10))}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def safe_name(name: str, max_len: int = 40) -> str:
    """Map a user-chosen name (room code, save name) to a safe file name.

    Only ``[a-z0-9_-]`` survives. If anything had to change (upper case,
    spaces, unicode, a reserved Windows name...) a short hash of the original
    is appended, so two distinct names can never map to the same file even on
    case-insensitive filesystems (macOS, Windows): ``"Lab"`` and ``"lab"``
    become ``lab-1a2b3c4d`` and ``lab``.
    """
    base = _SAFE_CHARS.sub("_", name.lower()).strip("_")[:max_len] or "x"
    if base == name and base not in _WINDOWS_RESERVED:
        return base
    return f"{base}-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]}"


def default_data_dir() -> Path:
    """The per-user application-data directory used when ``--data`` is not given.

    * Windows: ``%APPDATA%\\LANBoard``
    * macOS:   ``~/Library/Application Support/LANBoard``
    * Linux:   ``$XDG_DATA_HOME/lanboard``, i.e. usually ``~/.local/share/lanboard``

    A fixed per-user location (instead of the current directory) means the
    rooms are found again no matter where the server was started from, and a
    frozen executable never litters its own folder or the user's desktop.
    """
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "LANBoard"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "LANBoard"
    xdg = os.environ.get("XDG_DATA_HOME", "")
    return (Path(xdg) if os.path.isabs(xdg) else home / ".local" / "share") / "lanboard"


def dumps_line(rec: Any) -> bytes:
    """One JSONL record, encoded exactly like a frame payload (compact UTF-8)."""
    return json.dumps(rec, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"


_REPLACE_RETRIES = 6


def replace_file(src: Path, dst: Path) -> None:
    """``os.replace`` that tolerates Windows' transient sharing violations.

    POSIX lets you rename over a file that someone has open. Windows refuses
    (``PermissionError``) while any handle to the target lacks
    ``FILE_SHARE_DELETE``, which is what virus scanners and the search indexer
    briefly hold right after we write a file. Those handles go away within
    milliseconds, so we retry with a short backoff instead of failing the save.
    """
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt" or attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(0.02 * 2 ** attempt)


def _fsync_dir(directory: Path) -> None:
    """Make a rename durable on POSIX (the directory entry lives in the directory)."""
    if os.name == "nt":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (tmp file + fsync + os.replace)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        replace_file(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def read_json(path: Path) -> Any | None:
    """Read a JSON file, returning None if it is missing or unreadable."""
    try:
        with open(path, "rb") as fh:
            return json.loads(fh.read())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("cannot read %s: %s", path, exc)
        return None


def read_jsonl_sized(path: Path) -> list[tuple[dict, int]]:
    """Read a JSON-lines file as ``(record, line size in bytes)`` pairs.

    Corrupt lines are skipped (and logged). A crash in the middle of an append
    leaves a truncated last line; that line is simply ignored, which is
    exactly the "replay up to the last complete record" behaviour of a
    write-ahead log. The sizes feed the room's history byte cap.
    """
    out: list[tuple[dict, int]] = []
    try:
        with open(path, "rb") as fh:
            for lineno, line in enumerate(fh, 1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    log.warning("%s:%d: skipping corrupt line", path, lineno)
                    continue
                if isinstance(rec, dict):
                    out.append((rec, len(raw) + 1))
    except FileNotFoundError:
        pass
    return out


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSON-lines file, skipping (and logging) corrupt lines."""
    return [rec for rec, _ in read_jsonl_sized(path)]


class _AppendLog:
    """A lazily opened append-only JSONL file, flushed after every record.

    The file is opened in binary mode so a record takes the same bytes on
    every OS (text mode on Windows would turn ``\\n`` into ``\\r\\n``).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def _open(self):
        if self._fh is None:
            fh = open(self.path, "ab")
            try:
                if fh.tell() > 0:
                    with open(self.path, "rb") as rd:
                        rd.seek(-1, os.SEEK_END)
                        if rd.read(1) != b"\n":
                            fh.write(b"\n")
            except OSError:
                pass
            self._fh = fh
        return self._fh

    def append(self, rec: dict, raw: bytes | None = None) -> int:
        """Append one record (``raw``: its JSON, if already encoded). Returns bytes written."""
        line = raw + b"\n" if raw is not None else dumps_line(rec)
        fh = self._open()
        fh.write(line)
        fh.flush()
        return len(line)

    def append_many(self, recs: Iterable[dict], durable: bool = False) -> None:
        """Append several records in one write (``durable``: fsync before returning)."""
        body = b"".join(dumps_line(r) for r in recs)
        if not body:
            return
        fh = self._open()
        fh.write(body)
        fh.flush()
        if durable:
            os.fsync(fh.fileno())

    def rewrite(self, recs: Iterable[dict]) -> None:
        """Atomically replace the whole file with ``recs`` (used for truncation)."""
        self.close()
        atomic_write_bytes(self.path, b"".join(dumps_line(r) for r in recs))

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None


class RoomStore:
    """All files belonging to one room."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.meta_path = directory / "room.json"
        self.snapshot_path = directory / "snapshot.json"
        self.history_base_path = directory / "history_base.json"
        self.saves_dir = directory / "saves"
        self.blobs_dir = directory / "blobs"
        self._ops = _AppendLog(directory / "ops.jsonl")
        self._history = _AppendLog(directory / "history.jsonl")
        self._chat = _AppendLog(directory / "chat.jsonl")
        self._saves_lock = threading.Lock()

    @property
    def ops_path(self) -> Path:
        return self._ops.path

    @property
    def history_path(self) -> Path:
        return self._history.path

    @property
    def chat_path(self) -> Path:
        return self._chat.path

    def ensure(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.saves_dir.mkdir(exist_ok=True)
        self.blobs_dir.mkdir(exist_ok=True)

    def load_meta(self) -> dict | None:
        meta = read_json(self.meta_path)
        return meta if isinstance(meta, dict) else None

    def save_meta(self, meta: dict) -> None:
        atomic_write_json(self.meta_path, meta)

    def load_snapshot(self) -> dict | None:
        snap = read_json(self.snapshot_path)
        return snap if isinstance(snap, dict) else None

    def write_snapshot(self, snap: dict) -> None:
        """Atomically replace snapshot.json (called from a worker thread)."""
        atomic_write_json(self.snapshot_path, snap)

    def append_op(self, msg: dict, raw: bytes | None = None) -> int:
        """Log one OpMsg (``raw``: its already encoded JSON). Returns the line size."""
        return self._ops.append(msg, raw)

    def load_ops(self) -> list[dict]:
        return read_jsonl(self.ops_path)

    def load_ops_sized(self) -> list[tuple[dict, int]]:
        return read_jsonl_sized(self.ops_path)

    def load_history(self) -> tuple[dict | None, list[tuple[dict, int]]]:
        """``(history_base or None if never written, archived ops with line sizes)``."""
        base = read_json(self.history_base_path)
        return (base if isinstance(base, dict) else None), read_jsonl_sized(self.history_path)

    def write_checkpoint(self, snap: dict, archive: list[dict], base: dict | None = None) -> None:
        """One compaction step (worker thread): archive ops, then write the snapshot.

        ``base is None``: append ``archive`` (the ops about to leave ops.jsonl)
        to history.jsonl. Otherwise the history was trimmed or never written:
        write ``base`` to history_base.json and make ``archive`` the whole of
        history.jsonl. The history files are written (and fsync-ed) *before*
        snapshot.json, so they never lag behind a snapshot after a crash.
        """
        if base is None:
            self._history.append_many(archive, durable=True)
        else:
            atomic_write_json(self.history_base_path, base)
            self._history.rewrite(archive)
        atomic_write_json(self.snapshot_path, snap)

    def rewrite_ops(self, tail: list[dict]) -> None:
        """Truncate the log to ``tail`` (the ops newer than the last snapshot)."""
        self._ops.rewrite(tail)

    def append_chat(self, msg: dict) -> None:
        self._chat.append(msg)

    def load_chat(self, keep: int) -> list[dict]:
        """Return the last ``keep`` chat messages; compact the file if it grew large."""
        msgs = read_jsonl(self.chat_path)
        if len(msgs) > 2 * keep:
            msgs = msgs[-keep:]
            self._chat.rewrite(msgs)
        return msgs[-keep:]

    def _index_path(self) -> Path:
        return self.saves_dir / "_index.json"

    def _read_index(self) -> dict[str, dict]:
        idx = read_json(self._index_path())
        if isinstance(idx, dict):
            return idx
        idx = {}
        for p in self.saves_dir.glob("*.json"):
            if p.name.startswith("_"):
                continue
            data = read_json(p)
            if isinstance(data, dict) and isinstance(data.get("name"), str):
                idx[data["name"]] = {"name": data["name"], "ts": data.get("ts", 0),
                                     "objects": len(data.get("objects", []))}
        return idx

    def write_save(self, name: str, seq: int, objects: list[dict]) -> dict:
        """Store a named save and return its listing entry (worker thread)."""
        entry = {"name": name, "ts": round(time.time(), 3), "objects": len(objects)}
        with self._saves_lock:
            self.saves_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.saves_dir / f"{safe_name(name)}.json",
                              {"name": name, "ts": entry["ts"], "seq": seq, "objects": objects})
            idx = self._read_index()
            idx[name] = entry
            atomic_write_json(self._index_path(), idx)
        return entry

    def list_saves(self) -> list[dict]:
        with self._saves_lock:
            idx = self._read_index()
        return sorted(idx.values(), key=lambda e: e.get("ts", 0), reverse=True)

    def read_save(self, name: str) -> dict | None:
        data = read_json(self.saves_dir / f"{safe_name(name)}.json")
        if not isinstance(data, dict) or data.get("name") != name:
            return None
        return data

    def _blob_path(self, sha: str) -> Path:
        if not _HEX64.match(sha):
            raise ValueError("bad blob hash")
        return self.blobs_dir / sha

    def has_blob(self, sha: str) -> bool:
        return self._blob_path(sha).is_file()

    def write_blob(self, sha: str, data: bytes, mime: str) -> None:
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(self._blob_path(sha).with_suffix(".mime"), mime.encode("utf-8"))
        atomic_write_bytes(self._blob_path(sha), data)

    def read_blob(self, sha: str) -> tuple[bytes, str] | None:
        path = self._blob_path(sha)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            mime = path.with_suffix(".mime").read_text("utf-8").strip()
        except OSError:
            mime = "application/octet-stream"
        return data, mime

    def close(self) -> None:
        self._ops.close()
        self._history.close()
        self._chat.close()


class Storage:
    """Root of the data directory: server identity plus one RoomStore per room."""

    def __init__(self, root: str | os.PathLike) -> None:
        self.root = Path(root)
        self.rooms_dir = self.root / "rooms"
        self.rooms_dir.mkdir(parents=True, exist_ok=True)
        self.server_path = self.root / "server.json"

    def load_server_meta(self) -> dict:
        meta = read_json(self.server_path)
        return meta if isinstance(meta, dict) else {}

    def save_server_meta(self, meta: dict) -> None:
        atomic_write_json(self.server_path, meta)

    def room_store(self, code: str) -> RoomStore:
        return RoomStore(self.rooms_dir / safe_name(code))

    def iter_room_stores(self) -> Iterator[RoomStore]:
        for d in sorted(self.rooms_dir.iterdir()):
            if d.is_dir() and (d / "room.json").is_file():
                yield RoomStore(d)
