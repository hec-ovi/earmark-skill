"""Profiles, join/leave, presence, whoami, team, triage, wait_check, and `check` (the
per-agent change feed) with its read-only count `pending_changes` (the dirty hint)."""

from __future__ import annotations

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing stays off the startup path
    from typing import Any, Iterable

from earmark import model, seed, timeutil, views
from earmark.errors import Conflict, InvalidRequest, NotFound
from earmark.service import Tx

PROFILE_FIELDS = ("name", "role", "icon", "prompt", "topics")
TRIAGE_LIMIT = 100
WAIT_MODES = ("work", "review", "changes", "any")
WAIT_ALIASES = {"inbox": "changes"}  # wait woke on unread inbox comments before the check feed

# ---- check: the per-agent change feed (cursor scope 'check': last event id, last comment id) --
# Only writes by others count, after the cursor (and, for `check`, up to the heads read at its
# start); a comment also only when the agent has not read it on its issue yet. Every row below
# maps to a change kind or a count, so `pending_changes` (a count of distinct issues over the
# same queries) is exactly the number of issues `check` lists.
# Plans are pinned (CROSS JOIN order, INDEXED BY): the agent's own issues, then index seeks into
# their events and comments after the cursor, so the cost follows the agent's share of the board,
# never how long ago it last checked, whatever statistics the board carries.
CHECK_LIMIT = 10
CHECK_ACTIONS = ("status", "assignee", "approve", "reject", "unblocked", "review_cap", "title",
                 "body", "priority", "type", "topic", "due", "labels", "parent", "link", "unlink",
                 "attach")
_SEATS = (("assignee", "issues_assignee"), ("reviewer", "issues_reviewer"))
# events on issues the agent is assignee or reviewer of
CHECK_EVENTS_MINE = [
    f"SELECT {{cols}} FROM issues i INDEXED BY {index} CROSS JOIN events e INDEXED BY "
    f"events_issue ON e.issue_id = i.id AND e.id > :ev{{upto}} WHERE i.{seat} = :me "
    f"AND e.actor <> :me AND e.action IN ({', '.join(repr(a) for a in CHECK_ACTIONS)})"
    for seat, index in _SEATS]
# assignment changes naming the agent, which may hold the issue no more (index: events_action)
CHECK_EVENTS_NAMING = [
    "SELECT {cols} FROM events e WHERE e.action IN ('assignee', 'takeover', 'release') "
    "AND e.id > :ev{upto} AND e.actor <> :me AND e.issue_id IS NOT NULL "
    "AND (e.old = :me OR (e.action = 'assignee' AND e.new = :me))"]
# comments on its issues, and comments anywhere mentioning it; a comment the agent already read
# on its issue (`next`, `show --new`, `comments --new` moved its `issue:<id>` cursor past it) is
# not news, the same dedupe the inbox uses (one primary-key seek per comment row)
_UNREAD = ("c.id > coalesce((SELECT ic.last_comment_id FROM cursors ic WHERE ic.agent = :me "
           "AND ic.scope = 'issue:' || c.issue_id), 0)")
CHECK_COMMENTS_MINE = [
    f"SELECT {{cols}} FROM issues i INDEXED BY {index} CROSS JOIN comments c INDEXED BY "
    f"comments_issue ON c.issue_id = i.id AND c.id > :cm{{upto}} WHERE i.{seat} = :me "
    f"AND c.author <> :me AND {_UNREAD}" for seat, index in _SEATS]
CHECK_COMMENTS_MENTIONING = [
    "SELECT {cols} FROM mentions m INDEXED BY mentions_handle CROSS JOIN comments c "
    "ON c.id = m.comment_id WHERE m.handle = :me AND m.comment_id > :cm{upto} AND c.author <> :me "
    f"AND {_UNREAD}"]
_EVENT_COLS = "e.id, e.issue_id, e.actor, e.action, e.old, e.new, e.at"
_COMMENT_COLS = ("c.id, c.issue_id, c.kind, EXISTS (SELECT 1 FROM mentions q WHERE "
                 "q.comment_id = c.id AND q.handle = :me) AS mentioned")


def _union(parts: list[str], cols: str, upto: str) -> str:
    return " UNION ".join(p.format(cols=cols, upto=upto) for p in parts)


PENDING_SQL = "SELECT count(*) FROM ({} UNION {})".format(
    _union(CHECK_EVENTS_MINE + CHECK_EVENTS_NAMING, "e.issue_id", ""),
    _union(CHECK_COMMENTS_MINE + CHECK_COMMENTS_MENTIONING, "c.issue_id", ""))
CHECK_EVENTS_SQL = _union(CHECK_EVENTS_MINE + CHECK_EVENTS_NAMING, _EVENT_COLS,
                          " AND e.id <= :evh") + " ORDER BY 1"
CHECK_COMMENTS_SQL = _union(CHECK_COMMENTS_MINE + CHECK_COMMENTS_MENTIONING, _COMMENT_COLS,
                            " AND c.id <= :cmh") + " ORDER BY 1"
# kinds that say why a write happened; the status move of the same write is not repeated
_SEMANTIC = ("assigned", "unassigned", "rejected", "approved", "unblocked", "review_cap", "takeover")
_ACTION_KINDS = ("assigned", "rejected", "unblocked", "review_cap", "takeover")
_FIELDS = {"title": "title", "body": "body", "priority": "priority", "type": "type",
           "topic": "topic", "due": "due", "labels": "labels", "parent": "parent",
           "link": "links", "unlink": "links", "assignee": "assignee"}


def _minutes_left(lease: str | None, now) -> int:
    if not lease:
        return 0
    return max(0, int((timeutil.parse_iso(lease) - now).total_seconds() // 60))


class TeamMixin:
    # ---- profiles -----------------------------------------------------------------------

    def profiles(self) -> list[dict]:
        with self._read() as conn:
            rows = conn.execute("SELECT * FROM profiles ORDER BY slug").fetchall()
            return views.profiles(conn, rows)

    def get_profile(self, slug: str) -> dict:
        with self._read() as conn:
            return self._profile(conn, model.parse_slug(slug))

    def _profile(self, conn, slug: str) -> dict:
        row = conn.execute("SELECT * FROM profiles WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            known = [r[0] for r in conn.execute("SELECT slug FROM profiles ORDER BY slug")]
            raise NotFound(f"no profile {slug!r}", hint="profiles: " + (", ".join(known) or "none"))
        return views.profiles(conn, [row])[0]

    def add_profile(self, slug: str, *, name: str, role: str, icon: str | None = None,
                    prompt: str = "", topics: Iterable[str] = ()) -> dict:
        slug = model.parse_slug(slug)
        role = model.parse_role(role)
        name = (name or "").strip() or slug
        paths = [topics] if isinstance(topics, str) else list(topics or ())
        with self._tx() as tx:
            self._require_privileged(tx, "manage profiles")
            if tx.conn.execute("SELECT 1 FROM profiles WHERE slug = ?", (slug,)).fetchone():
                raise Conflict(f"profile {slug!r} already exists", hint=f"earmark profile edit {slug}")
            topic_ids = [self._topic_id(tx.conn, p) for p in paths]
            tx.conn.execute("INSERT INTO profiles(slug, name, role, icon, prompt, created_at, "
                            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (slug, name, role, (icon or "").strip() or None, prompt or "", tx.ts, tx.ts))
            self._set_profile_topics(tx, slug, topic_ids)
            self._event(tx, None, "profile_add", None, slug)
            return self._profile(tx.conn, slug)

    @staticmethod
    def _set_profile_topics(tx: Tx, slug: str, topic_ids: list[int]) -> None:
        tx.conn.execute("DELETE FROM profile_topics WHERE profile = ?", (slug,))
        tx.conn.executemany("INSERT OR IGNORE INTO profile_topics(profile, topic_id) VALUES (?, ?)",
                            [(slug, t) for t in topic_ids])

    def edit_profile(self, slug: str, *, if_version: int | None = None, **fields: Any) -> dict:
        """fields ⊆ {name, role, icon, prompt, topics}."""
        unknown = sorted(set(fields) - set(PROFILE_FIELDS))
        if unknown:
            raise InvalidRequest(f"cannot edit {', '.join(unknown)}; editable: {', '.join(PROFILE_FIELDS)}")
        slug = model.parse_slug(slug)
        with self._tx() as tx:
            self._require_privileged(tx, "manage profiles")
            current = self._profile(tx.conn, slug)
            if if_version is not None and int(if_version) != current["version"]:
                raise Conflict(f"profile {slug} changed: you have version {if_version}, it is now "
                               f"{current['version']}", hint=f"earmark profile show {slug}",
                               details=dict(current, current_version=current["version"]))
            changed = self._profile_changes(tx, current, fields)
            if changed:
                tx.conn.execute("UPDATE profiles SET updated_at = ?, version = version + 1 "
                                "WHERE slug = ?", (tx.ts, slug))
                self._event(tx, None, "profile_edit", slug, ",".join(changed))
            return self._profile(tx.conn, slug)

    def _profile_changes(self, tx: Tx, current: dict, fields: dict) -> list[str]:
        changed: list[str] = []
        for column in ("name", "role", "icon", "prompt"):
            if column not in fields:
                continue
            value = fields[column]
            if column == "role":
                value = model.parse_role(value)
            elif column == "name":
                value = (value or "").strip()
                if not value:
                    raise InvalidRequest("profile name cannot be empty")
            elif column == "icon":
                value = (value or "").strip() or None
            else:
                value = value or ""
            if value != current[column]:
                tx.conn.execute(f"UPDATE profiles SET {column} = ? WHERE slug = ?",
                                (value, current["slug"]))
                changed.append(column)
        if "topics" in fields:
            raw = fields["topics"]
            paths = [raw] if isinstance(raw, str) else list(raw or ())
            ids = [self._topic_id(tx.conn, p) for p in paths]
            new_paths = sorted({model.parse_topic_path(p) for p in paths})
            if new_paths != current["topics"]:
                self._set_profile_topics(tx, current["slug"], ids)
                changed.append("topics")
        return changed

    def remove_profile(self, slug: str) -> dict:
        """Refused while agents (online or offline) use it: they would lose role and scope."""
        slug = model.parse_slug(slug)
        with self._tx() as tx:
            self._require_privileged(tx, "manage profiles")
            self._profile(tx.conn, slug)
            users = [r[0] for r in tx.conn.execute(
                "SELECT handle FROM agents WHERE profile = ? ORDER BY handle", (slug,))]
            if users:
                others = [r[0] for r in tx.conn.execute(
                    "SELECT slug FROM profiles WHERE slug <> ? ORDER BY slug", (slug,))]
                target = "worker" if "worker" in others else (others[0] if others else "<profile>")
                raise InvalidRequest(
                    f"profile {slug!r} is used by {len(users)} agent(s): {', '.join(users)}",
                    hint=f"move them to another profile first: earmark join {target} "
                         f"--as {users[0]}", details={"agents": users})
            tx.conn.execute("DELETE FROM profiles WHERE slug = ?", (slug,))
            self._event(tx, None, "profile_remove", slug, None)
        return {"deleted": slug}

    # ---- join, leave, whoami ------------------------------------------------------------

    def join(self, profile: str, *, handle: str | None = None, cli: str | None = None) -> dict:
        """Briefing: {agent, profile, scope, assigned, inbox_new, loop, board}. Without a handle,
        resumes the seat its CLI left (see _resumable_handle), else mints <profile>-<n>.
        `join_action` is then 'join' or 'rejoin' (an offline agent came back)."""
        from earmark import db

        slug = model.parse_slug(profile)
        wanted = model.parse_handle(handle) if handle else (
            model.parse_handle(self.actor) if self.actor else None)
        cli = (cli or "").strip() or None
        with db.write(self.conn) as conn:
            self._load_meta()
            self._profile(conn, slug)
            now = timeutil.now()
            ts = timeutil.iso(now)
            me = wanted or self._resumable_handle(conn, slug, cli) or self._free_handle(conn, slug)
            existing = conn.execute("SELECT handle, profile, cli, online FROM agents "
                                    "WHERE handle = ?", (me,)).fetchone()
            action = "join"
            if existing:
                self._check_rejoin(existing, slug, cli)
                action = "join" if existing["online"] else "rejoin"
                conn.execute("UPDATE agents SET profile = ?, cli = coalesce(?, cli), online = 1, "
                             "last_seen = ? WHERE handle = ?", (slug, cli, ts, me))
            else:
                conn.execute("INSERT INTO agents(handle, kind, profile, cli, online, joined_at, "
                             "last_seen) VALUES (?, 'agent', ?, ?, 1, ?, ?)", (me, slug, cli, ts, ts))
            tx = Tx(conn, me, now, ts, None, me == self.owner, self.lease_minutes, self.require_review)
            used_cli = cli or (existing["cli"] if existing else None)
            self._event(tx, None, action, None, f"{slug} cli={used_cli}" if used_cli else slug)
            self._start_check_cursor(conn, me, ts, keep=action == "rejoin")
        self.set_actor(me)
        self.join_action = action
        return self._briefing(me)

    @staticmethod
    def _resumable_handle(conn, slug: str, cli: str | None) -> str | None:
        """`join PROFILE --cli C` without a handle resumes the seat a session of the same CLI
        left: the most recently seen offline agent with this profile and this CLI, so its
        cursors, assigned work and history carry over. Never an online agent, never a handle from
        another CLI or profile; None (mint a new handle) when there is no such seat or no CLI."""
        if not cli:
            return None
        row = conn.execute("SELECT handle FROM agents WHERE kind = 'agent' AND online = 0 AND "
                           "profile = ? AND cli = ? ORDER BY last_seen DESC, joined_at DESC, "
                           "handle LIMIT 1", (slug, cli)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _check_rejoin(existing, slug: str, cli: str | None) -> None:
        """`join --as H` for an existing H from another CLI is someone else's identity: refused,
        unless H has left and the same profile is given (a deliberate resume, logged `rejoin`)."""
        theirs = existing["cli"]
        if not cli or not theirs or cli == theirs:
            return
        if not existing["online"] and existing["profile"] == slug:
            return
        handle = existing["handle"]
        state = "online" if existing["online"] else f"offline, profile {existing['profile']}"
        hint = f"earmark join {slug} --cli {cli}  (picks a free handle)"
        if not existing["online"] and existing["profile"]:
            hint += (f"; to resume {handle} on purpose: earmark join {existing['profile']} "
                     f"--as {handle} --cli {cli}")
        raise Conflict(f"handle {handle!r} belongs to an agent on {theirs} ({state}); "
                       f"you are on {cli}", hint=hint,
                       details={"handle": handle, "cli": theirs, "online": bool(existing["online"]),
                                "profile": existing["profile"]})

    @staticmethod
    def _start_check_cursor(conn, handle: str, ts: str, *, keep: bool = False) -> None:
        """The `check` feed starts at the current heads (created when missing). keep (a rejoin:
        an agent that left comes back) leaves an existing cursor where it was, so its first
        `check` reports what others changed while it was away."""
        conflict = ("DO NOTHING" if keep else
                    "DO UPDATE SET last_comment_id = excluded.last_comment_id, "
                    "last_event_id = excluded.last_event_id, updated_at = excluded.updated_at")
        conn.execute(
            "INSERT INTO cursors(agent, scope, last_comment_id, last_event_id, updated_at) "
            "SELECT ?, 'check', (SELECT coalesce(max(id), 0) FROM comments), "
            "(SELECT coalesce(max(id), 0) FROM events), ? WHERE 1 "
            f"ON CONFLICT(agent, scope) {conflict}", (handle, ts))

    @staticmethod
    def _free_handle(conn, slug: str) -> str:
        """Smallest n with <profile>-<n> not used by any agent, online or offline: a handle is an
        identity (claims, cursors, mentions) and is never handed to a new agent."""
        taken = {r[0] for r in conn.execute("SELECT handle FROM agents")}

        def candidate(n: int) -> str:
            return f"{slug[:32 - len(str(n)) - 1]}-{n}"

        n = 1
        while candidate(n) in taken:
            n += 1
        return model.parse_handle(candidate(n))

    def _briefing(self, handle: str) -> dict:
        with self._read() as conn:
            agent_row = self._actor_row(conn, handle)
            agent = views.agents(conn, self.prefix, [agent_row], timeutil.now())[0]
            profile = self._profile(conn, agent_row["profile"]) if agent_row["profile"] else None
            assigned_ids = [r[0] for r in conn.execute(
                "SELECT id FROM issues WHERE status NOT IN ('done', 'canceled') AND "
                "(assignee = ? OR (status = 'in_review' AND reviewer = ?)) ORDER BY priority, rank, id",
                (handle, handle))]
            return {
                "agent": agent,
                "profile": profile,
                "scope": profile["topics"] if profile else [],
                "assigned": views.summaries(conn, self.prefix, assigned_ids, timeutil.now_iso()),
                "inbox_new": self._inbox_count(conn, handle),
                "loop": seed.loop_text(profile["role"] if profile else None),
                "board": {"prefix": self.prefix, "require_review": self.require_review,
                          "lease_minutes": self.lease_minutes},
            }

    def leave(self, *, keep: bool = False) -> dict:
        with self._tx() as tx:
            returned: list[str] = []
            if not keep:
                returned = self._return_work(tx)
            tx.conn.execute("UPDATE agents SET online = 0, last_seen = ? WHERE handle = ?",
                            (tx.ts, tx.actor))
            self._event(tx, None, "leave", None, ",".join(returned) or None)
            agent = views.agents(tx.conn, self.prefix, [self._actor_row(tx.conn)], tx.now)[0]
        self._seen_checked = None  # offline now: only its next join or write brings it back
        return {"agent": agent, "returned": returned}

    def _return_work(self, tx: Tx) -> list[str]:
        rows = tx.conn.execute(
            "SELECT * FROM issues WHERE (assignee = ? AND status IN ('in_progress', 'todo')) OR "
            "(reviewer = ? AND status = 'in_review') ORDER BY id", (tx.actor, tx.actor)).fetchall()
        for row in rows:
            if row["status"] == "in_review":
                self._apply(tx, row["id"], {"reviewer": None, "lease_until": None})
                self._event(tx, row["id"], "release", tx.actor, None)
            elif row["status"] == "in_progress":
                changes = self._status_changes(tx, row, "todo")
                self._apply(tx, row["id"], changes)
                self._status_events(tx, row, "todo", changes)
            else:
                self._apply(tx, row["id"], {"assignee": None})
                self._event(tx, row["id"], "assignee", tx.actor, None)
        return [self._key(r["id"]) for r in rows]

    def whoami(self) -> dict:
        """{agent: Agent, profile: Profile|None, scope: [paths], is_owner}."""
        handle = self._need_actor()
        with self._read() as conn:
            row = self._actor_row(conn, handle)
            agent = views.agents(conn, self.prefix, [row], timeutil.now())[0]
            profile = self._profile(conn, row["profile"]) if row["profile"] else None
        return {"agent": agent, "profile": profile, "scope": profile["topics"] if profile else [],
                "is_owner": handle == self.owner}

    def team(self) -> dict:
        with self._read() as conn:
            rows = conn.execute(f"{views.AGENT_SELECT} ORDER BY a.online DESC, a.handle").fetchall()
            return {"agents": views.agents(conn, self.prefix, rows, timeutil.now())}

    # ---- triage, wait -------------------------------------------------------------------

    def triage(self, *, limit: int = TRIAGE_LIMIT) -> dict:
        """PM view: {backlog, unassigned, offline, stale, review_queue, review_since, blocked,
        review_cap, overdue, idle_agents, counts}. Lists hold IssueSummary (Agent for
        idle_agents), at most `limit` each, most urgent (effective priority) first; counts are the
        totals. offline: open work assigned to agents that have left (anyone in scope may claim
        it). review_cap: issues parked in blocked because their review rounds ran out (the owner
        or a pm decides); they are not repeated under blocked.
        idle_agents: online agents (not the human owner, not pms) working and reviewing nothing."""
        limit = int(limit)
        if limit < 1:
            raise InvalidRequest("limit must be at least 1")
        now = timeutil.now()
        now_ts = timeutil.iso(now)
        today = now.date().isoformat()
        urgent = f"{views.EFFECTIVE_PRIORITY}, i.rank, i.id"
        queries = {
            "backlog": ("i.status = 'backlog'", [], urgent),
            "unassigned": ("i.status = 'todo' AND i.assignee IS NULL", [], urgent),
            "offline": ("i.status IN ('todo', 'blocked', 'in_progress') AND i.assignee IN "
                        "(SELECT handle FROM agents WHERE online = 0)", [], urgent),
            "stale": (views.stale_sql(), [now_ts], "i.lease_until, i.id"),
            "review_queue": ("i.status = 'in_review' AND (i.reviewer IS NULL OR "
                             "(i.lease_until IS NOT NULL AND i.lease_until < ?))", [now_ts],
                             "i.review_requested_at, i.id"),
            "blocked": (f"i.status NOT IN ('done', 'canceled') AND (i.status = 'blocked' OR "
                        f"EXISTS ({views.OPEN_BLOCKER})) AND NOT ({views.REVIEW_CAPPED})", [], urgent),
            "review_cap": (views.REVIEW_CAPPED, [], urgent),
            "overdue": ("i.status NOT IN ('done', 'canceled') AND i.due_at IS NOT NULL AND "
                        "i.due_at < ?", [today], "i.due_at, i.priority, i.id"),
        }
        out: dict[str, Any] = {}
        counts: dict[str, int] = {}
        with self._read() as conn:
            for name, (where, params, order) in queries.items():
                counts[name] = conn.execute(f"SELECT count(*) FROM issues i WHERE {where}",
                                            params).fetchone()[0]
                rows = conn.execute(f"SELECT i.id, i.review_requested_at FROM issues i WHERE {where} "
                                    f"ORDER BY {order} LIMIT ?", params + [limit]).fetchall()
                out[name] = views.summaries(conn, self.prefix, [r[0] for r in rows], now_ts)
                if name == "review_queue":
                    out["review_since"] = {self._key(r[0]): r[1] for r in rows}
            agents = conn.execute(
                f"{views.AGENT_SELECT} WHERE a.online = 1 AND a.kind <> 'human' AND "
                f"coalesce(p.role, '') <> 'pm' ORDER BY a.last_seen").fetchall()
            team = views.agents(conn, self.prefix, agents, now)
        idle = [a for a in team if not a["working"] + a["reviewing"]]
        out["idle_agents"] = idle[:limit]
        counts["idle_agents"] = len(idle)
        out["counts"] = counts
        return out

    # ---- check: what others changed in the agent's world -------------------------------

    def check(self, *, peek: bool = False) -> dict:
        """The actor's change feed since its last check (or join): issues newly assigned to it,
        events by others on issues it is assignee or reviewer of, comments and files by others
        on them, comments anywhere mentioning it (only past its `issue:<id>` cursor: what `next`
        or `show --new` printed is not reported again); summarized per issue as kinds of change
        and counts. {agent, since, holding, todo_for_you, changed (at most 10: action first, then
        effective priority, then key), more, cursor}. Advances the cursor to the heads read at
        the start (one small write) unless peek; `cursor` is the stored cursor after the call."""
        me = self._need_actor()
        now = timeutil.now()
        now_ts = timeutil.iso(now)
        with self._read() as conn:
            agent = self._actor_row(conn)
            heads = self._heads(conn)
            cursor = conn.execute("SELECT last_event_id, last_comment_id, updated_at FROM cursors "
                                  "WHERE agent = ? AND scope = 'check'", (me,)).fetchone()
            if cursor is None:  # joined before the feed existed: it starts now
                ev, cm, since = heads["event_id"], heads["comment_id"], now_ts
            else:
                ev, cm, since = cursor[0], cursor[1], cursor[2]
            params = {"me": me, "ev": ev, "cm": cm, "evh": heads["event_id"],
                      "cmh": heads["comment_id"]}
            per = self._check_changes(conn, me, params)
            ranked = self._check_ranked(conn, per)
            holding = [{"key": self._key(r[0]), "lease_until": r[1],
                        "lease_left_min": _minutes_left(r[1], now)} for r in conn.execute(
                "SELECT id, lease_until FROM issues WHERE (assignee = ? AND status = 'in_progress') "
                "OR (reviewer = ? AND status = 'in_review') ORDER BY lease_until, id", (me, me))]
            mode = "review" if agent["role"] == "reviewer" else "work"
            sql, args = self._candidates(conn, agent, mode, [], now_ts)
            todo = conn.execute(f"SELECT count(*) {sql}", args).fetchone()[0]
        position = {"event_id": ev, "comment_id": cm}
        moved = cursor is None or (ev, cm) != (heads["event_id"], heads["comment_id"])
        if not peek:
            if moved:
                self._advance_check(me, heads, now_ts)
            position = dict(heads)
        return {"agent": me, "since": since, "holding": holding, "todo_for_you": todo,
                "changed": ranked[:CHECK_LIMIT], "more": max(0, len(ranked) - CHECK_LIMIT),
                "cursor": position}

    def pending_changes(self) -> int:
        """How many issues `check` would list now (read-only: two indexed queries, no write).
        0 without an actor or a check cursor. The CLI prints the dirty hint from it."""
        me = self.actor
        if not me:
            return 0
        row = self.conn.execute("SELECT last_event_id, last_comment_id FROM cursors "
                                "WHERE agent = ? AND scope = 'check'", (me,)).fetchone()
        if row is None:
            return 0
        return self.conn.execute(PENDING_SQL, {"me": me, "ev": row[0], "cm": row[1]}).fetchone()[0]

    def _check_changes(self, conn, me: str, params: dict) -> dict:
        """{issue_id: {changes, comments, questions, files}} from the feed's rows."""
        per: dict[int, dict] = {}

        def entry(issue_id: int) -> dict:
            return per.setdefault(issue_id, {"changes": [], "comments": 0, "questions": 0,
                                             "files": 0, "_status": None, "_fields": {}})

        writes: dict[tuple, list] = {}
        for row in conn.execute(CHECK_EVENTS_SQL, params):
            writes.setdefault((row["issue_id"], row["actor"], row["at"]), []).append(row)
        for (issue_id, _actor, _at), rows in writes.items():
            item = entry(issue_id)
            kinds = [self._check_kind(me, r) for r in rows]
            semantic = any(k and k["kind"] in _SEMANTIC for k in kinds)
            for row, kind in zip(rows, kinds):
                if row["action"] == "attach":
                    item["files"] += 1
                elif kind is None or (kind["kind"] == "status" and semantic):
                    continue
                elif kind["kind"] == "status":
                    if item["_status"] is None:
                        item["_status"] = kind
                        item["changes"].append(kind)
                    else:
                        item["_status"].update(new=kind["new"], by=kind["by"])
                elif kind["kind"] == "field":
                    seen = item["_fields"].get(kind["field"])
                    if seen is None:
                        item["_fields"][kind["field"]] = kind
                        item["changes"].append(kind)
                    else:
                        seen["by"] = kind["by"]
                        if "new" in kind:
                            seen["new"] = kind["new"]
                elif kind not in item["changes"]:
                    item["changes"].append(kind)
        for row in conn.execute(CHECK_COMMENTS_SQL, params):
            item = entry(row["issue_id"])
            item["comments"] += 1
            if row["kind"] == "question" or row["mentioned"]:
                item["questions"] += 1
        for item in per.values():
            del item["_status"], item["_fields"]
        return per

    def _check_kind(self, me: str, row) -> dict | None:
        """One event as a change for `me`: {kind, by, ...}; None for attach (a count)."""
        action, by, old, new = row["action"], row["actor"], row["old"], row["new"]
        if action == "status":
            return {"kind": "status", "old": old, "new": new, "by": by}
        if action == "assignee" and new == me:
            return {"kind": "assigned", "by": by}
        if action == "assignee" and old == me or action == "release":
            return {"kind": "unassigned", "by": by}
        if action == "takeover":
            return {"kind": "takeover", "by": new or by}
        if action == "reject":
            return {"kind": "rejected", "by": by}
        if action == "approve":
            return {"kind": "approved", "by": by}
        if action == "unblocked":
            return {"kind": "unblocked", "by_issue": (old or "").split(" ", 1)[0] or None, "by": by}
        if action == "review_cap":
            return {"kind": "review_cap", "by": by}
        if action == "priority":
            return {"kind": "field", "field": "priority", "old": old, "new": new, "by": by}
        if action in _FIELDS:
            return {"kind": "field", "field": _FIELDS[action], "by": by}
        return None

    def _check_ranked(self, conn, per: dict) -> list[dict]:
        if not per:
            return []
        ids = list(per)
        marks = ",".join("?" * len(ids))
        own = {r[0]: r[1] for r in conn.execute(
            f"SELECT id, priority FROM issues WHERE id IN ({marks})", ids)}
        holds = {r[0]: (r[1], r[2]) for r in conn.execute(
            f"SELECT k.src, min(b.priority), count(*) FROM links k JOIN issues b ON b.id = k.dst "
            f"JOIN issues s ON s.id = k.src WHERE k.kind = 'blocks' AND b.status NOT IN "
            f"('done', 'canceled') AND s.status NOT IN ('done', 'canceled') AND k.src IN ({marks}) "
            f"GROUP BY k.src", ids)}
        out = []
        for issue_id, item in per.items():
            priority = own[issue_id]
            held, blocks_open = holds.get(issue_id, (4, 0))
            effective = min(priority, held)
            action = item["questions"] > 0 or any(c["kind"] in _ACTION_KINDS for c in item["changes"])
            out.append(((not action, effective, issue_id), {
                "key": self._key(issue_id), "priority": model.priority_name(priority),
                "effective_priority": model.priority_name(effective), "blocks_open": blocks_open,
                "action": action, "changes": item["changes"], "comments": item["comments"],
                "questions": item["questions"], "files": item["files"]}))
        out.sort(key=lambda pair: pair[0])
        return [entry for _, entry in out]

    def _advance_check(self, me: str, heads: dict, ts: str) -> None:
        """Move the check cursor forward to heads (never back). One small write."""
        from earmark import db

        with db.write(self.conn) as conn:
            conn.execute(
                "INSERT INTO cursors(agent, scope, last_comment_id, last_event_id, updated_at) "
                "VALUES (?, 'check', ?, ?, ?) ON CONFLICT(agent, scope) DO UPDATE SET "
                "last_comment_id = max(cursors.last_comment_id, excluded.last_comment_id), "
                "last_event_id = max(cursors.last_event_id, excluded.last_event_id), "
                "updated_at = excluded.updated_at",
                (me, heads["comment_id"], heads["event_id"], ts))

    def wait_check(self, mode: str) -> dict | None:
        """mode work|review|changes|any (inbox: an alias of changes). Read-only. Returns
        {reason, ...} when something is available for the actor, else None: {reason: changes,
        count} when its check feed is not empty (pending_changes), {reason: work|review, key,
        title} when `next` would claim something. any = changes, or the role's own mode (review
        for reviewers, work otherwise). The feed, not the inbox: once `check` has moved its
        cursor, a wait blocks until something new arrives. `earmark wait` calls it after
        data_version moves."""
        mode = str(mode or "any").strip().lower()
        mode = WAIT_ALIASES.get(mode, mode)
        if mode not in WAIT_MODES:
            raise InvalidRequest(f"invalid wait mode {mode!r}; use work, review, changes or any")
        self._need_actor()
        with self._read() as conn:
            agent = self._actor_row(conn)
            if mode in ("changes", "any"):
                count = self.pending_changes()
                if count:
                    return {"reason": "changes", "count": count}
                if mode == "changes":
                    return None
                mode = "review" if agent["role"] == "reviewer" else "work"
            _, row = self._pick(conn, agent, mode == "review", [], timeutil.now_iso())
            if row is not None:
                return {"reason": mode, "key": self._key(row["id"]), "title": row["title"]}
        return None
