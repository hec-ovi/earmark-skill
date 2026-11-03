"""Live updates: one read-only watcher thread and a hub of Server-Sent Event subscribers.

The watcher keeps one board connection and checks `PRAGMA data_version` (board.data_version())
once per interval. That pragma lives in memory and costs no I/O; it moves only when another
connection commits. On a change the watcher reads board.heads() and the hub wakes every
subscriber. Nothing here ever writes to the board.

Each subscriber owns a socketpair used as a wakeup pipe, so a stream thread can block in one
select() on both the client socket (to see a disconnect at once) and its wakeup socket, with
the ping interval as the timeout. Bursts of commits coalesce into the latest heads.
"""

from __future__ import annotations

import socket
import sys
import threading
from typing import Callable

from earmark.errors import Busy


def normalize_heads(heads: dict | None) -> dict:
    heads = heads or {}
    return {
        "event_id": int(heads.get("event_id") or 0),
        "comment_id": int(heads.get("comment_id") or 0),
    }


class Subscriber:
    def __init__(self) -> None:
        self._r, self._w = socket.socketpair()
        self._r.setblocking(False)
        self._w.setblocking(False)
        self._lock = threading.Lock()
        self._pending: dict | None = None
        self.closed = False

    def fileno(self) -> int:
        return self._r.fileno()

    def notify(self, heads: dict | None) -> None:
        with self._lock:
            if heads is not None:
                self._pending = heads
        try:
            self._w.send(b"!")
        except (BlockingIOError, InterruptedError):
            pass  # buffer full: a wakeup is already pending
        except OSError:
            pass  # closed concurrently

    def take(self) -> dict | None:
        try:
            while self._r.recv(512):
                pass
        except (BlockingIOError, InterruptedError):
            pass
        except OSError:
            pass
        with self._lock:
            heads, self._pending = self._pending, None
        return heads

    def close(self) -> None:
        for s in (self._r, self._w):
            try:
                s.close()
            except OSError:
                pass


class Hub:
    def __init__(self, max_streams: int = 32) -> None:
        self.max_streams = max_streams
        self._subs: set[Subscriber] = set()
        self._lock = threading.Lock()
        self._heads: dict | None = None
        self._closed = False

    @property
    def heads(self) -> dict | None:
        with self._lock:
            return self._heads

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._subs)

    def subscribe(self) -> Subscriber:
        with self._lock:
            if self._closed:
                raise Busy("server is shutting down", hint="reload the page")
            if len(self._subs) >= self.max_streams:
                raise Busy(
                    f"too many live streams (max {self.max_streams})",
                    hint="close other earmark tabs and reload",
                )
            sub = Subscriber()
            self._subs.add(sub)
            return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            self._subs.discard(sub)
        sub.close()

    def publish(self, heads: dict) -> None:
        heads = normalize_heads(heads)
        with self._lock:
            self._heads = heads
            subs = list(self._subs)
        for sub in subs:
            sub.notify(heads)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            subs = list(self._subs)
        for sub in subs:
            sub.closed = True
            sub.notify(None)


class Watcher(threading.Thread):
    """Polls data_version on its own connection; publishes heads to the hub on change."""

    def __init__(self, open_board: Callable[[], object], hub: Hub, *, interval: float = 1.0,
                 verbose: bool = False) -> None:
        super().__init__(name="earmark-watcher", daemon=True)
        self._open_board = open_board
        self._hub = hub
        self._interval = interval
        self._verbose = verbose
        self._stop_event = threading.Event()
        self.ready = threading.Event()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)

    def _log(self, msg: str) -> None:
        if self._verbose:
            print(f"earmark ui: watcher: {msg}", file=sys.stderr, flush=True)

    def run(self) -> None:
        board = None
        last = None
        while not self._stop_event.is_set():
            try:
                if board is None:
                    board = self._open_board()
                    last = board.data_version()
                    self._hub.publish(board.heads())
                    self.ready.set()
                else:
                    version = board.data_version()
                    if version != last:
                        last = version
                        self._hub.publish(board.heads())
            except Exception as exc:  # keep watching; a busy or vanished board recovers
                self._log(f"{type(exc).__name__}: {exc}")
                if board is not None:
                    try:
                        board.close()
                    except Exception:
                        pass
                board = None
            self._stop_event.wait(self._interval)
        if board is not None:
            try:
                board.close()
            except Exception:
                pass
