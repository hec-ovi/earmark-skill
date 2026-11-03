"""Comments, @mentions, per-issue and inbox read cursors."""

from __future__ import annotations

from earmark import model, views
from earmark.errors import Forbidden, InvalidRequest, NotFound
from earmark.service import Tx, require_text

INBOX_ISSUES = """
SELECT id FROM issues WHERE assignee = :me OR reviewer = :me OR reporter = :me
UNION SELECT issue_id FROM comments WHERE author = :me
UNION SELECT c.issue_id FROM mentions m JOIN comments c ON c.id = m.comment_id WHERE m.handle = :me
"""


class CommentsMixin:
    def add_comment(self, key: str, body: str, *, kind: str = "note",
                    reply_to: int | None = None) -> dict:
        body = require_text(body, "comment text")
        kind = model.parse_comment_kind(kind or "note")
        with self._tx() as tx:
            row = self._issue_row(tx.conn, key)
            if reply_to is not None:
                self._check_reply(tx.conn, row["id"], reply_to)
            comment_id = self._insert_comment(tx, row["id"], body, kind, reply_to)
            self._apply(tx, row["id"], self._renewal(tx, row), bump=False)
            return self._comment(tx.conn, comment_id)

    def _check_reply(self, conn, issue_id: int, reply_to) -> None:
        try:
            target = int(str(reply_to).lstrip("#"))
        except ValueError:
            raise InvalidRequest(f"invalid comment id {reply_to!r}") from None
        found = conn.execute("SELECT issue_id FROM comments WHERE id = ?", (target,)).fetchone()
        if found is None:
            raise NotFound(f"no comment #{target}")
        if found[0] != issue_id:
            raise InvalidRequest(f"comment #{target} is on {self._key(found[0])}, not "
                                 f"{self._key(issue_id)}")

    def _insert_comment(self, tx: Tx, issue_id: int, body: str, kind: str,
                        reply_to: int | None = None) -> int:
        reply = int(str(reply_to).lstrip("#")) if reply_to is not None else None
        cur = tx.conn.execute(
            "INSERT INTO comments(issue_id, author, kind, body, reply_to, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (issue_id, tx.actor, kind, body, reply, tx.ts))
        self._record_mentions(tx.conn, cur.lastrowid, body)
        return cur.lastrowid

    @staticmethod
    def _record_mentions(conn, comment_id: int, body: str) -> None:
        candidates = model.mentions(body)
        if not candidates:
            return
        marks = ",".join("?" * len(candidates))
        known = {r[0] for r in conn.execute(f"SELECT handle FROM agents WHERE handle IN ({marks})",
                                             candidates)}
        conn.executemany("INSERT OR IGNORE INTO mentions(comment_id, handle) VALUES (?, ?)",
                         [(comment_id, h) for h in candidates if h in known])

    def _comment(self, conn, comment_id: int) -> dict:
        row = conn.execute(f"SELECT {views.COMMENT_COLUMNS} FROM comments c WHERE c.id = ?",
                           (comment_id,)).fetchone()
        return views.comments(conn, self.prefix, [row])[0]

    def edit_comment(self, comment_id: int, body: str) -> dict:
        body = require_text(body, "comment text")
        with self._tx() as tx:
            row = tx.conn.execute("SELECT id, issue_id, author, body FROM comments WHERE id = ?",
                                  (int(str(comment_id).lstrip("#")),)).fetchone()
            if row is None:
                raise NotFound(f"no comment #{comment_id}")
            if row["author"] != tx.actor:
                raise Forbidden(f"comment #{row['id']} is by {row['author']}; you can edit only "
                                f"your own comments")
            if body != row["body"]:
                tx.conn.execute("UPDATE comments SET body = ?, edited_at = ? WHERE id = ?",
                                (body, tx.ts, row["id"]))
                tx.conn.execute("DELETE FROM mentions WHERE comment_id = ?", (row["id"],))
                self._record_mentions(tx.conn, row["id"], body)
                issue = self._issue_row(tx.conn, row["issue_id"])
                self._apply(tx, issue["id"], self._renewal(tx, issue), bump=False)
            return self._comment(tx.conn, row["id"])

    def list_comments(self, key: str, *, since: int | None = None, new: bool = False,
                      last: int | None = None, limit: int = 100) -> dict:
        """{items: [Comment], last_id, has_more}. new=True reads after and advances the cursor."""
        limit = int(limit)
        if limit < 1:
            raise InvalidRequest("limit must be at least 1")
        if new and since is not None:
            raise InvalidRequest("give either --since or --new, not both")
        actor = self._need_actor() if new else None
        with self._read() as conn:
            issue_id = self._issue_id(conn, key)
            after = self._cursor(conn, actor, f"issue:{issue_id}") if new else int(since or 0)
            if last:
                rows = conn.execute(
                    f"SELECT {views.COMMENT_COLUMNS} FROM comments c WHERE c.issue_id = ? AND c.id > ? "
                    f"ORDER BY c.id DESC LIMIT ?", (issue_id, after, min(int(last), limit))).fetchall()[::-1]
                has_more = False
            else:
                rows = conn.execute(
                    f"SELECT {views.COMMENT_COLUMNS} FROM comments c WHERE c.issue_id = ? AND c.id > ? "
                    f"ORDER BY c.id LIMIT ?", (issue_id, after, limit + 1)).fetchall()
                has_more = len(rows) > limit
                rows = rows[:limit]
            items = views.comments(conn, self.prefix, rows)
        last_id = items[-1]["id"] if items else (after or None)
        if new and items:
            self._advance_cursor(actor, f"issue:{issue_id}", items[-1]["id"])
        return {"items": items, "last_id": last_id, "has_more": has_more}

    def inbox(self, *, peek: bool = False, limit: int = 50) -> dict:
        """{items: [Comment], last_id, has_more, cursor}. Advances unless peek."""
        actor = self._need_actor()
        limit = int(limit)
        if limit < 1:
            raise InvalidRequest("limit must be at least 1")
        with self._read() as conn:
            self._actor_row(conn)
            cursor = self._cursor(conn, actor, "inbox")
            rows = conn.execute(
                f"SELECT {views.COMMENT_COLUMNS} FROM comments c WHERE c.id > :cursor "
                f"AND c.author <> :me AND c.issue_id IN ({INBOX_ISSUES}) ORDER BY c.id LIMIT :n",
                {"cursor": cursor, "me": actor, "n": limit + 1}).fetchall()
            has_more = len(rows) > limit
            items = views.comments(conn, self.prefix, rows[:limit])
        last_id = items[-1]["id"] if items else cursor
        if items and not peek:
            self._advance_cursor(actor, "inbox", last_id)
            cursor = last_id
        return {"items": items, "last_id": last_id, "has_more": has_more, "cursor": cursor}

    def _inbox_count(self, conn, actor: str) -> int:
        cursor = self._cursor(conn, actor, "inbox")
        return conn.execute(
            f"SELECT count(*) FROM comments c WHERE c.id > :cursor AND c.author <> :me "
            f"AND c.issue_id IN ({INBOX_ISSUES})", {"cursor": cursor, "me": actor}).fetchone()[0]

    def _advance_cursor(self, actor: str, scope: str, last_id: int) -> None:
        """Move a read cursor forward (never back). One small write, only when asked."""
        from earmark import db, timeutil

        with db.write(self.conn) as conn:
            conn.execute(
                "INSERT INTO cursors(agent, scope, last_comment_id, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(agent, scope) DO UPDATE SET last_comment_id = excluded.last_comment_id, "
                "updated_at = excluded.updated_at WHERE excluded.last_comment_id > cursors.last_comment_id",
                (actor, scope, last_id, timeutil.now_iso()))
