"""Board service: the only layer that runs SQL. The CLI and the HTTP server call these methods.

Contract (frozen for v0.1; additive changes only):
- Every method returns JSON-ready dicts/lists whose shapes are the ones in docs/HTTP_API.md
  (IssueSummary, Issue, Comment, Topic, Agent, Profile, Event) and contracts/board.schema.json.
- Inputs arrive as raw strings or simple values from the CLI or query strings; the board parses
  and validates them (keys, priorities, dates, durations, 'me'/'none', status groups).
- Errors are raised as earmark.errors subclasses with a message and, when useful, a hint.
- Field edits: only keys present in **fields change; a value of None clears nullable fields
  (due, parent, topic).
- Writes require an actor (see ARCHITECTURE.md, Identity) and run in db.write().

The methods live in focused modules and are composed here:

| Module | Methods |
|---|---|
| issues.py | list_issues, get_issue, issue_events, activity, create_issue, update_issue, assign, rank, link, unlink, delete_issue |
| workflow.py | move, claim, release, next, submit_review, approve, reject, done, reopen, cancel |
| comments.py | add_comment, edit_comment, list_comments, inbox |
| topics.py | topics, add_topic, edit_topic, move_topic, remove_topic |
| team.py | profiles, get_profile, add_profile, edit_profile, remove_profile, join, leave, whoami, team, triage, wait_check |
| admin.py | info, config, doctor, export, backup, data_version, heads |

A Board holds one SQLite connection; use one Board per thread (or serialize calls).
"""

from __future__ import annotations

import os

from earmark import db as _db
from earmark import model, seed, timeutil
from earmark.admin import AdminMixin
from earmark.comments import CommentsMixin
from earmark.errors import InvalidRequest
from earmark.issues import IssuesMixin
from earmark.service import BoardBase
from earmark.team import TeamMixin
from earmark.topics import TopicsMixin
from earmark.workflow import WorkflowMixin


class Board(IssuesMixin, WorkflowMixin, CommentsMixin, TopicsMixin, TeamMixin, AdminMixin,
            BoardBase):
    # ---- lifecycle ---------------------------------------------------------------------

    @classmethod
    def open(cls, db: str | None = None, actor: str | None = None, *,
             cwd: str | None = None) -> "Board":
        """Discover and open a board (see ARCHITECTURE.md, Storage). Raises NoBoard."""
        path = _db.find_board(db, cwd=cwd)
        return cls(path, _db.open_board(path), actor)

    @classmethod
    def init(cls, directory: str, *, prefix: str | None = None, owner: str | None = None,
             require_review: bool = True) -> "Board":
        """Create <directory>/.earmark/earmark.db, seed profiles, create the owner agent."""
        root = os.path.abspath(directory)
        if not os.path.isdir(root):
            raise InvalidRequest(f"{root} is not a directory")
        board_dir = os.path.join(root, _db.DIR_NAME)
        path = os.path.join(board_dir, _db.DB_NAME)
        if os.path.exists(path):
            raise InvalidRequest(f"a board already exists at {path}", hint="earmark info")
        prefix = model.parse_prefix(prefix) if prefix else model.default_prefix(os.path.basename(root))
        owner = model.parse_handle(owner, "owner handle") if owner else default_owner(root)
        os.makedirs(board_dir, exist_ok=True)
        ignore = os.path.join(board_dir, ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w", encoding="utf-8") as fh:
                fh.write("*\n")
        conn = _db.create(path)
        with _db.write(conn):
            _seed_board(conn, prefix=prefix, owner=owner, require_review=require_review)
        return cls(path, conn, owner)

    def close(self) -> None:
        conn, self.conn = getattr(self, "conn", None), None
        if conn is not None:
            conn.close()

    def __enter__(self) -> "Board":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def touch(self) -> None:
        """Refresh agents.last_seen for the actor, at most once per 60 s. No-op without actor."""
        if not self.actor:
            return
        now = timeutil.now()
        cutoff = timeutil.iso(now - _seen_window())
        if self._seen_checked and self._seen_checked >= cutoff:  # last_seen known to be fresh
            return
        row = self.conn.execute("SELECT last_seen FROM agents WHERE handle = ?",
                                (self.actor,)).fetchone()
        if row is None:
            return
        if row[0] < cutoff:
            with _db.write(self.conn) as conn:
                self._refresh_seen(conn, self.actor, row[0], now)
        else:
            self._seen_checked = row[0]


def _seen_window():
    from datetime import timedelta

    from earmark.service import SEEN_THROTTLE_S

    return timedelta(seconds=SEEN_THROTTLE_S)


def default_owner(directory: str) -> str:
    """Slug of `git config user.name`, else $USER, else 'owner'."""
    import subprocess

    try:
        out = subprocess.run(["git", "config", "user.name"], cwd=directory, capture_output=True,
                             text=True, timeout=_db.GIT_TIMEOUT_S, check=False)
        name = model.slugify(out.stdout.strip()) if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        name = ""
    return name or model.slugify(os.environ.get("USER") or os.environ.get("USERNAME") or "") or "owner"


def _seed_board(conn, *, prefix: str, owner: str, require_review: bool) -> None:
    ts = timeutil.now_iso()
    meta = {"prefix": prefix, "owner": owner, "created_at": ts,
            "require_review": "1" if require_review else "0",
            "lease_minutes": str(model.DEFAULT_LEASE_MINUTES)}
    conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                     "SET value = excluded.value", list(meta.items()))
    conn.executemany(
        "INSERT INTO profiles(slug, name, role, icon, prompt, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(p["slug"], p["name"], p["role"], p["icon"], p["prompt"], ts, ts) for p in seed.profile_seeds()])
    conn.execute("INSERT INTO agents(handle, kind, profile, cli, online, joined_at, last_seen) "
                 "VALUES (?, 'human', NULL, 'human', 1, ?, ?)", (owner, ts, ts))
