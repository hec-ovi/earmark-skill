"""Status transitions, claims and leases, and `next` (pick and claim in one transaction)."""

from __future__ import annotations

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing stays off the startup path
    from typing import Iterable

from earmark import model, timeutil, views
from earmark.errors import Conflict, Empty, Forbidden, InvalidRequest
from earmark.service import Tx, require_text, subtree_sql

WORK_ORDER = ("(i.assignee IS NOT NULL AND i.assignee = ?) DESC, i.priority, (i.due_at IS NULL), "
              "i.due_at, i.rank, i.id")
REVIEW_ORDER = "i.priority, i.review_requested_at, i.id"


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
            return self._detail(tx.conn, row["id"])

    # ---- transition core ----------------------------------------------------------------

    def _move(self, tx: Tx, row, target: str, *, note: str | None = None,
              note_kind: str = "note") -> None:
        if row["status"] == target:
            hint = f"earmark claim {self._key(row['id'])}" if target == "in_progress" else None
            raise InvalidRequest(f"{self._key(row['id'])} is already {target}", hint=hint)
        self._check_move(tx, row, target)
        if note and note.strip():
            self._insert_comment(tx, row["id"], note.strip(), note_kind)
        changes = self._status_changes(tx, row, target)
        self._apply(tx, row["id"], changes)
        self._status_events(tx, row, target, changes)

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
        if row["assignee"] not in (None, tx.actor):
            key = self._key(row["id"])
            raise Conflict(f"{key} is assigned to {row['assignee']}; only they can claim it until "
                           f"it is reassigned", hint=f"earmark next  (or ask a pm: earmark assign {key} <handle>)",
                           details={"assignee": row["assignee"]})

    @staticmethod
    def _status_changes(tx: Tx, row, target: str) -> dict:
        src = row["status"]
        changes: dict = {"status": target}
        if target == "in_progress":
            if row["assignee"] is None:
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

    def _status_events(self, tx: Tx, row, target: str, changes: dict) -> None:
        src, issue_id = row["status"], row["id"]
        self._event(tx, issue_id, "status", src, target)
        if target == "in_progress" and src in ("todo", "blocked", "backlog"):
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

    # ---- claims -------------------------------------------------------------------------

    def claim(self, key: str) -> dict:
        with self._tx() as tx:
            row = self._issue_row(tx.conn, key)
            self._claim(tx, row)
            return self._detail(tx.conn, row["id"])

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
        changes = self._status_changes(tx, row, "in_progress")
        self._apply(tx, row["id"], changes)
        self._status_events(tx, row, "in_progress", changes)

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
            return self._detail(tx.conn, row["id"])

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
            mode, row = self._pick(conn, agent, review, narrow)
            if row is None:
                raise self._empty(conn, agent, mode, narrow)
            if dry_run:
                return self._detail(conn, row["id"])
        with self._tx() as tx:
            agent = self._actor_row(tx.conn)
            mode, row = self._pick(tx.conn, agent, review, narrow)
            if row is None:
                raise self._empty(tx.conn, agent, mode, narrow)
            if mode == "review":
                self._take(tx, row, "reviewer")
            else:
                self._claim(tx, row)
            return self._detail(tx.conn, row["id"])

    def _pick(self, conn, agent, review: bool | None, narrow: list[str]):
        mode = "review" if (review if review is not None else agent["role"] == "reviewer") else "work"
        me, now_ts = agent["handle"], timeutil.now_iso()
        where, params = self._scope_where(conn, agent, narrow)
        if mode == "work":
            sql = (f"SELECT i.* FROM {views.ISSUE_FROM} WHERE ((i.status = 'todo' AND "
                   f"(i.assignee IS NULL OR i.assignee = ?)) OR (i.status = 'in_progress' AND "
                   f"(i.assignee IS NULL OR (i.lease_until IS NOT NULL AND i.lease_until < ?)))) "
                   f"AND {where} AND NOT EXISTS ({views.OPEN_BLOCKER}) ORDER BY {WORK_ORDER} LIMIT 1")
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

    def _empty(self, conn, agent, mode: str, narrow: list[str]) -> Empty:
        me, now_ts = agent["handle"], timeutil.now_iso()
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
