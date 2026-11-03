"""Issue reads (list, show, events, activity) and field writes (create, edit, assign, rank,
links, delete). Status moves and claims live in workflow.py."""

from __future__ import annotations

from typing import Any, Iterable

from earmark import model, timeutil, views
from earmark.errors import Conflict, Forbidden, InvalidRequest, NotFound
from earmark.service import Tx, require_text, subtree_sql

EDITABLE = ("title", "body", "topic", "priority", "type", "due", "labels", "parent")
SORTS = {
    "priority": ("i.priority", "i.rank", "i.id"),
    "rank": ("i.rank", "i.id"),
    "updated": ("i.updated_at", "i.id"),
    "created": ("i.created_at", "i.id"),
    "due": ("(i.due_at IS NULL)", "i.due_at", "i.priority", "i.rank", "i.id"),
    "key": ("i.id",),
}
RANK_STEP = 1024.0
RANK_MIN_GAP = 1e-6
EXCERPT = 120
REVERSE_LINK = {"blocks": "blocked_by", "duplicates": "duplicated_by", "relates": "relates"}


def _excerpt(text: str | None) -> str | None:
    if text is None:
        return None
    return text if len(text) <= EXCERPT else text[:EXCERPT - 1] + "…"


def _title(value: str) -> str:
    title = " ".join(str(value or "").split())
    if not 1 <= len(title) <= 200:
        raise InvalidRequest("title must be 1 to 200 characters")
    return title


class IssuesMixin:
    # ---- reads --------------------------------------------------------------------------

    def list_issues(self, *, topic: Iterable[str] = (), exact: bool = False,
                    status: Iterable[str] | str | None = None, priority: Iterable[str] = (),
                    min_priority: str | None = None, assignee: str | None = None,
                    reviewer: str | None = None, reporter: str | None = None,
                    type: Iterable[str] = (), label: Iterable[str] = (), q: str | None = None,
                    due_before: str | None = None, overdue: bool = False,
                    updated_since: str | None = None, created_since: str | None = None,
                    ready: bool = False, blocked: bool = False, stale: bool = False,
                    parent: str | None = None, sort: str = "priority", desc: bool = False,
                    limit: int = 50, offset: int = 0) -> dict:
        """{items: [IssueSummary], total, limit, offset}. status None means 'open'."""
        limit, offset = int(limit), int(offset)
        if limit < 0 or offset < 0:
            raise InvalidRequest("limit and offset must be zero or more")
        sort_key = str(sort or "priority").lower()
        if sort_key not in SORTS:
            raise InvalidRequest(f"invalid sort {sort!r}; use one of: {', '.join(SORTS)}")
        now_ts = timeutil.now_iso()
        with self._read() as conn:
            where, params = self._issue_filters(
                conn, now_ts, topic=topic, exact=exact, status=status, priority=priority,
                min_priority=min_priority, assignee=assignee, reviewer=reviewer,
                reporter=reporter, type=type, label=label, q=q, due_before=due_before,
                overdue=overdue, updated_since=updated_since, created_since=created_since,
                ready=ready, blocked=blocked, stale=stale, parent=parent)
            total = conn.execute(f"SELECT count(*) FROM issues i WHERE {where}", params).fetchone()[0]
            order = ", ".join(term if not desc or term.startswith("(") else term + " DESC"
                              for term in SORTS[sort_key])
            rows = conn.execute(
                f"SELECT {views.SUMMARY_COLUMNS} FROM {views.ISSUE_FROM} WHERE {where} "
                f"ORDER BY {order} LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
            items = views.summaries_from_rows(conn, self.prefix, rows, now_ts)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def _issue_filters(self, conn, now_ts: str, **f) -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []

        def add(sql: str, *values) -> None:
            clauses.append(sql)
            params.extend(values)

        topics = [f["topic"]] if isinstance(f["topic"], str) else list(f["topic"] or ())
        if topics:
            paths = [self._topic_path(conn, t) for t in topics]
            if f["exact"]:
                add(f"i.topic_id IN (SELECT id FROM topics WHERE path IN ({','.join('?' * len(paths))}))",
                    *paths)
            else:
                sql, values = subtree_sql(paths, "path")
                add(f"i.topic_id IN (SELECT id FROM topics WHERE {sql})", *values)
        statuses = model.parse_statuses(f["status"])
        if len(statuses) < len(model.STATUSES):
            add(f"i.status IN ({','.join('?' * len(statuses))})", *statuses)
        priorities = model.parse_priorities(f["priority"])
        if priorities:
            add(f"i.priority IN ({','.join('?' * len(priorities))})", *priorities)
        if f["min_priority"] is not None:
            add("i.priority <= ?", model.parse_priority(f["min_priority"]))
        for column in ("assignee", "reviewer", "reporter"):
            handle = self._handle_filter(f[column])
            if handle == "":
                add(f"i.{column} IS NULL")
            elif handle is not None:
                add(f"i.{column} = ?", handle)
        types = model.parse_types(f["type"])
        if types:
            add(f"i.type IN ({','.join('?' * len(types))})", *types)
        for label in model.parse_labels(f["label"]):
            add("EXISTS (SELECT 1 FROM labels l WHERE l.issue_id = i.id AND l.label = ?)", label)
        if f["q"]:
            needle = "%" + str(f["q"]).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            add("(i.title LIKE ? ESCAPE '\\' OR i.body LIKE ? ESCAPE '\\')", needle, needle)
        if f["due_before"]:
            add("i.due_at IS NOT NULL AND i.due_at < ?", timeutil.parse_date(f["due_before"]))
        if f["overdue"]:
            add("i.due_at IS NOT NULL AND i.due_at < ? AND i.status NOT IN ('done', 'canceled')",
                timeutil.today().isoformat())
        if f["updated_since"]:
            add("i.updated_at >= ?", timeutil.parse_when(f["updated_since"]))
        if f["created_since"]:
            add("i.created_at >= ?", timeutil.parse_when(f["created_since"]))
        if f["ready"]:
            add(f"NOT EXISTS ({views.OPEN_BLOCKER})")
        if f["blocked"]:
            add(f"EXISTS ({views.OPEN_BLOCKER})")
        if f["stale"]:
            add(views.stale_sql(), now_ts)
        if f["parent"] is not None:
            parent = str(f["parent"]).strip().lower()
            if parent == "none":
                add("i.parent_id IS NULL")
            else:
                add("i.parent_id = ?", self._issue_id(conn, f["parent"]))
        return (" AND ".join(clauses) or "1"), params

    def _topic_path(self, conn, path: str) -> str:
        norm = model.parse_topic_path(path)
        if conn.execute("SELECT 1 FROM topics WHERE path = ?", (norm,)).fetchone() is None:
            raise self._topic_not_found(conn, norm)
        return norm

    def get_issue(self, key: str, *, comments: int = 0) -> dict:
        """Issue; adds new_comments for the actor, and 'comments' (last N) when comments > 0."""
        with self._read() as conn:
            issue_id = self._issue_id(conn, key)
            data = views.issue_detail(conn, self.prefix, issue_id, timeutil.now_iso())
            data["new_comments"] = self._new_comment_count(conn, issue_id)
            if comments and int(comments) > 0:
                rows = conn.execute(
                    f"SELECT {views.COMMENT_COLUMNS} FROM comments c WHERE c.issue_id = ? "
                    f"ORDER BY c.id DESC LIMIT ?", (issue_id, int(comments))).fetchall()
                data["comments"] = views.comments(conn, self.prefix, rows[::-1])
        return data

    def _new_comment_count(self, conn, issue_id: int) -> int | None:
        if not self.actor:
            return None
        cursor = self._cursor(conn, self.actor, f"issue:{issue_id}")
        return conn.execute("SELECT count(*) FROM comments WHERE issue_id = ? AND id > ? "
                            "AND author <> ?", (issue_id, cursor, self.actor)).fetchone()[0]

    @staticmethod
    def _cursor(conn, agent: str, scope: str) -> int:
        row = conn.execute("SELECT last_comment_id FROM cursors WHERE agent = ? AND scope = ?",
                           (agent, scope)).fetchone()
        return row[0] if row else 0

    def _detail(self, conn, issue_id: int) -> dict:
        data = views.issue_detail(conn, self.prefix, issue_id, timeutil.now_iso())
        data["new_comments"] = self._new_comment_count(conn, issue_id)
        return data

    def issue_events(self, key: str) -> list[dict]:
        with self._read() as conn:
            issue_id = self._issue_id(conn, key)
            rows = conn.execute("SELECT * FROM events WHERE issue_id = ? ORDER BY id", (issue_id,))
            return [views.event(r, self.prefix) for r in rows]

    def activity(self, *, since: int | None = None, limit: int = 50) -> dict:
        """{items: [Event] newest first, last_id}: events after `since` (all when None)."""
        limit = max(0, int(limit))
        with self._read() as conn:
            rows = conn.execute("SELECT * FROM events WHERE id > ? ORDER BY id DESC LIMIT ?",
                                (int(since or 0), limit)).fetchall()
            head = conn.execute("SELECT coalesce(max(id), 0) FROM events").fetchone()[0]
            items = [views.event(r, self.prefix) for r in rows]
        last_id = items[0]["id"] if items else (int(since) if since else head)
        return {"items": items, "last_id": last_id}

    # ---- writes -------------------------------------------------------------------------

    def create_issue(self, title: str, *, body: str = "", topic: str | None = None,
                     priority: str = "medium", type: str = "task", status: str = "todo",
                     labels: Iterable[str] = (), due: str | None = None,
                     parent: str | None = None, assignee: str | None = None) -> dict:
        title = _title(title)
        body = str(body or "")
        prio = model.parse_priority(priority if priority is not None else "medium")
        kind = model.parse_type(type or "task")
        state = model.parse_status(status or "todo")
        tags = model.parse_labels(labels)
        due_at = timeutil.parse_date(due) if due else None
        with self._tx() as tx:
            if state not in ("backlog", "todo") and not tx.privileged:
                raise InvalidRequest("new issues start in backlog or todo",
                                     hint="earmark new TITLE [--status backlog]")
            conn = tx.conn
            topic_id = self._topic_id(conn, topic) if topic else None
            parent_id = self._issue_id(conn, parent) if parent else None
            wanted = str(assignee or "").strip().lower()
            owner = self._agent_handle(conn, wanted) if wanted not in ("", "none") else None
            rank = conn.execute("SELECT coalesce(max(rank), 0) FROM issues").fetchone()[0] + RANK_STEP
            row = {"title": title, "body": body, "type": kind, "status": state, "priority": prio,
                   "topic_id": topic_id, "parent_id": parent_id, "reporter": tx.actor,
                   "assignee": owner, "rank": rank, "due_at": due_at, "created_at": tx.ts,
                   "updated_at": tx.ts}
            row.update(self._initial_status_fields(tx, state, owner))
            cols = ", ".join(row)
            cur = conn.execute(f"INSERT INTO issues({cols}) VALUES ({','.join('?' * len(row))})",
                               list(row.values()))
            issue_id = cur.lastrowid
            conn.executemany("INSERT INTO labels(issue_id, label) VALUES (?, ?)",
                             [(issue_id, t) for t in tags])
            self._event(tx, issue_id, "created", None, title)
            return self._detail(conn, issue_id)

    @staticmethod
    def _initial_status_fields(tx: Tx, status: str, assignee: str | None) -> dict:
        if status == "in_progress":
            return {"assignee": assignee or tx.actor, "lease_until": tx.lease_until(),
                    "started_at": tx.ts}
        if status == "in_review":
            return {"review_requested_at": tx.ts}
        if status in model.CLOSED:
            return {"resolved_at": tx.ts}
        return {}

    def update_issue(self, key: str, *, if_version: int | None = None, append: str | None = None,
                     add_labels: Iterable[str] = (), remove_labels: Iterable[str] = (),
                     **fields: Any) -> dict:
        """fields ⊆ {title, body, topic, priority, type, due, labels, parent}."""
        unknown = sorted(set(fields) - set(EDITABLE))
        if unknown:
            raise InvalidRequest(f"cannot edit {', '.join(unknown)}; editable: {', '.join(EDITABLE)}")
        if "body" in fields and append is not None:
            raise InvalidRequest("give either a new body or --append, not both")
        parsed = self._parse_edit(fields)
        add = model.parse_labels(add_labels)
        remove = model.parse_labels(remove_labels)
        with self._tx() as tx:
            conn = tx.conn
            row = self._issue_row(conn, key)
            if if_version is not None and int(if_version) != row["version"]:
                raise self._version_conflict(conn, row, int(if_version))
            changes, events = self._field_changes(conn, row, parsed, append)
            label_change = self._label_change(conn, row["id"], parsed.get("labels"), add, remove)
            if label_change:
                events.append(("labels", *label_change))
            if changes or label_change:
                changes.update(self._renewal(tx, row))
                self._apply(tx, row["id"], changes)
                for action, old, new in events:
                    self._event(tx, row["id"], action, old, new)
            return self._detail(conn, row["id"])

    @staticmethod
    def _parse_edit(fields: dict) -> dict:
        out: dict[str, Any] = {}
        if "title" in fields:
            out["title"] = _title(fields["title"])
        if "body" in fields:
            out["body"] = str(fields["body"] or "")
        if "priority" in fields:
            out["priority"] = model.parse_priority(fields["priority"])
        if "type" in fields:
            out["type"] = model.parse_type(fields["type"])
        if "due" in fields:
            out["due"] = timeutil.parse_date(fields["due"]) if fields["due"] else None
        if "labels" in fields:
            out["labels"] = model.parse_labels(fields["labels"] or [])
        for name in ("topic", "parent"):
            if name in fields:
                value = fields[name]
                out[name] = None if value in (None, "", "none") else value
        return out

    def _field_changes(self, conn, row, parsed: dict, append: str | None) -> tuple[dict, list]:
        changes: dict[str, Any] = {}
        events: list[tuple] = []

        def change(column: str, action: str, new, old_text=None, new_text=None) -> None:
            if row[column] != new:
                changes[column] = new
                events.append((action, old_text if old_text is not None else row[column],
                               new_text if new_text is not None else new))

        if "title" in parsed:
            change("title", "title", parsed["title"])
        body = parsed.get("body")
        if append is not None:
            extra = require_text(append, "appended text")
            body = (row["body"].rstrip() + "\n\n" + extra) if row["body"].strip() else extra
        if body is not None and body != row["body"]:
            changes["body"] = body
            events.append(("body", _excerpt(row["body"]), _excerpt(body)))
        if "priority" in parsed:
            change("priority", "priority", parsed["priority"], model.priority_name(row["priority"]),
                   model.priority_name(parsed["priority"]))
        if "type" in parsed:
            change("type", "type", parsed["type"])
        if "due" in parsed:
            change("due_at", "due", parsed["due"])
        if "topic" in parsed:
            topic_id = self._topic_id(conn, parsed["topic"]) if parsed["topic"] else None
            if topic_id != row["topic_id"]:
                changes["topic_id"] = topic_id
                events.append(("topic", self._topic_path_of(conn, row["topic_id"]),
                               self._topic_path_of(conn, topic_id)))
        if "parent" in parsed:
            parent_id = self._valid_parent(conn, row["id"], parsed["parent"])
            if parent_id != row["parent_id"]:
                changes["parent_id"] = parent_id
                events.append(("parent", self._key(row["parent_id"]), self._key(parent_id)))
        return changes, events

    @staticmethod
    def _topic_path_of(conn, topic_id: int | None) -> str | None:
        if topic_id is None:
            return None
        row = conn.execute("SELECT path FROM topics WHERE id = ?", (topic_id,)).fetchone()
        return row[0] if row else None

    def _valid_parent(self, conn, issue_id: int, parent) -> int | None:
        if parent is None:
            return None
        parent_id = self._issue_id(conn, parent)
        cycle = conn.execute(
            "WITH RECURSIVE up(id) AS (SELECT ? UNION SELECT i.parent_id FROM issues i "
            "JOIN up ON i.id = up.id WHERE i.parent_id IS NOT NULL) SELECT 1 FROM up WHERE id = ?",
            (parent_id, issue_id)).fetchone()
        if cycle:
            raise InvalidRequest(f"{self._key(parent_id)} cannot be the parent of "
                                 f"{self._key(issue_id)} (that would make a cycle)")
        return parent_id

    @staticmethod
    def _label_change(conn, issue_id: int, replace: list | None, add: list, remove: list):
        """Apply label edits; returns (old_text, new_text) when the set changed, else None."""
        current = [r[0] for r in conn.execute(
            "SELECT label FROM labels WHERE issue_id = ? ORDER BY label", (issue_id,))]
        target = set(current if replace is None else replace)
        target = (target | set(add)) - set(remove)
        if target == set(current):
            return None
        conn.execute("DELETE FROM labels WHERE issue_id = ?", (issue_id,))
        conn.executemany("INSERT INTO labels(issue_id, label) VALUES (?, ?)",
                         [(issue_id, t) for t in sorted(target)])
        return ",".join(current), ",".join(sorted(target))

    def _version_conflict(self, conn, row, expected: int) -> Conflict:
        current = row["version"]
        issue = self._detail(conn, row["id"])
        since = self._events_since_version(conn, row["id"], current - expected)
        details = dict(issue, current_version=current, events_since=since)
        return Conflict(f"{self._key(row['id'])} changed: you have version {expected}, it is now "
                        f"{current}", hint=f"earmark show {self._key(row['id'])}", details=details)

    def _events_since_version(self, conn, issue_id: int, bumps: int) -> list[dict]:
        """The issue's events from the last `bumps` writes (one write = consecutive events
        sharing actor and timestamp), oldest first."""
        if bumps <= 0:
            return []
        rows = conn.execute("SELECT * FROM events WHERE issue_id = ? ORDER BY id DESC LIMIT 500",
                            (issue_id,)).fetchall()
        picked, groups, last = [], 0, None
        for r in rows:
            mark = (r["actor"], r["at"])
            if mark != last:
                groups += 1
                last = mark
                if groups > bumps:
                    break
            picked.append(r)
        return [views.event(r, self.prefix) for r in reversed(picked)]

    @staticmethod
    def _renewal(tx: Tx, row) -> dict:
        """Lease renewal when the actor holds the issue (any write by the holder renews)."""
        return {"lease_until": tx.lease_until()} if views.holder(row) == tx.actor else {}

    @staticmethod
    def _apply(tx: Tx, issue_id: int, changes: dict, *, bump: bool = True) -> None:
        sets = [f"{column} = ?" for column in changes] + ["updated_at = ?"]
        if bump:
            sets.append("version = version + 1")
        tx.conn.execute(f"UPDATE issues SET {', '.join(sets)} WHERE id = ?",
                        [*changes.values(), tx.ts, issue_id])

    def assign(self, key: str, handle: str | None) -> dict:
        """Owner or pm. Unassigning in_progress work releases it (→ todo); unassigning an
        in_review issue is refused (release or reject the review instead)."""
        with self._tx() as tx:
            self._require_privileged(tx, "assign issues")
            conn = tx.conn
            row = self._issue_row(conn, key)
            target = None
            if handle is not None and str(handle).strip().lower() not in ("", "none"):
                target = self._agent_handle(conn, handle)
            if target == row["assignee"]:
                return self._detail(conn, row["id"])
            if target is None and row["status"] == "in_review":
                issue_key = self._key(row["id"])
                raise Forbidden(f"{issue_key} is in review; its assignee stays until the review ends",
                                hint=f"earmark reject {issue_key} -m \"<why>\"  (or earmark release "
                                     f"{issue_key} to return the review)")
            if target is None and row["status"] == "in_progress":
                changes = self._status_changes(tx, row, "todo")
                self._apply(tx, row["id"], changes)
                self._status_events(tx, row, "todo", changes)
            else:
                changes: dict[str, Any] = {"assignee": target}
                if row["status"] == "in_progress":
                    changes["lease_until"] = tx.lease_until()
                self._apply(tx, row["id"], changes)
                self._event(tx, row["id"], "assignee", row["assignee"], target)
            return self._detail(conn, row["id"])

    def rank(self, key: str, *, before: str | None = None, after: str | None = None,
             top: bool = False, bottom: bool = False) -> dict:
        chosen = [name for name, value in (("before", before), ("after", after), ("top", top),
                                           ("bottom", bottom)) if value]
        if len(chosen) != 1:
            raise InvalidRequest("give exactly one of --before KEY, --after KEY, --top, --bottom")
        with self._tx() as tx:
            conn = tx.conn
            row = self._issue_row(conn, key)
            pivot = before or after
            pivot_id = self._issue_id(conn, pivot) if pivot else None
            if pivot_id == row["id"]:
                raise InvalidRequest("cannot rank an issue relative to itself")
            new_rank = self._target_rank(conn, row["id"], chosen[0], pivot_id)
            if new_rank != row["rank"]:
                self._apply(tx, row["id"], {"rank": new_rank, **self._renewal(tx, row)})
                self._event(tx, row["id"], "rank", row["rank"], new_rank)
            return self._detail(conn, row["id"])

    def _target_rank(self, conn, issue_id: int, mode: str, pivot_id: int | None) -> float:
        for attempt in range(2):
            if mode in ("top", "bottom"):
                agg = "min" if mode == "top" else "max"
                edge = conn.execute(f"SELECT {agg}(rank) FROM issues WHERE id <> ?",
                                    (issue_id,)).fetchone()[0]
                if edge is None:
                    return conn.execute("SELECT rank FROM issues WHERE id = ?", (issue_id,)).fetchone()[0]
                return edge - RANK_STEP if mode == "top" else edge + RANK_STEP
            pivot_rank = conn.execute("SELECT rank FROM issues WHERE id = ?", (pivot_id,)).fetchone()[0]
            if mode == "before":
                sql = ("SELECT rank FROM issues WHERE id NOT IN (?, ?) AND (rank < ? OR "
                       "(rank = ? AND id < ?)) ORDER BY rank DESC, id DESC LIMIT 1")
            else:
                sql = ("SELECT rank FROM issues WHERE id NOT IN (?, ?) AND (rank > ? OR "
                       "(rank = ? AND id > ?)) ORDER BY rank, id LIMIT 1")
            neighbor = conn.execute(sql, (issue_id, pivot_id, pivot_rank, pivot_rank, pivot_id)).fetchone()
            if neighbor is None:
                return pivot_rank - RANK_STEP if mode == "before" else pivot_rank + RANK_STEP
            low, high = sorted((neighbor[0], pivot_rank))
            if high - low >= RANK_MIN_GAP or attempt == 1:
                return (low + high) / 2
            self._renumber(conn)
        raise AssertionError("unreachable")

    @staticmethod
    def _renumber(conn) -> None:
        ids = [r[0] for r in conn.execute("SELECT id FROM issues ORDER BY rank, id")]
        conn.executemany("UPDATE issues SET rank = ? WHERE id = ?",
                         [((n + 1) * RANK_STEP, i) for n, i in enumerate(ids)])

    def link(self, key: str, kind: str, target: str) -> dict:
        kind = model.parse_link_kind(kind)
        with self._tx() as tx:
            conn = tx.conn
            src, dst = self._issue_row(conn, key), self._issue_row(conn, target)
            if src["id"] == dst["id"]:
                raise InvalidRequest("an issue cannot link to itself")
            if self._find_link(conn, src["id"], dst["id"], kind) is None:
                if kind == "blocks" and self._blocks_path(conn, dst["id"], src["id"]):
                    raise InvalidRequest(f"{self._key(dst['id'])} already blocks "
                                         f"{self._key(src['id'])} (directly or through others)")
                conn.execute("INSERT INTO links(src, dst, kind, created_by, created_at) "
                             "VALUES (?, ?, ?, ?, ?)", (src["id"], dst["id"], kind, tx.actor, tx.ts))
                self._link_events(tx, src, dst, kind, "link")
            return self._detail(conn, src["id"])

    def unlink(self, key: str, kind: str, target: str) -> dict:
        kind = model.parse_link_kind(kind)
        with self._tx() as tx:
            conn = tx.conn
            src, dst = self._issue_row(conn, key), self._issue_row(conn, target)
            found = self._find_link(conn, src["id"], dst["id"], kind)
            if found is None:
                raise NotFound(f"no link {self._key(src['id'])} {kind} {self._key(dst['id'])}",
                               hint=f"earmark show {self._key(src['id'])}")
            conn.execute("DELETE FROM links WHERE src = ? AND dst = ? AND kind = ?", (*found, kind))
            self._link_events(tx, src, dst, kind, "unlink")
            return self._detail(conn, src["id"])

    @staticmethod
    def _find_link(conn, src: int, dst: int, kind: str) -> tuple[int, int] | None:
        pairs = [(src, dst), (dst, src)] if kind == "relates" else [(src, dst)]
        for a, b in pairs:
            if conn.execute("SELECT 1 FROM links WHERE src = ? AND dst = ? AND kind = ?",
                            (a, b, kind)).fetchone():
                return a, b
        return None

    @staticmethod
    def _blocks_path(conn, start: int, goal: int) -> bool:
        return conn.execute(
            "WITH RECURSIVE down(id) AS (SELECT dst FROM links WHERE src = ? AND kind = 'blocks' "
            "UNION SELECT l.dst FROM links l JOIN down ON l.src = down.id WHERE l.kind = 'blocks') "
            "SELECT 1 FROM down WHERE id = ?", (start, goal)).fetchone() is not None

    def _link_events(self, tx: Tx, src, dst, kind: str, action: str) -> None:
        self._apply(tx, src["id"], self._renewal(tx, src))
        self._apply(tx, dst["id"], self._renewal(tx, dst))
        self._event(tx, src["id"], action, None if action == "link" else f"{kind} {self._key(dst['id'])}",
                    f"{kind} {self._key(dst['id'])}" if action == "link" else None)
        reverse = f"{REVERSE_LINK[kind]} {self._key(src['id'])}"
        self._event(tx, dst["id"], action, None if action == "link" else reverse,
                    reverse if action == "link" else None)

    def delete_issue(self, key: str) -> dict:
        with self._tx() as tx:
            self._require_owner(tx, "delete issues")
            row = self._issue_row(tx.conn, key)
            issue_key = self._key(row["id"])
            tx.conn.execute("DELETE FROM cursors WHERE scope = ?", (f"issue:{row['id']}",))
            tx.conn.execute("DELETE FROM issues WHERE id = ?", (row["id"],))
            self._event(tx, None, "delete", f"{issue_key} {row['title']}", None)
        return {"deleted": issue_key}
