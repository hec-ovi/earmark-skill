"""Shared helpers for the CLI tests (no tests here): paths, fixtures shaped per docs/HTTP_API.md,
a recording fake board, the commands.md samples and runners for main(argv) and the launcher."""

from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
LAUNCHER = os.path.join(ROOT, "earmark")
COMMANDS_MD = os.path.join(ROOT, "skills", "earmark", "references", "commands.md")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

ENV_KEYS = ("EARMARK_AS", "EARMARK_DB", "NO_COLOR", "EARMARK_DEBUG")


def contract_blocks() -> list[str]:
    """Fenced code blocks of commands.md, in order (the exact text formats)."""
    with open(COMMANDS_MD, encoding="utf-8") as f:
        text = f.read()
    return [b.rstrip("\n") for b in re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.S)]


def contract_block(first_line_prefix: str) -> str:
    for block in contract_blocks():
        if block.startswith(first_line_prefix):
            return block
    raise AssertionError(f"commands.md has no code block starting with {first_line_prefix!r}")


def contract_line(prefix: str) -> str:
    """A backticked sample in commands.md prose, e.g. the `next` empty line."""
    with open(COMMANDS_MD, encoding="utf-8") as f:
        text = f.read()
    for span in re.findall(r"`([^`\n]+)`", text):
        if span.startswith(prefix):
            return span
    raise AssertionError(f"commands.md has no `{prefix}...` sample")


# ---- fixtures (docs/HTTP_API.md shapes, data from the commands.md samples) ---------------

DAY = "2026-11-03"


def summary(**over) -> dict:
    base = {"key": "GAME-42", "id": 42, "title": "Eye color widget resets on reload",
            "status": "in_progress", "priority": "high", "type": "bug",
            "topic": "ui-ux/character-menu/face-layer/eye-color", "assignee": "claude-ui",
            "reviewer": None, "reporter": "hector", "labels": ["regression"], "due": "2026-11-07",
            "parent": "GAME-30", "children": 0, "blocked": False, "stale": False,
            "lease_until": "2026-11-03T16:05:00Z", "rank": 3072.0, "version": 7,
            "comment_count": 3, "last_comment_id": 1199,
            "created_at": "2026-11-03T10:02:11Z", "updated_at": "2026-11-03T14:05:09Z"}
    base.update(over)
    return base


def summary_51(**over) -> dict:
    base = summary(key="GAME-51", id=51, title="Mute button state", status="todo",
                   priority="medium", type="task", topic="ui-ux/video-calls", assignee=None,
                   labels=[], due=None, parent=None, blocked=True, lease_until=None,
                   comment_count=0, last_comment_id=None)
    base.update(over)
    return base


def issue(**over) -> dict:
    base = summary(comment_count=12, last_comment_id=1199)
    base.update({"body": "<description>", "started_at": "2026-11-03T14:05:00Z",
                 "review_requested_at": None, "resolved_at": None,
                 "children": [summary(key="GAME-43", id=43, title="a"),
                              summary(key="GAME-44", id=44, title="b")],
                 "links": {"blocks": ["GAME-51"], "blocked_by": ["GAME-77"], "duplicates": [],
                           "duplicated_by": [], "relates": []},
                 "link_status": {"GAME-51": "todo", "GAME-77": "done"},
                 "new_comments": 3})
    base.update(over)
    return base


def comment(id: int, author: str, kind: str, body: str, created_at: str, reply_to=None,
            issue_key: str = "GAME-42", edited_at=None) -> dict:
    return {"id": id, "issue": issue_key, "author": author, "kind": kind, "body": body,
            "reply_to": reply_to, "mentions": [], "created_at": created_at, "edited_at": edited_at}


COMMENTS = [
    comment(1187, "claude-ui", "progress", "Moved the widget state into the store; reload test passes.",
            "2026-11-03T14:05:09Z"),
    comment(1190, "codex-qa", "note", "Still resets when switching tabs. @claude-ui",
            "2026-11-03T15:10:44Z", reply_to=1187),
]


def topic(path: str, name: str, open_: int, children=()) -> dict:
    return {"id": abs(hash(path)) % 1000, "path": path, "slug": path.rsplit("/", 1)[-1],
            "name": name, "icon": None, "description": "", "parent_id": None, "position": 0,
            "archived": False, "counts": {"open": open_, "total": open_, "by_status": {}},
            "children": list(children)}


TOPICS = [topic("ui-ux", "UI & UX", 14, [
    topic("ui-ux/character-menu", "Character menu", 9, [
        topic("ui-ux/character-menu/face-layer", "Face customization layer", 4, [
            topic("ui-ux/character-menu/face-layer/eye-color", "Eye color widget", 2)])]),
    topic("ui-ux/video-calls", "Video calls", 5)])]

# 2026-11-03T16:00:00Z as the "now" of the team sample
TEAM_NOW = 1793721600.0


def agent(handle, role, profile, cli, seen, presence, working=(), reviewing=()) -> dict:
    return {"handle": handle, "kind": "agent", "profile": profile, "role": role, "cli": cli,
            "online": presence != "offline", "presence": presence, "joined_at": seen,
            "last_seen": seen, "working": list(working), "reviewing": list(reviewing), "assigned": []}


TEAM = {"agents": [
    agent("claude-ui", "worker", "ui-lead", "claude-code", "2026-11-03T15:58:00Z", "active",
          working=[summary()]),
    agent("codex-qa", "reviewer", "reviewer", "codex", "2026-11-03T15:46:00Z", "active",
          reviewing=[summary(key="GAME-37", id=37, status="in_review")]),
    agent("grok-pm", "pm", "pm", "grok", "2026-11-03T15:00:00Z", "idle"),
]}


# ---- fake board --------------------------------------------------------------------------

class FakeBoard:
    """Records every call; returns canned results or raises queued errors per method."""

    instances: list["FakeBoard"] = []

    def __init__(self, db=None, actor=None):
        self.db = db
        self.actor = actor
        self.path = "/tmp/board/.earmark/earmark.db"
        self.prefix = "GAME"
        self.owner = "hector"
        self.calls: list[tuple[str, tuple, dict]] = []
        self.results: dict = {}
        self.errors: dict = {}
        self.closed = False
        FakeBoard.instances.append(self)

    @classmethod
    def open(cls, db=None, actor=None, *, cwd=None):
        board = cls(db, actor)
        board.calls.append(("open", (db, actor), {}))
        return board

    def set_actor(self, handle):
        self.actor = handle
        self.calls.append(("set_actor", (handle,), {}))

    def close(self):
        self.closed = True

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name in self.errors:
                raise self.errors[name]
            result = self.results.get(name, DEFAULTS.get(name))
            return result(*args, **kwargs) if callable(result) else result
        return method

    def called(self, name) -> list[tuple[tuple, dict]]:
        return [(a, k) for n, a, k in self.calls if n == name]


DEFAULTS = {
    "touch": None,
    "list_issues": lambda **kw: {"items": [summary(blocks_open=1), summary_51()], "total": 2,
                                 "limit": kw.get("limit", 50), "offset": kw.get("offset", 0)},
    "get_issue": lambda key, comments=0: issue(**({"comments": COMMENTS} if comments else {})),
    "list_comments": {"items": COMMENTS, "last_id": 1190, "has_more": False},
    "inbox": {"items": COMMENTS, "last_id": 1190, "has_more": False, "cursor": 1190},
    "topics": TOPICS,
    "team": TEAM,
    "next": issue(),
    "claim": summary(),
    "data_version": 1,
}


@contextlib.contextmanager
def fake_board(**results):
    """Patch Board.open with a FakeBoard; yields the list that collects opened boards."""
    from earmark.board import Board
    FakeBoard.instances = []
    orig = FakeBoard.__init__

    def init(self, db=None, actor=None):
        orig(self, db, actor)
        self.results.update(results)

    with mock.patch.object(FakeBoard, "__init__", init), \
            mock.patch.object(Board, "open", FakeBoard.open):
        yield FakeBoard.instances


# ---- runners -----------------------------------------------------------------------------

def clean_env(**env) -> dict:
    out = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    out.update({k: v for k, v in env.items() if v is not None})
    return out


def run_main(argv: list[str], *, stdin: str = "", env: dict | None = None) -> tuple[int, str, str]:
    """main(argv) in-process with captured stdout/stderr and a scripted stdin."""
    from earmark.cli import main
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, clean_env(**(env or {})), clear=True), \
            mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def run_launcher(argv: list[str], *, cwd: str | None = None, stdin: str = "",
                 env: dict | None = None, timeout: float = 60) -> subprocess.CompletedProcess:
    """The checkout launcher in a subprocess: non-TTY stdin/stdout, like an agent's shell."""
    return subprocess.run([sys.executable, LAUNCHER, *argv], cwd=cwd, input=stdin,
                          capture_output=True, text=True, env=clean_env(**(env or {})),
                          timeout=timeout, encoding="utf-8")


# ---- real boards -------------------------------------------------------------------------

class CliBoardCase(unittest.TestCase):
    """A fresh board in a temp dir (owner hector, prefix GAME), a
    frozen board clock, and self.cli(...) running main(argv) against it."""

    prefix = "GAME"
    review = True

    def setUp(self):
        import shutil
        import tempfile
        from datetime import datetime, timezone

        from earmark import timeutil
        self.tmp = tempfile.mkdtemp(prefix="earmark-cli-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.now = datetime(2026, 11, 3, 10, 2, 11, tzinfo=timezone.utc)
        timeutil.set_clock(lambda: self.now)
        self.addCleanup(timeutil.reset_clock)
        self.db = os.path.join(self.tmp, ".earmark", "earmark.db")
        argv = ["init", "--db", self.tmp, "--prefix", self.prefix, "--owner", "hector"]
        if not self.review:
            argv.append("--no-review")
        code, out, err = run_main(argv)
        assert code == 0, (out, err)

    def advance(self, **delta):
        from datetime import timedelta
        self.now += timedelta(**delta)

    def cli(self, *argv, as_: str | None = None, stdin: str = "", env: dict | None = None):
        full = ["--db", self.db, *argv]
        if as_:
            full += ["--as", as_]
        return run_main(full, stdin=stdin, env=env)

    def ok(self, *argv, as_: str | None = None, stdin: str = "") -> str:
        code, out, err = self.cli(*argv, as_=as_, stdin=stdin)
        if code != 0:
            raise AssertionError(f"earmark {' '.join(argv)} -> {code}\n{out}{err}")
        return out

    def fails(self, code: int, *argv, as_: str | None = None, stdin: str = "") -> str:
        got, out, err = self.cli(*argv, as_=as_, stdin=stdin)
        if got != code:
            raise AssertionError(f"earmark {' '.join(argv)} -> {got}, wanted {code}\n{out}{err}")
        return err or out

    def json(self, *argv, as_: str | None = None, stdin: str = "") -> dict:
        import json
        _, out, _ = self.cli(*argv, "--json", as_=as_, stdin=stdin)
        return json.loads(out)
