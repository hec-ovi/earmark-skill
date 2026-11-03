"""Shared helpers for the server tests: a recording fake board and a live server harness.

No tests here. The fake board answers every Board method with a canned, contract-shaped value
and records the call, so routing, argument mapping and the security layer are tested without
SQLite. Real-board tests use RealBoardMixin.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from earmark import errors  # noqa: E402
from earmark.server import make_server  # noqa: E402


def issue(key: str = "GAME-1", **extra) -> dict:
    n = int(key.split("-")[-1]) if key.split("-")[-1].isdigit() else 1
    data = {
        "key": key, "id": n, "title": f"Issue {n}", "status": "todo", "priority": "medium",
        "type": "task", "topic": None, "assignee": None, "reviewer": None, "reporter": "owner",
        "labels": [], "due": None, "parent": None, "children": [], "blocked": False,
        "stale": False, "lease_until": None, "rank": 1024.0, "version": 1, "comment_count": 0,
        "last_comment_id": None, "created_at": "2026-11-03T10:00:00Z",
        "updated_at": "2026-11-03T10:00:00Z", "body": "", "started_at": None,
        "review_requested_at": None, "resolved_at": None,
        "links": {"blocks": [], "blocked_by": [], "duplicates": [], "duplicated_by": [], "relates": []},
    }
    data.update(extra)
    return data


def topic(tid: int = 1, path: str = "engine") -> dict:
    return {"id": tid, "path": path, "slug": path.rsplit("/", 1)[-1], "name": path, "icon": None,
            "description": "", "parent_id": None, "position": 0, "archived": False,
            "counts": {"open": 0, "total": 0, "by_status": {}}, "children": []}


def comment(cid: int = 1, key: str = "GAME-1", body: str = "hi") -> dict:
    return {"id": cid, "issue": key, "author": "owner", "kind": "note", "body": body,
            "reply_to": None, "mentions": [], "created_at": "2026-11-03T10:00:00Z", "edited_at": None}


def profile(slug: str = "ui-lead") -> dict:
    return {"slug": slug, "name": slug, "role": "worker", "icon": None, "prompt": "", "topics": [],
            "version": 1, "created_at": "2026-11-03T10:00:00Z", "updated_at": "2026-11-03T10:00:00Z",
            "agents": []}


class FakeStore:
    """State shared by every FakeBoard a server opens."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[tuple[str, tuple, dict]] = []
        self.fail: dict[str, Exception] = {}
        self.results: dict[str, object] = {}
        self.version = 1
        self.heads = {"event_id": 10, "comment_id": 5}
        self.opened = 0
        self.closed = 0
        self.actors: list[str | None] = []

    def factory(self, actor: str | None) -> "FakeBoard":
        with self.lock:
            self.opened += 1
            self.actors.append(actor)
        return FakeBoard(self, actor)

    def bump(self, event_id: int | None = None, comment_id: int | None = None) -> None:
        with self.lock:
            self.version += 1
            if event_id is not None:
                self.heads = dict(self.heads, event_id=event_id)
            if comment_id is not None:
                self.heads = dict(self.heads, comment_id=comment_id)

    def last(self, name: str) -> tuple[tuple, dict]:
        with self.lock:
            for call in reversed(self.calls):
                if call[0] == name:
                    return call[1], call[2]
        raise AssertionError(f"{name} was not called; calls: {[c[0] for c in self.calls]}")

    def names(self) -> list[str]:
        with self.lock:
            return [c[0] for c in self.calls]


_DEFAULTS = {
    "info": lambda a, k: {"prefix": "GAME", "owner": "owner",
                          "settings": {"require_review": True, "lease_minutes": 120},
                          "counts": {"by_status": {"todo": 1}, "open": 1, "total": 1}},
    "topics": lambda a, k: [topic()],
    "add_topic": lambda a, k: topic(2, a[0]),
    "edit_topic": lambda a, k: topic(a[0] if isinstance(a[0], int) else 1),
    "move_topic": lambda a, k: topic(a[0] if isinstance(a[0], int) else 1, a[1]),
    "remove_topic": lambda a, k: {"removed": a[0]},
    "list_issues": lambda a, k: {"items": [issue()], "total": 1, "limit": k.get("limit", 50),
                                 "offset": k.get("offset", 0)},
    "get_issue": lambda a, k: issue(a[0]),
    "create_issue": lambda a, k: issue("GAME-7", title=a[0]),
    "update_issue": lambda a, k: issue(a[0], version=(k.get("if_version") or 1) + 1),
    "move": lambda a, k: issue(a[0], status=a[1]),
    "assign": lambda a, k: issue(a[0], assignee=a[1]),
    "rank": lambda a, k: issue(a[0]),
    "link": lambda a, k: issue(a[0]),
    "unlink": lambda a, k: issue(a[0]),
    "delete_issue": lambda a, k: {"deleted": a[0]},
    "issue_events": lambda a, k: [{"id": 1, "issue": a[0], "actor": "owner", "action": "created",
                                   "old": None, "new": None, "at": "2026-11-03T10:00:00Z"}],
    "activity": lambda a, k: {"items": [], "last_id": 0},
    "list_comments": lambda a, k: {"items": [comment()], "last_id": 1, "has_more": False},
    "add_comment": lambda a, k: comment(9, a[0], a[1]),
    "edit_comment": lambda a, k: comment(a[0], body=a[1]),
    "team": lambda a, k: {"agents": []},
    "profiles": lambda a, k: [profile()],
    "get_profile": lambda a, k: profile(a[0]),
    "add_profile": lambda a, k: profile(a[0]),
    "edit_profile": lambda a, k: profile(a[0]),
    "remove_profile": lambda a, k: {"removed": a[0]},
}


class FakeBoard:
    prefix = "GAME"
    owner = "owner"
    path = "/nonexistent/earmark.db"

    def __init__(self, store: FakeStore, actor: str | None) -> None:
        self._store = store
        self.actor = actor

    def close(self) -> None:
        with self._store.lock:
            self._store.closed += 1

    def data_version(self) -> int:
        return self._store.version

    def heads(self) -> dict:
        return dict(self._store.heads)

    def __getattr__(self, name: str):
        if name not in _DEFAULTS:
            raise AttributeError(name)
        store = self._store

        def method(*args, **kwargs):
            with store.lock:
                store.calls.append((name, args, kwargs))
                exc = store.fail.pop(name, None)
                result = store.results.get(name)
            if exc is not None:
                raise exc
            if result is not None:
                return result(args, kwargs) if callable(result) else result
            return _DEFAULTS[name](args, kwargs)

        return method


class Response:
    def __init__(self, status: int, headers: http.client.HTTPMessage, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def json(self) -> dict:
        return json.loads(self.body.decode("utf-8"))

    @property
    def data(self):
        return self.json["data"]

    @property
    def error(self) -> dict:
        return self.json["error"]


class ServerHarness:
    """Run make_server() on an ephemeral port in a background thread."""

    def __init__(self, db_path: str | None = None, *, board_factory=None, ui_dir: str | None = None,
                 poll_interval: float = 0.1, ping_interval: float = 25.0, max_streams: int = 32,
                 verbose: bool = False) -> None:
        self.server = make_server(db_path, host="127.0.0.1", port=0, board_factory=board_factory,
                                  ui_dir=ui_dir, poll_interval=poll_interval,
                                  ping_interval=ping_interval, max_streams=max_streams, verbose=verbose)
        self.port = self.server.port
        self.host = f"127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.close()
        self.thread.join(5)

    def connection(self, timeout: float = 5.0) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)

    def request(self, method: str, path: str, body=None, *, headers: dict | None = None,
                host: str | None = None, raw: bytes | None = None, write_headers: bool = True) -> Response:
        conn = self.connection()
        try:
            hdrs = {}
            if method not in ("GET", "HEAD") and write_headers:
                hdrs["X-Earmark"] = "1"
                hdrs["Content-Type"] = "application/json"
            hdrs.update(headers or {})
            payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", host if host is not None else self.host)
            if payload is not None:
                hdrs.setdefault("Content-Length", str(len(payload)))
            for name, value in hdrs.items():
                if value is not None:
                    conn.putheader(name, value)
            conn.endheaders(payload)
            resp = conn.getresponse()
            return Response(resp.status, resp.headers, resp.read())
        finally:
            conn.close()


class SSEReader:
    """Minimal Server-Sent Events client over a raw socket."""

    def __init__(self, port: int, *, host: str | None = None, timeout: float = 5.0,
                 headers: dict | None = None) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
        self.sock.sendall((f"GET /api/stream HTTP/1.1\r\nHost: {host or f'127.0.0.1:{port}'}\r\n"
                           f"Accept: text/event-stream\r\n{extra}\r\n").encode())
        self.buf = b""
        head = self._read_until(b"\r\n\r\n", timeout)
        lines = head.decode("latin-1").split("\r\n")
        self.status = int(lines[0].split()[1])
        self.headers = {k.strip().lower(): v.strip() for k, v in
                        (line.split(":", 1) for line in lines[1:] if ":" in line)}

    def _read_until(self, marker: bytes, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while marker not in self.buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no {marker!r} within {timeout}s; got {self.buf!r}")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(4096)
            if not chunk:
                raise EOFError(f"stream closed; got {self.buf!r}")
            self.buf += chunk
        head, self.buf = self.buf.split(marker, 1)
        return head

    def read_body(self, timeout: float = 5.0) -> bytes:
        """The body of a non-stream response (an error), by Content-Length."""
        length = int(self.headers.get("content-length", "0"))
        deadline = time.monotonic() + timeout
        while len(self.buf) < length and time.monotonic() < deadline:
            self.sock.settimeout(max(0.01, deadline - time.monotonic()))
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self.buf += chunk
        body, self.buf = self.buf[:length], self.buf[length:]
        return body

    def next_block(self, timeout: float = 5.0) -> str:
        return self._read_until(b"\n\n", timeout).decode("utf-8")

    def next_change(self, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            block = self.next_block(max(0.01, deadline - time.monotonic()))
            fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line and not line.startswith(":"))
            if fields.get("event") == "change":
                return json.loads(fields["data"])

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class FakeServerCase(unittest.TestCase):
    """Base: a live server over a FakeStore. No tests of its own."""

    ui_dir: str | None = None
    harness_kwargs: dict = {}

    def setUp(self) -> None:
        self.store = FakeStore()
        self.h = ServerHarness(board_factory=self.store.factory, ui_dir=self.ui_dir, **self.harness_kwargs)
        self.addCleanup(self.h.stop)

    def call(self, method: str, path: str, body=None, **kw) -> Response:
        return self.h.request(method, path, body, **kw)

    def assertEnvelopeOk(self, resp: Response, status: int = 200) -> dict:
        self.assertEqual(resp.status, status, resp.body)
        env = resp.json
        self.assertEqual(set(env), {"contract_version", "ok", "data", "error", "meta"})
        self.assertTrue(env["ok"])
        self.assertIsNone(env["error"])
        return env["data"]

    def assertEnvelopeError(self, resp: Response, status: int, code: str) -> dict:
        self.assertEqual(resp.status, status, resp.body)
        env = resp.json
        self.assertFalse(env["ok"])
        self.assertIsNone(env["data"])
        self.assertEqual(env["error"]["code"], code, env["error"])
        self.assertIn("retriable", env["error"])
        return env["error"]


# ---- real boards ---------------------------------------------------------------------------
def core_ready() -> bool:
    """True once earmark.board.Board is implemented (core has landed)."""
    try:
        from earmark.board import Board
    except Exception:
        return False
    tmp = tempfile.mkdtemp(prefix="earmark-probe-")
    try:
        board = Board.init(tmp, prefix="PROBE", owner="owner")
        board.close()
        return True
    except NotImplementedError:
        return False
    except Exception:
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def new_board_dir(prefix: str = "GAME", owner: str = "owner", **kw) -> tuple[str, str]:
    """Create a board in a fresh temp dir; returns (dir, db_path)."""
    from earmark.board import Board

    tmp = tempfile.mkdtemp(prefix="earmark-server-test-")
    board = Board.init(tmp, prefix=prefix, owner=owner, **kw)
    path = board.path
    board.close()
    return tmp, path


__all__ = [
    "errors", "issue", "topic", "comment", "profile", "FakeStore", "FakeBoard", "Response",
    "ServerHarness", "SSEReader", "FakeServerCase", "core_ready", "new_board_dir",
]
