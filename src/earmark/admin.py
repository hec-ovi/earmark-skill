"""Board-level utilities: info, config, doctor, export, backup, heads, data_version."""

from __future__ import annotations

import os
import sqlite3
import sys

from earmark import db, model, timeutil, views
from earmark.errors import InvalidRequest


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


class AdminMixin:
    def info(self) -> dict:
        """Superset of GET /api/board: prefix, owner, settings, counts, enums, version, plus
        path, files, agents and schema details."""
        from earmark import CONTRACT_VERSION, __version__

        with self._read() as conn:
            by_status = {s: 0 for s in model.STATUSES}
            for status, n in conn.execute("SELECT status, count(*) FROM issues GROUP BY status"):
                by_status[status] = n
            online, agents = conn.execute(
                "SELECT coalesce(sum(online), 0), count(*) FROM agents").fetchone()
            topics = conn.execute("SELECT count(*) FROM topics").fetchone()[0]
            heads = self._heads(conn)
        return {
            "path": self.path,
            "prefix": self.prefix,
            "owner": self.owner,
            "created_at": self.meta.get("created_at"),
            "schema_version": int(self.meta.get("schema_version", db.SCHEMA_VERSION)),
            "settings": {"require_review": self.require_review, "lease_minutes": self.lease_minutes},
            "counts": {"by_status": by_status, "total": sum(by_status.values()),
                       "open": sum(n for s, n in by_status.items() if s not in model.CLOSED)},
            "agents": {"online": int(online), "total": agents},
            "topics": topics,
            "heads": heads,
            "files": {"db": _file_size(self.path), "wal": _file_size(self.path + "-wal"),
                      "shm": _file_size(self.path + "-shm")},
            "enums": {"statuses": list(model.STATUSES), "priorities": list(model.PRIORITIES),
                      "types": list(model.TYPES), "comment_kinds": list(model.COMMENT_KINDS),
                      "roles": list(model.ROLES), "link_kinds": list(model.LINK_KINDS),
                      "status_groups": {k: list(v) for k, v in model.STATUS_GROUPS.items()},
                      "transitions": {k: list(v) for k, v in model.TRANSITIONS.items()}},
            "version": __version__,
            "contract_version": CONTRACT_VERSION,
            "sqlite_version": sqlite3.sqlite_version,
        }

    def config(self, key: str | None = None, value: str | None = None) -> dict:
        """No key: all settings. Key: that setting. Key and value: set it (owner only)."""
        if key is None:
            self._read_meta_fresh()
            return self._settings()
        name = str(key).strip().lower().replace("-", "_")
        if name not in model.SETTINGS:
            raise InvalidRequest(f"unknown setting {key!r}; settings: {', '.join(model.SETTINGS)}")
        if value is None:
            self._read_meta_fresh()
            return {name: self._settings()[name]}
        stored = self._setting_value(name, value)
        with self._tx() as tx:
            self._require_owner(tx, "change board settings")
            old = self.meta.get(name)
            if old != stored:
                tx.conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) "
                                "DO UPDATE SET value = excluded.value", (name, stored))
                self._event(tx, None, "config", f"{name}={old}", f"{name}={stored}")
        self._read_meta_fresh()
        return {name: self._settings()[name]}

    def _read_meta_fresh(self) -> None:
        with self._read():
            pass

    def _settings(self) -> dict:
        return {"require_review": self.require_review, "lease_minutes": self.lease_minutes,
                "prefix": self.prefix}

    @staticmethod
    def _setting_value(name: str, value) -> str:
        if name == "require_review":
            return "1" if model.parse_bool(value) else "0"
        if name == "lease_minutes":
            try:
                minutes = int(str(value).strip())
            except ValueError:
                minutes = int(timeutil.parse_duration(value).total_seconds() // 60)
            if not 1 <= minutes <= 10080:
                raise InvalidRequest("lease_minutes must be between 1 and 10080 (one week)")
            return str(minutes)
        return model.parse_prefix(value)

    def doctor(self) -> list[dict]:
        """[{name, ok, detail}] checks for Python, SQLite, WAL, schema and the board file."""
        conn = self.conn
        checks = []

        def check(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail})

        check("python", sys.version_info >= (3, 10), sys.version.split()[0])
        check("sqlite", sqlite3.sqlite_version_info >= db.MIN_SQLITE,
              f"{sqlite3.sqlite_version} (needs {'.'.join(map(str, db.MIN_SQLITE))}+)")
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        check("wal", str(mode).lower() == "wal", f"journal_mode={mode}")
        version = db.schema_version(conn)
        check("schema", version == db.SCHEMA_VERSION, f"schema_version={version} (current "
              f"{db.SCHEMA_VERSION})")
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        check("integrity", quick == "ok", f"quick_check: {quick}")
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        check("foreign_keys", not fk, f"{len(fk)} violations" if fk else "no violations")
        board_dir = os.path.dirname(self.path)
        ignore = os.path.join(board_dir, ".gitignore")
        check("location", os.path.isfile(self.path), self.path)
        check("gitignore", os.path.isfile(ignore), ignore if os.path.isfile(ignore) else
              f"missing {ignore} (board files could be committed)")
        wal = _file_size(self.path + "-wal")
        check("wal_size", wal <= 64 * 1024 * 1024, f"{wal} bytes")
        owner = conn.execute("SELECT kind FROM agents WHERE handle = ?", (self.owner,)).fetchone()
        check("owner", owner is not None, f"{self.owner}" + ("" if owner else " is not an agent"))
        return checks

    def export(self) -> dict:
        """JSON-ready dump of the whole board from one consistent snapshot."""
        with self._read() as conn:
            prefix = self.prefix
            rows = conn.execute(f"SELECT {views.DETAIL_COLUMNS} FROM {views.ISSUE_FROM} "
                                f"ORDER BY i.id").fetchall()
            issues = views.summaries_from_rows(conn, prefix, rows, timeutil.now_iso())
            for issue, row in zip(issues, rows):
                issue.update(body=row["body"], started_at=row["started_at"],
                             review_requested_at=row["review_requested_at"],
                             resolved_at=row["resolved_at"])
            comment_rows = conn.execute(f"SELECT {views.COMMENT_COLUMNS} FROM comments c "
                                        f"ORDER BY c.id").fetchall()
            return {
                "exported_at": timeutil.now_iso(),
                "board": {"prefix": prefix, "owner": self.owner, "meta": dict(self.meta)},
                "topics": [dict(r) for r in conn.execute("SELECT * FROM topics ORDER BY path")],
                "profiles": views.profiles(conn, conn.execute(
                    "SELECT * FROM profiles ORDER BY slug").fetchall()),
                "agents": [dict(r) for r in conn.execute("SELECT * FROM agents ORDER BY handle")],
                "issues": issues,
                "links": [{"src": model.format_key(prefix, r["src"]),
                           "dst": model.format_key(prefix, r["dst"]), "kind": r["kind"],
                           "created_by": r["created_by"], "created_at": r["created_at"]}
                          for r in conn.execute("SELECT * FROM links ORDER BY src, dst, kind")],
                "comments": views.comments(conn, prefix, comment_rows),
                "cursors": [dict(r) for r in conn.execute(
                    "SELECT * FROM cursors ORDER BY agent, scope")],
                "events": [views.event(r, prefix) for r in conn.execute(
                    "SELECT * FROM events ORDER BY id")],
            }

    def backup(self, dest: str) -> dict:
        """Consistent copy through the SQLite backup API, as a single file (journal_mode=DELETE)."""
        target = os.path.abspath(os.path.expanduser(dest))
        if os.path.isdir(target):
            stamp = timeutil.now_iso().replace(":", "").replace("-", "")
            target = os.path.join(target, f"earmark-{stamp}.db")
        if os.path.exists(target):
            raise InvalidRequest(f"{target} already exists; choose a new path")
        if not os.path.isdir(os.path.dirname(target)):
            raise InvalidRequest(f"directory {os.path.dirname(target)} does not exist")
        copy = sqlite3.connect(target, isolation_level=None)
        try:
            self.conn.backup(copy)
            copy.execute("PRAGMA journal_mode=DELETE")
        finally:
            copy.close()
        return {"path": target, "bytes": _file_size(target)}

    def data_version(self) -> int:
        """PRAGMA data_version on this board's connection: changes when another connection commits."""
        return int(self.conn.execute("PRAGMA data_version").fetchone()[0])

    def heads(self) -> dict:
        """{event_id, comment_id}: the newest ids, for change notifications."""
        return self._heads(self.conn)

    @staticmethod
    def _heads(conn) -> dict:
        event_id = conn.execute("SELECT coalesce(max(id), 0) FROM events").fetchone()[0]
        comment_id = conn.execute("SELECT coalesce(max(id), 0) FROM comments").fetchone()[0]
        return {"event_id": event_id, "comment_id": comment_id}
