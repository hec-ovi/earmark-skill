"""Status transitions, claims and leases, `next` (pick and claim in one transaction), `need`
(file a blocker for another specialist and park the issue) and auto-unblock.

One timestamp per transaction: everything a write decides (stale or live, lease ends) reads tx.ts.
An issue assigned to an agent that has left (agents.online = 0) counts as unassigned: anyone in
scope may claim it, and the claim is a `takeover` naming the previous assignee."""

from __future__ import annotations

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing stays off the startup path
    from typing import Iterable

from earmark import model, timeutil, views
from earmark.errors import Conflict, Empty, Forbidden, InvalidRequest
from earmark.service import Tx, require_text, subtree_sql

WORK_ORDER = (f"(i.assignee IS NOT NULL AND i.assignee = ?) DESC, {views.EFFECTIVE_PRIORITY}, "
              f"{views.BLOCKS_OPEN} DESC, (i.due_at IS NULL), i.due_at, i.rank, i.id")
REVIEW_ORDER = f"{views.EFFECTIVE_PRIORITY}, i.review_requested_at, i.id"
OFFLINE = "(SELECT handle FROM agents WHERE online = 0)"


class WorkflowMixin:
    # ---- public moves -------------------------------------------------------------------

    def move(self, key: str, status: str, *, note: str | None = None) -> dict:
        target = model.parse_status(status)
        return self._moving(key, target, note=note, kind="note")

    def submit_review(self, key: str, *, note: str) -> dict:
        note = require_text(note, "a handoff note (-m: what changed, how to verify)")
        return self._moving(key, "in_review", note=note, kind="handoff", need_assignee=True)

    def approve(self, key: str, *, note: str | None = None) -> dict:
        return self._moving(key, "done", note=(note or "").strip() or "Approved.", kind="approval",
                            require="in_review", need_assignee=True)

    def reject(self, key: str, *, note: str) -> dict:
        note = require_text(note, "a note on what to change (-m)")
        return self._moving(key, "todo", note=note, kind="changes", require="in_review",
                            need_assignee=True)

    def done(self, key: str, *, note: str | None = None) -> dict:
        return self._moving(key, "done", note=note, kind="note")

    def reopen(self, key: str, *, note: str) -> dict:
        note = require_text(note, "a note on why it reopens (-m)")
        return self._moving(key, "todo", note=note, kind="note", require=model.CLOSED)

    def cancel(self, key: str, *, note: str) -> dict:
        note = require_text(note, "a note on why it is canceled (-m)")
        return self._moving(key, "canceled", note=note, kind="note")

    def _moving(self, key: str, target: str, *, note: str | None, kind: str,
                require: str | tuple | None = None, need_assignee: bool = False) -> dict:
        with self._tx() as tx:
            row = self._issue_row(tx.conn, key)
            issue_key = self._key(row["id"])
            if require is not None:
                allowed = (require,) if isinstance(require, str) else require
                if row["status"] not in allowed:
                    raise InvalidRequest(f"{issue_key} is {row['status']}, not "
                                         f"{' or '.join(allowed)}", hint=f"earmark show {issue_key}")
            if need_assignee and row["assignee"] is None:
                raise InvalidRequest(f"{issue_key} has no assignee; review needs someone's work",
                                     hint=f"earmark claim {issue_key}  (or a pm: earmark assign "
                                          f"{issue_key} <handle>)")
            if kind == "note" and row["status"] == "in_review" and target == "done":
                kind = "approval"
            self._move(tx, row, target, note=note, note_kind=kind)
            return self._detail(tx.conn, row["id"], tx.ts)

    # ---- transition core ----------------------------------------------------------------

    def _move(self, tx: Tx, row, target: str, *, note: str | None = None,
              note_kind: str = "note") -> None:
        if row["status"] == target:
            hint = f"earmark claim {self._key(row['id'])}" if target == "in_progress" else None
            raise InvalidRequest(f"{self._key(row['id'])} is already {target}", hint=hint)
        self._check_move(tx, row, target)
        if note and note.strip():
            self._insert_comment(tx, row["id"], note.strip(), note_kind)
        takeover = (self._offline_assignee(tx, row)
                    if target == "in_progress" and row["status"] != "in_review" else None)
        changes = self._status_changes(tx, row, target, takeover=takeover)
        self._apply(tx, row["id"], changes)
        self._status_events(tx, row, target, changes, takeover=takeover)
        if target in model.CLOSED:
            self._unblock(tx, row["id"], target)

    def _unblock(self, tx: Tx, issue_id: int, resolution: str) -> list[int]:
        """Auto-unblock: the issues issue_id blocks that are `blocked` with no other open blocker
        move to todo, assignee kept (event `unblocked` by the resolver). Same transaction."""
        rows = tx.conn.execute(
            f"SELECT i.* FROM links k JOIN issues i ON i.id = k.dst WHERE k.src = ? "
            f"AND k.kind = 'blocks' AND i.status = 'blocked' AND NOT EXISTS ({views.OPEN_BLOCKER}) "
            f"ORDER BY i.id", (issue_id,)).fetchall()
        for row in rows:
            changes = self._status_changes(tx, row, "todo")
            self._apply(tx, row["id"], changes)
            self._event(tx, row["id"], "status", "blocked", "todo")
            self._event(tx, row["id"], "unblocked", f"{self._key(issue_id)} {resolution}", "todo")
        return [r["id"] for r in rows]

    def _offline_assignee(self, tx: Tx, row) -> str | None:
        """The assignee when it is someone else who has left the board, else None."""
        who = row["assignee"]
        if who is None or who == tx.actor:
            return None
        found = tx.conn.execute("SELECT online FROM agents WHERE handle = ?", (who,)).fetchone()
        return who if found is None or not found[0] else None

    def _check_move(self, tx: Tx, row, target: str) -> None:
        if tx.privileged:
            return
        src, key = row["status"], self._key(row["id"])
        if target not in model.TRANSITIONS[src]:
            allowed = ", ".join(model.TRANSITIONS[src])
            raise InvalidRequest(f"cannot move {key} from {src} to {target}; allowed: {allowed}",
                                 hint=f"earmark show {key}")
        if src == "in_progress" and target == "done" and tx.require_review:
            raise Forbidden("this board requires review before done",
                            hint=f"earmark review {key} -m \"<what changed> · <how to verify>\"")
        if src == "in_review" and target in ("done", "todo") and row["assignee"] == tx.actor:
            raise Forbidden("you cannot approve or reject your own work",
                            hint="earmark next --review picks someone else's handoff")
        self._check_holder(tx, row)
        if target == "in_progress" and src != "in_review":
            self._check_assigned(tx, row)

    def _check_holder(self, tx: Tx, row) -> None:
        holder = views.holder(row)
        lease = row["lease_until"]
        if holder and holder != tx.actor and lease and lease >= tx.ts:
            key = self._key(row["id"])
            raise Conflict(f"{key} is held by {holder} until {lease}",
                           hint=f"earmark comment {key} \"@{holder} ...\"  (or pick another: earmark next)",
                           details={"holder": holder, "lease_until": lease, "status": row["status"]})

    def _check_assigned(self, tx: Tx, row) -> None:
        if row["assignee"] not in (None, tx.actor) and not self._offline_assignee(tx, row):
            key = self._key(row["id"])
            raise Conflict(f"{key} is assigned to {row['assignee']}; only they can claim it until "
                           f"it is reassigned", hint=f"earmark next  (or ask a pm: earmark assign {key} <handle>)",
                           details={"assignee": row["assignee"]})

    @staticmethod
    def _status_changes(tx: Tx, row, target: str, *, takeover: str | None = None) -> dict:
        src = row["status"]
        changes: dict = {"status": target}
        if target == "in_progress":
            if row["assignee"] is None or takeover:
                changes["assignee"] = tx.actor
            changes["lease_until"] = tx.lease_until()
            if row["started_at"] is None:
                changes["started_at"] = tx.ts
            if src == "in_review":
                changes["reviewer"] = None
        elif target == "in_review":
            changes.update(reviewer=None, lease_until=None, review_requested_at=tx.ts)
        elif target in model.CLOSED:
            changes.update(resolved_at=tx.ts, lease_until=None)
            if target == "done" and src == "in_review":
                changes["reviewer"] = tx.actor
        else:  # todo, backlog, blocked
            changes["lease_until"] = None
            if src in ("in_review",) + model.CLOSED:
                changes["reviewer"] = None
            if src in model.CLOSED:
                changes["resolved_at"] = None
            if src == "in_progress" and target == "todo":
                changes["assignee"] = None
        return {k: v for k, v in changes.items() if k == "status" or row[k] != v}

    def _status_events(self, tx: Tx, row, target: str, changes: dict, *,
                       takeover: str | None = None) -> None:
        src, issue_id = row["status"], row["id"]
        self._event(tx, issue_id, "status", src, target)
        if takeover:
            self._event(tx, issue_id, "takeover", takeover, tx.actor)
        elif target == "in_progress" and src in ("todo", "blocked", "backlog"):
            self._event(tx, issue_id, "claim", row["assignee"], changes.get("assignee", row["assignee"]))
        elif src == "in_progress" and target == "in_review":
            self._event(tx, issue_id, "review", None, row["assignee"])
        elif src == "in_review" and target == "done":
            self._event(tx, issue_id, "approve", None, tx.actor)
        elif src == "in_review" and target == "todo":
            self._event(tx, issue_id, "reject", None, row["assignee"])
        elif src in model.CLOSED and target == "todo":
            self._event(tx, issue_id, "reopen", src, target)
        elif src == "in_progress" and target == "todo":
            self._event(tx, issue_id, "release", row["assignee"], None)

    # ---- need: work only another agent can do ------------------------------------------

    def need(self, key: str, title: str, *, topic: str, body: str = "", priority: str | None = None,
             type: str = "task", labels: Iterable[str] = (), assignee: str | None = None,
             attach=()) -> dict:
        """One write: create NEW in topic (reporter = actor, priority = KEY's unless given,
        assigned to `assignee` when given), link NEW blocks KEY, note on KEY naming NEW, attach
        files to NEW; when the actor holds KEY, KEY → blocked (assignee kept, lease cleared).
        {created: Issue, blocked: Issue}."""
        from earmark import attachments
        from earmark.issues import RANK_STEP, _title

        title = _title(title)
        if not topic or not str(topic).strip():
            raise InvalidRequest("need: give the topic of the specialist who does it (-t PATH)",
                                 hint=f"earmark need {key} \"{title}\" -t PATH")
        kind = model.parse_type(type or "task")
        tags = model.parse_labels(labels)
        prio = model.parse_priority(priority) if priority is not None else None
        staged = self._prepare(key, attach) if attach else []
        try:
            with self._tx() as tx:
                conn = tx.conn
                row = self._issue_row(conn, key)
                issue_key = self._key(row["id"])
                if row["status"] in model.CLOSED:
                    raise InvalidRequest(f"{issue_key} is {row['status']}; nothing waits on it",
                                         hint=f"earmark new \"{title}\" -t {topic}")
                topic_id = self._topic_id(conn, topic)
                owner = None
                if assignee is not None and str(assignee).strip().lower() not in ("", "none"):
                    owner = self._agent_handle(conn, assignee)
                rank = conn.execute("SELECT coalesce(max(rank), 0) FROM issues").fetchone()[0] + RANK_STEP
                cur = conn.execute(
                    "INSERT INTO issues(title, body, type, status, priority, topic_id, reporter, "
                    "assignee, rank, created_at, updated_at) VALUES (?, ?, ?, 'todo', ?, ?, ?, ?, ?, ?, ?)",
                    (title, str(body or ""), kind, row["priority"] if prio is None else prio, topic_id,
                     tx.actor, owner, rank, tx.ts, tx.ts))
                new_id = cur.lastrowid
                new_key = self._key(new_id)
                conn.executemany("INSERT INTO labels(issue_id, label) VALUES (?, ?)",
                                 [(new_id, t) for t in tags])
                self._event(tx, new_id, "created", None, title)
                if owner:
                    self._event(tx, new_id, "assignee", None, owner)
                conn.execute("INSERT INTO links(src, dst, kind, created_by, created_at) "
                             "VALUES (?, ?, 'blocks', ?, ?)", (new_id, row["id"], tx.actor, tx.ts))
                new_row = self._issue_row(conn, new_id)
                self._link_events(tx, new_row, row, "blocks", "link")
                if staged:
                    self._insert_attachments(tx, new_id, None, staged)
                self._insert_comment(tx, row["id"], f"waiting on {new_key}: {title}", "note")
                if row["status"] == "in_progress" and row["assignee"] == tx.actor:
                    current = self._issue_row(conn, row["id"])
                    changes = self._status_changes(tx, current, "blocked")
                    self._apply(tx, row["id"], changes)
                    self._status_events(tx, current, "blocked", changes)
                return {"created": self._detail(conn, new_id, tx.ts),
                        "blocked": self._detail(conn, row["id"], tx.ts)}
        finally:
            attachments.discard(staged)

    # ---- claims -------------------------------------------------------------------------

    def claim(self, key: str) -> dict:
        with self._tx() as tx:
            row = self._issue_row(tx.conn, key)
            self._claim(tx, row)
            return self._detail(tx.conn, row["id"], tx.ts)

    def _claim(self, tx: Tx, row) -> None:
        src, key = row["status"], self._key(row["id"])
        if src in model.CLOSED:
            raise InvalidRequest(f"{key} is {src}", hint=f"earmark reopen {key} -m \"<why>\"")
        if src == "in_review":
            return self._take(tx, row, "reviewer")
        if src == "in_progress":
            return self._take(tx, row, "assignee")
        if src == "backlog" and not tx.privileged:
            raise InvalidRequest(f"{key} is in backlog; it moves to todo before anyone claims it",
                                 hint=f"earmark move {key} todo")
        self._check_assigned(tx, row)
        takeover = self._offline_assignee(tx, row)
        changes = self._status_changes(tx, row, "in_progress", takeover=takeover)
        self._apply(tx, row["id"], changes)
        self._status_events(tx, row, "in_progress", changes, takeover=takeover)

    def _take(self, tx: Tx, row, column: str) -> None:
        """Claim the holder seat (assignee of in_progress, reviewer of in_review): renew when
        it is yours, take it when empty or stale, conflict when someone holds a live lease."""
        key, current, lease = self._key(row["id"]), row[column], row["lease_until"]
        if column == "reviewer" and row["assignee"] == tx.actor and not tx.privileged:
            raise Forbidden("you cannot review your own work",
                            hint="earmark next --review picks someone else's handoff")
        if current == tx.actor:
            tx.conn.execute("UPDATE issues SET lease_until = ? WHERE id = ?",
                            (tx.lease_until(), row["id"]))
            return
        if current and lease and lease >= tx.ts:
            raise Conflict(f"{key} is held by {current} until {lease}",
                           hint="earmark next  (pick another issue)",
                           details={"holder": current, "lease_until": lease, "status": row["status"]})
        self._apply(tx, row["id"], {column: tx.actor, "lease_until": tx.lease_until()})
        if current:
            self._event(tx, row["id"], "takeover", current, tx.actor)
        else:
            self._event(tx, row["id"], "claim", None, tx.actor)

    def release(self, key: str, *, note: str | None = None) -> dict:
        with self._tx() as tx:
            row = self._issue_row(tx.conn, key)
            src, ikey = row["status"], self._key(row["id"])
            if src not in ("in_progress", "in_review"):
                raise InvalidRequest(f"{ikey} is {src}; only claimed work can be released")
            if src == "in_review" and row["reviewer"] is None:
                raise InvalidRequest(f"nobody holds the review of {ikey}")
            if not tx.privileged:
                self._check_holder(tx, row)
            if note and note.strip():
                self._insert_comment(tx, row["id"], note.strip(), "note")
            if src == "in_progress":
                changes = self._status_changes(tx, row, "todo")
                self._apply(tx, row["id"], changes)
                self._status_events(tx, row, "todo", changes)
            else:
                self._apply(tx, row["id"], {"reviewer": None, "lease_until": None})
                self._event(tx, row["id"], "release", row["reviewer"], None)
            return self._detail(tx.conn, row["id"], tx.ts)

    # ---- next ---------------------------------------------------------------------------

    def next(self, *, review: bool | None = None, topics: Iterable[str] = (),
             dry_run: bool = False) -> dict:
        """Pick and claim in one transaction. review None = role default. Raises Empty with
        details {review_queue, inbox, stale, scope} when nothing is claimable."""
        self._need_actor()
        given = [topics] if isinstance(topics, str) else list(topics or ())
        narrow = [model.parse_topic_path(t) for t in given if t]
        with self._read() as conn:
            agent = self._actor_row(conn)
            for path in narrow:
                self._topic_path(conn, path)
            now_ts = timeutil.now_iso()
            mode, row = self._pick(conn, agent, review, narrow, now_ts)
            if row is None:
                raise self._empty(conn, agent, mode, narrow, now_ts)
            if dry_run:
                return self._detail(conn, row["id"], now_ts)
        with self._tx() as tx:
            agent = self._actor_row(tx.conn)
            mode, row = self._pick(tx.conn, agent, review, narrow, tx.ts)
            if row is None:
                raise self._empty(tx.conn, agent, mode, narrow, tx.ts)
            if mode == "review":
                self._take(tx, row, "reviewer")
            else:
                self._claim(tx, row)
            return self._detail(tx.conn, row["id"], tx.ts)

    def _pick(self, conn, agent, review: bool | None, narrow: list[str], now_ts: str):
        """The first claimable issue at now_ts (tx.ts inside the claiming transaction, so the
        pick and the claim agree on what is stale)."""
        mode = "review" if (review if review is not None else agent["role"] == "reviewer") else "work"
        me = agent["handle"]
        where, params = self._scope_where(conn, agent, narrow)
        if mode == "work":
            sql = (f"SELECT i.* FROM {views.ISSUE_FROM} WHERE ((i.status = 'todo' AND "
                   f"(i.assignee IS NULL OR i.assignee = ? OR i.assignee IN {OFFLINE})) OR "
                   f"(i.status = 'in_progress' AND (i.assignee IS NULL OR (i.lease_until IS NOT NULL "
                   f"AND i.lease_until < ?)))) AND {where} AND NOT EXISTS ({views.OPEN_BLOCKER}) "
                   f"ORDER BY {WORK_ORDER} LIMIT 1")
            args = [me, now_ts, *params, me]
        else:
            sql = (f"SELECT i.* FROM {views.ISSUE_FROM} WHERE i.status = 'in_review' AND "
                   f"(i.reviewer IS NULL OR (i.lease_until IS NOT NULL AND i.lease_until < ?)) "
                   f"AND (i.assignee IS NULL OR i.assignee <> ?) AND {where} "
                   f"ORDER BY {REVIEW_ORDER} LIMIT 1")
            args = [now_ts, me, *params]
        return mode, conn.execute(sql, args).fetchone()

    def _scope_where(self, conn, agent, narrow: list[str]) -> tuple[str, list]:
        scope_sql, scope_params = subtree_sql(self._scope_paths(conn, agent["profile"]))
        narrow_sql, narrow_params = subtree_sql(narrow)
        return f"{scope_sql} AND {narrow_sql}", scope_params + narrow_params

    def _empty(self, conn, agent, mode: str, narrow: list[str], now_ts: str) -> Empty:
        me = agent["handle"]
        where, params = self._scope_where(conn, agent, narrow)
        review_queue = conn.execute(
            f"SELECT count(*) FROM {views.ISSUE_FROM} WHERE i.status = 'in_review' AND "
            f"(i.reviewer IS NULL OR (i.lease_until IS NOT NULL AND i.lease_until < ?)) AND "
            f"(i.assignee IS NULL OR i.assignee <> ?) AND {where}", [now_ts, me, *params]).fetchone()[0]
        stale = conn.execute(f"SELECT count(*) FROM {views.ISSUE_FROM} WHERE {views.stale_sql()} "
                             f"AND {where}", [now_ts, *params]).fetchone()[0]
        inbox = self._inbox_count(conn, me)
        scope = narrow or self._scope_paths(conn, agent["profile"])
        if mode == "work" and review_queue:
            hint = "earmark next --review"
        elif inbox:
            hint = "earmark inbox"
        else:
            hint = f"earmark wait --for {mode}"
        what = "ready" if mode == "work" else "to review"
        return Empty(f"nothing {what} in your scope", hint=hint,
                     details={"review_queue": review_queue, "inbox": inbox, "stale": stale,
                              "scope": scope, "mode": mode})
