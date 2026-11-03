"""Shared machinery for the board modules: meta, identity, write context, events, lookups."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing stays off the startup path
    from typing import Iterator

from earmark import db, model, timeutil, views
from earmark.errors import Forbidden, IdentityRequired, InvalidRequest, NotFound, NotJoined

SEEN_THROTTLE_S = 60
FILES_DIR = "files"  # attachment bytes, beside the database


class Tx:
    """One write transaction: the connection, who acts, and the time it happens at.
    (A plain class: dataclasses would pull in `inspect` and cost ~15 ms of startup.)"""

    __slots__ = ("conn", "actor", "now", "ts", "role", "is_owner", "lease_minutes",
                 "require_review")

    def __init__(self, conn: sqlite3.Connection, actor: str, now: datetime, ts: str,
                 role: str | None, is_owner: bool, lease_minutes: int, require_review: bool):
        self.conn, self.actor, self.now, self.ts = conn, actor, now, ts
        self.role, self.is_owner = role, is_owner
        self.lease_minutes, self.require_review = lease_minutes, require_review

    @property
    def privileged(self) -> bool:
        return self.is_owner or self.role == "pm"

    def lease_until(self) -> str:
        return timeutil.add_minutes(self.now, self.lease_minutes)


class BoardBase:
    path: str
    prefix: str
    owner: str
    actor: str | None
    conn: sqlite3.Connection

    def __init__(self, path: str, conn: sqlite3.Connection, actor: str | None = None):
        self.path = path
        self.conn = conn
        self.actor = None
        self.meta: dict[str, str] = {}
        self._seen_checked: str | None = None
        self._load_meta()
        self.set_actor(actor)

    # ---- meta and settings --------------------------------------------------------------

    def _load_meta(self) -> None:
        self.meta = {k: v for k, v in self.conn.execute("SELECT key, value FROM meta")}
        self.prefix = self.meta.get("prefix", "EM")
        self.owner = self.meta.get("owner", "owner")

    @property
    def require_review(self) -> bool:
        return self.meta.get("require_review", "1") != "0"

    @property
    def lease_minutes(self) -> int:
        try:
            return int(self.meta.get("lease_minutes", model.DEFAULT_LEASE_MINUTES))
        except ValueError:
            return model.DEFAULT_LEASE_MINUTES

    @property
    def max_attachment_mb(self) -> int:
        try:
            return int(self.meta.get("max_attachment_mb", model.DEFAULT_MAX_ATTACHMENT_MB))
        except ValueError:
            return model.DEFAULT_MAX_ATTACHMENT_MB

    @property
    def files_dir(self) -> str:
        """Attachment bytes: `files/` beside the database file."""
        return os.path.join(os.path.dirname(os.path.abspath(self.path)), FILES_DIR)

    def set_actor(self, handle: str | None) -> None:
        """Validated lazily: an unknown or malformed handle fails with not_joined on first write."""
        norm = str(handle).strip().lower().lstrip("@") if handle else ""
        self.actor = norm or None
        self._seen_checked = None

    # ---- read and write contexts --------------------------------------------------------

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with db.snapshot(self.conn) as conn:
            self._load_meta()
            try:
                yield conn
            except OverflowError:  # an id, limit or position too large for SQLite
                raise out_of_range() from None

    def _need_actor(self) -> str:
        if not self.actor:
            raise IdentityRequired("this command needs to know who you are",
                                   hint="earmark join <profile>, then pass --as HANDLE "
                                        "(or set EARMARK_AS)")
        return self.actor

    @contextmanager
    def _tx(self) -> Iterator[Tx]:
        actor = self._need_actor()
        with db.write(self.conn) as conn:
            self._load_meta()
            agent = conn.execute(
                "SELECT a.last_seen, a.online, p.role FROM agents a LEFT JOIN profiles p "
                "ON p.slug = a.profile WHERE a.handle = ?", (actor,)).fetchone()
            if agent is None:
                raise self._not_joined(actor)
            now = timeutil.now()
            tx = Tx(conn, actor, now, timeutil.iso(now), agent["role"], actor == self.owner,
                    self.lease_minutes, self.require_review)
            self._refresh_seen(conn, actor, agent["last_seen"], now, online=bool(agent["online"]))
            try:
                yield tx
            except OverflowError:  # rolled back by db.write like any other error
                raise out_of_range() from None

    @staticmethod
    def _not_joined(actor: str) -> NotJoined:
        return NotJoined(f"{actor!r} has not joined this board",
                         hint=f"earmark join <profile> --as {actor}  (see: earmark profiles)")

    def _refresh_seen(self, conn, actor: str, last_seen: str, now: datetime, *,
                      online: bool = True) -> None:
        """Conditional write: only when last_seen is older than the throttle window, or the
        agent had left (any command by it marks it online again)."""
        cutoff = timeutil.iso(now - timedelta(seconds=SEEN_THROTTLE_S))
        if not online:
            conn.execute("UPDATE agents SET online = 1, last_seen = ? WHERE handle = ?",
                         (timeutil.iso(now), actor))
            last_seen = timeutil.iso(now)
        elif last_seen < cutoff:
            conn.execute("UPDATE agents SET last_seen = ? WHERE handle = ? AND last_seen < ?",
                         (timeutil.iso(now), actor, cutoff))
            last_seen = timeutil.iso(now)
        self._seen_checked = last_seen

    def _actor_row(self, conn, handle: str | None = None):
        handle = handle or self._need_actor()
        row = conn.execute(f"{views.AGENT_SELECT} WHERE a.handle = ?", (handle,)).fetchone()
        if row is None:
            raise self._not_joined(handle)
        return row

    @staticmethod
    def _require_privileged(tx: Tx, what: str) -> None:
        if not tx.privileged:
            raise Forbidden(f"only the owner or a pm can {what}",
                            hint="ask the pm: earmark comment KEY \"@<pm> ...\"")

    @staticmethod
    def _require_owner(tx: Tx, what: str) -> None:
        if not tx.is_owner:
            raise Forbidden(f"only the board owner can {what}")

    # ---- events -------------------------------------------------------------------------

    @staticmethod
    def _event(tx: Tx, issue_id: int | None, action: str, old=None, new=None) -> None:
        tx.conn.execute(
            "INSERT INTO events(issue_id, actor, action, old, new, at) VALUES (?, ?, ?, ?, ?, ?)",
            (issue_id, tx.actor, action, None if old is None else str(old),
             None if new is None else str(new), tx.ts))

    # ---- lookups ------------------------------------------------------------------------

    def _key(self, issue_id: int | None) -> str | None:
        return model.format_key(self.prefix, issue_id)

    def _issue_id(self, conn, key: str | int) -> int:
        issue_id = model.parse_key(key, self.prefix)
        if conn.execute("SELECT 1 FROM issues WHERE id = ?", (issue_id,)).fetchone() is None:
            raise NotFound(f"no issue {self._key(issue_id)}", hint="earmark ls")
        return issue_id

    def _issue_row(self, conn, key: str | int) -> sqlite3.Row:
        issue_id = model.parse_key(key, self.prefix)
        row = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
        if row is None:
            raise NotFound(f"no issue {self._key(issue_id)}", hint="earmark ls")
        return row

    def _topic_id(self, conn, path: str) -> int:
        norm = model.parse_topic_path(path)
        row = conn.execute("SELECT id FROM topics WHERE path = ?", (norm,)).fetchone()
        if row is None:
            raise self._topic_not_found(conn, norm)
        return row[0]

    def _topic_not_found(self, conn, path: str) -> NotFound:
        matches = close_topic_matches(conn, path)
        hint = ("did you mean: " + ", ".join(matches)) if matches else "earmark topics"
        return NotFound(f"no topic {path!r}", hint=hint, details={"matches": matches})

    def _topic_row(self, conn, ref: str | int) -> sqlite3.Row:
        if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
            row = conn.execute("SELECT * FROM topics WHERE id = ?", (int(ref),)).fetchone()
            if row is None:
                raise NotFound(f"no topic with id {ref}", hint="earmark topics")
            return row
        path = model.parse_topic_path(ref)
        row = conn.execute("SELECT * FROM topics WHERE path = ?", (path,)).fetchone()
        if row is None:
            raise self._topic_not_found(conn, path)
        return row

    def _agent_handle(self, conn, value: str) -> str:
        """An existing agent handle; 'me' is the actor."""
        handle = self._need_actor() if str(value).strip().lower() == "me" else model.parse_handle(value)
        if conn.execute("SELECT 1 FROM agents WHERE handle = ?", (handle,)).fetchone() is None:
            raise NotFound(f"no agent {handle!r}", hint="earmark team")
        return handle

    def _handle_filter(self, value: str | None):
        """List filter value: None (no filter), '' meaning IS NULL ('none'), or a handle ('me')."""
        if value is None:
            return None
        norm = str(value).strip().lower()
        if norm == "none":
            return ""
        if norm == "me":
            return self._need_actor()
        return model.parse_handle(norm)

    def _scope_paths(self, conn, profile: str | None) -> list[str]:
        if not profile:
            return []
        return [r[0] for r in conn.execute(
            "SELECT t.path FROM profile_topics pt JOIN topics t ON t.id = pt.topic_id "
            "WHERE pt.profile = ? ORDER BY t.path", (profile,))]


def close_topic_matches(conn, path: str, limit: int = 3) -> list[str]:
    import difflib

    paths = [r[0] for r in conn.execute("SELECT path FROM topics ORDER BY path")]
    leaf = path.rsplit("/", 1)[-1]
    by_leaf = [p for p in paths if p.rsplit("/", 1)[-1] == leaf or p.endswith("/" + path)]
    fuzzy = difflib.get_close_matches(path, paths, n=limit, cutoff=0.5)
    by_leaf_fuzzy = [p for p in paths
                     if difflib.SequenceMatcher(None, leaf, p.rsplit("/", 1)[-1]).ratio() >= 0.75]
    out: list[str] = []
    for candidate in by_leaf + fuzzy + by_leaf_fuzzy:
        if candidate not in out:
            out.append(candidate)
    return out[:limit]


def subtree_sql(paths: list[str], column: str = "t.path") -> tuple[str, list]:
    """SQL matching any of the paths or their subtrees."""
    if not paths:
        return "1", []
    clause = " OR ".join(f"({column} = ? OR {column} LIKE ?)" for _ in paths)
    params: list = []
    for p in paths:
        params += [p, p + "/%"]
    return f"({clause})", params


def out_of_range() -> InvalidRequest:
    return InvalidRequest(f"a number in the request is out of range (ids, limits and positions "
                          f"go up to {model.MAX_ID})")


def require_text(value: str | None, what: str) -> str:
    text = (value or "").strip()
    if not text:
        raise InvalidRequest(f"{what} is required")
    return text
