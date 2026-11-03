"""Row → dict builders for the shapes in docs/HTTP_API.md. Related data is fetched in batches
(one query per relation per 500 ids), never per row."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing stays off the startup path
    from typing import Iterable, Sequence

from earmark import model, timeutil
from earmark.db import chunks

SUMMARY_COLUMNS = (
    "i.id, i.title, i.status, i.priority, i.type, t.path AS topic, i.assignee, i.reviewer, "
    "i.reporter, i.due_at, i.parent_id, i.lease_until, i.rank, i.version, i.created_at, i.updated_at"
)
DETAIL_COLUMNS = SUMMARY_COLUMNS + ", i.body, i.started_at, i.review_requested_at, i.resolved_at"
ISSUE_FROM = "issues i LEFT JOIN topics t ON t.id = i.topic_id"
OPEN_BLOCKER = ("SELECT 1 FROM links k JOIN issues b ON b.id = k.src WHERE k.dst = i.id "
                "AND k.kind = 'blocks' AND b.status NOT IN ('done', 'canceled')")
ACTIVE_MINUTES = 15


def holder(row) -> str | None:
    """The assignee while in_progress, the reviewer while in_review, else nobody."""
    if row["status"] == "in_progress":
        return row["assignee"]
    if row["status"] == "in_review":
        return row["reviewer"]
    return None


def is_stale(row, now_ts: str) -> bool:
    lease = row["lease_until"]
    return bool(holder(row) and lease and lease < now_ts)


def stale_sql(now_param: str = "?") -> str:
    return ("(((i.status = 'in_progress' AND i.assignee IS NOT NULL) OR "
            "(i.status = 'in_review' AND i.reviewer IS NOT NULL)) "
            f"AND i.lease_until IS NOT NULL AND i.lease_until < {now_param})")


def _in_marks(ids: Sequence) -> str:
    return ",".join("?" * len(ids))


def _batch(conn: sqlite3.Connection, sql: str, ids: list) -> Iterable[sqlite3.Row]:
    for part in chunks(ids):
        yield from conn.execute(sql.format(marks=_in_marks(part)), part)


def issue_extras(conn: sqlite3.Connection, ids: list[int]) -> dict:
    """labels, children counts, comment counts/last ids and open-blocker flags for ids."""
    labels: dict[int, list[str]] = {}
    for row in _batch(conn, "SELECT issue_id, label FROM labels WHERE issue_id IN ({marks}) "
                            "ORDER BY issue_id, label", ids):
        labels.setdefault(row[0], []).append(row[1])
    children = {r[0]: r[1] for r in _batch(
        conn, "SELECT parent_id, count(*) FROM issues WHERE parent_id IN ({marks}) "
              "GROUP BY parent_id", ids)}
    comments = {r[0]: (r[1], r[2]) for r in _batch(
        conn, "SELECT issue_id, count(*), max(id) FROM comments WHERE issue_id IN ({marks}) "
              "GROUP BY issue_id", ids)}
    blocked = {r[0] for r in _batch(
        conn, "SELECT DISTINCT k.dst FROM links k JOIN issues b ON b.id = k.src "
              "WHERE k.kind = 'blocks' AND b.status NOT IN ('done', 'canceled') "
              "AND k.dst IN ({marks})", ids)}
    return {"labels": labels, "children": children, "comments": comments, "blocked": blocked}


def summary(row, prefix: str, extras: dict, now_ts: str) -> dict:
    issue_id = row["id"]
    count, last = extras["comments"].get(issue_id, (0, None))
    return {
        "key": model.format_key(prefix, issue_id),
        "id": issue_id,
        "title": row["title"],
        "status": row["status"],
        "priority": model.priority_name(row["priority"]),
        "type": row["type"],
        "topic": row["topic"],
        "assignee": row["assignee"],
        "reviewer": row["reviewer"],
        "reporter": row["reporter"],
        "labels": extras["labels"].get(issue_id, []),
        "due": row["due_at"],
        "parent": model.format_key(prefix, row["parent_id"]),
        "children": extras["children"].get(issue_id, 0),
        "blocked": issue_id in extras["blocked"],
        "stale": is_stale(row, now_ts),
        "lease_until": row["lease_until"],
        "rank": float(row["rank"]),
        "version": row["version"],
        "comment_count": count,
        "last_comment_id": last,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def summaries_from_rows(conn: sqlite3.Connection, prefix: str, rows: list, now_ts: str) -> list[dict]:
    if not rows:
        return []
    extras = issue_extras(conn, [r["id"] for r in rows])
    return [summary(r, prefix, extras, now_ts) for r in rows]


def summaries(conn: sqlite3.Connection, prefix: str, ids: Iterable[int], now_ts: str) -> list[dict]:
    """IssueSummary dicts for ids, in the order given (missing ids are skipped)."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    rows = {r["id"]: r for r in _batch(
        conn, f"SELECT {SUMMARY_COLUMNS} FROM {ISSUE_FROM} WHERE i.id IN ({{marks}})", ids)}
    ordered = [rows[i] for i in ids if i in rows]
    return summaries_from_rows(conn, prefix, ordered, now_ts)


def issue_detail(conn: sqlite3.Connection, prefix: str, issue_id: int, now_ts: str) -> dict | None:
    row = conn.execute(f"SELECT {DETAIL_COLUMNS} FROM {ISSUE_FROM} WHERE i.id = ?",
                       (issue_id,)).fetchone()
    if row is None:
        return None
    data = summary(row, prefix, issue_extras(conn, [issue_id]), now_ts)
    data.update(body=row["body"], started_at=row["started_at"],
                review_requested_at=row["review_requested_at"], resolved_at=row["resolved_at"])
    child_ids = [r[0] for r in conn.execute(
        "SELECT id FROM issues WHERE parent_id = ? ORDER BY rank, id", (issue_id,))]
    data["children"] = summaries(conn, prefix, child_ids, now_ts)
    data["links"] = issue_links(conn, prefix, issue_id)
    return data


def issue_links(conn: sqlite3.Connection, prefix: str, issue_id: int) -> dict:
    links = {"blocks": [], "blocked_by": [], "duplicates": [], "duplicated_by": [], "relates": []}
    rows = conn.execute("SELECT src, dst, kind FROM links WHERE src = ? OR dst = ? "
                        "ORDER BY src, dst", (issue_id, issue_id)).fetchall()
    for src, dst, kind in rows:
        outgoing = src == issue_id
        other = dst if outgoing else src
        if kind == "relates":
            name = "relates"
        elif kind == "blocks":
            name = "blocks" if outgoing else "blocked_by"
        else:
            name = "duplicates" if outgoing else "duplicated_by"
        links[name].append(other)
    return {name: [model.format_key(prefix, i) for i in sorted(set(ids))]
            for name, ids in links.items()}


# ---- comments, events -------------------------------------------------------------------

COMMENT_COLUMNS = "c.id, c.issue_id, c.author, c.kind, c.body, c.reply_to, c.created_at, c.edited_at"


def comments(conn: sqlite3.Connection, prefix: str, rows: list) -> list[dict]:
    ids = [r["id"] for r in rows]
    mentioned: dict[int, list[str]] = {}
    for row in _batch(conn, "SELECT comment_id, handle FROM mentions WHERE comment_id IN ({marks}) "
                            "ORDER BY rowid", ids):
        mentioned.setdefault(row[0], []).append(row[1])
    return [{
        "id": r["id"],
        "issue": model.format_key(prefix, r["issue_id"]),
        "author": r["author"],
        "kind": r["kind"],
        "body": r["body"],
        "reply_to": r["reply_to"],
        "mentions": mentioned.get(r["id"], []),
        "created_at": r["created_at"],
        "edited_at": r["edited_at"],
    } for r in rows]


def event(row, prefix: str) -> dict:
    return {"id": row["id"], "issue": model.format_key(prefix, row["issue_id"]),
            "actor": row["actor"], "action": row["action"], "old": row["old"],
            "new": row["new"], "at": row["at"]}


# ---- team -------------------------------------------------------------------------------

def presence(online: int, last_seen: str, now) -> str:
    if not online:
        return "offline"
    cutoff = timeutil.iso(now - timedelta(minutes=ACTIVE_MINUTES))
    return "active" if last_seen >= cutoff else "idle"


def agents(conn: sqlite3.Connection, prefix: str, rows: list, now) -> list[dict]:
    """Agent dicts with working/reviewing/assigned issue summaries (one batch for all agents)."""
    handles = [r["handle"] for r in rows]
    now_ts = timeutil.iso(now)
    work: dict[str, dict[str, list[int]]] = {h: {"working": [], "reviewing": [], "assigned": []}
                                             for h in handles}
    issue_rows = []
    if handles:
        for part in chunks(handles):
            marks = _in_marks(part)
            issue_rows += conn.execute(
                f"SELECT id, status, assignee, reviewer FROM issues "
                f"WHERE status NOT IN ('done', 'canceled') "
                f"AND (assignee IN ({marks}) OR reviewer IN ({marks})) ORDER BY priority, rank, id",
                part + part).fetchall()
    for r in issue_rows:
        if r["status"] == "in_progress" and r["assignee"] in work:
            work[r["assignee"]]["working"].append(r["id"])
        elif r["assignee"] in work:
            work[r["assignee"]]["assigned"].append(r["id"])
        if r["status"] == "in_review" and r["reviewer"] in work:
            work[r["reviewer"]]["reviewing"].append(r["id"])
    by_id = {s["id"]: s for s in summaries(conn, prefix, [r["id"] for r in issue_rows], now_ts)}
    out = []
    for r in rows:
        lists = work[r["handle"]]
        out.append({
            "handle": r["handle"],
            "kind": r["kind"],
            "profile": r["profile"],
            "role": r["role"],
            "cli": r["cli"],
            "online": bool(r["online"]),
            "presence": presence(r["online"], r["last_seen"], now),
            "joined_at": r["joined_at"],
            "last_seen": r["last_seen"],
            **{name: [by_id[i] for i in ids if i in by_id] for name, ids in lists.items()},
        })
    return out


AGENT_SELECT = ("SELECT a.handle, a.kind, a.profile, p.role, a.cli, a.online, a.joined_at, "
                "a.last_seen FROM agents a LEFT JOIN profiles p ON p.slug = a.profile")


def profiles(conn: sqlite3.Connection, rows: list) -> list[dict]:
    slugs = [r["slug"] for r in rows]
    topics: dict[str, list[str]] = {}
    members: dict[str, list[str]] = {}
    for row in _batch(conn, "SELECT pt.profile, t.path FROM profile_topics pt "
                            "JOIN topics t ON t.id = pt.topic_id WHERE pt.profile IN ({marks}) "
                            "ORDER BY t.path", slugs):
        topics.setdefault(row[0], []).append(row[1])
    for row in _batch(conn, "SELECT profile, handle FROM agents WHERE profile IN ({marks}) "
                            "ORDER BY handle", slugs):
        members.setdefault(row[0], []).append(row[1])
    return [{
        "slug": r["slug"], "name": r["name"], "role": r["role"], "icon": r["icon"],
        "prompt": r["prompt"], "topics": topics.get(r["slug"], []), "version": r["version"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
        "agents": members.get(r["slug"], []),
    } for r in rows]
