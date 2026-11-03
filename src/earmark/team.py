"""Profiles, join/leave, presence, whoami, team, triage and wait_check."""

from __future__ import annotations

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing stays off the startup path
    from typing import Any, Iterable

from earmark import model, seed, timeutil, views
from earmark.errors import Conflict, InvalidRequest, NotFound
from earmark.service import Tx

PROFILE_FIELDS = ("name", "role", "icon", "prompt", "topics")
TRIAGE_LIMIT = 100


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
        slug = model.parse_slug(slug)
        with self._tx() as tx:
            self._require_privileged(tx, "manage profiles")
            self._profile(tx.conn, slug)
            tx.conn.execute("DELETE FROM profiles WHERE slug = ?", (slug,))
            self._event(tx, None, "profile_remove", slug, None)
        return {"deleted": slug}

    # ---- join, leave, whoami ------------------------------------------------------------

    def join(self, profile: str, *, handle: str | None = None, cli: str | None = None) -> dict:
        """Briefing: {agent, profile, scope, assigned, inbox_new, loop, board}."""
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
            me = wanted or self._free_handle(conn, slug)
            existing = conn.execute("SELECT handle FROM agents WHERE handle = ?", (me,)).fetchone()
            if existing:
                conn.execute("UPDATE agents SET profile = ?, cli = coalesce(?, cli), online = 1, "
                             "last_seen = ? WHERE handle = ?", (slug, cli, ts, me))
            else:
                conn.execute("INSERT INTO agents(handle, kind, profile, cli, online, joined_at, "
                             "last_seen) VALUES (?, 'agent', ?, ?, 1, ?, ?)", (me, slug, cli, ts, ts))
            tx = Tx(conn, me, now, ts, None, me == self.owner, self.lease_minutes, self.require_review)
            self._event(tx, None, "join", None, slug)
        self.set_actor(me)
        return self._briefing(me)

    @staticmethod
    def _free_handle(conn, slug: str) -> str:
        """Smallest n with <profile>-<n> unused or offline."""
        taken = {r[0] for r in conn.execute("SELECT handle FROM agents WHERE online = 1")}

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
        self._seen_checked = None  # offline now: the next command by this agent brings it back
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
        overdue, idle_agents, counts}. Lists hold IssueSummary (Agent for idle_agents), at most
        `limit` each, most urgent (effective priority) first; counts are the totals. offline:
        open work assigned to agents that have left (anyone in scope may claim it).
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
                        f"EXISTS ({views.OPEN_BLOCKER}))", [], urgent),
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

    def wait_check(self, mode: str) -> dict | None:
        """mode work|review|inbox|any. Read-only. Returns {reason, ...} when something is
        available for the actor, else None. `earmark wait` calls it after data_version moves."""
        mode = str(mode or "any").strip().lower()
        if mode not in ("work", "review", "inbox", "any"):
            raise InvalidRequest(f"invalid wait mode {mode!r}; use work, review, inbox or any")
        self._need_actor()
        with self._read() as conn:
            agent = self._actor_row(conn)
            if mode in ("inbox", "any"):
                count = self._inbox_count(conn, agent["handle"])
                if count:
                    return {"reason": "inbox", "count": count}
            for kind in ("work", "review"):
                if mode in (kind, "any"):
                    _, row = self._pick(conn, agent, kind == "review", [], timeutil.now_iso())
                    if row is not None:
                        return {"reason": kind, "key": self._key(row["id"]), "title": row["title"]}
        return None
