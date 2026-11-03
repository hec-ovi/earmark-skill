"""Topic tree on materialized paths: subtree filters, rolled-up counts, add/edit/move/remove."""

from __future__ import annotations

from earmark import model
from earmark.errors import Conflict, InvalidRequest
from earmark.service import Tx

TOPIC_COLUMNS = "id, parent_id, slug, path, name, icon, description, position, archived, created_at"


def _node(row, counts: dict) -> dict:
    return {
        "id": row["id"], "path": row["path"], "slug": row["slug"], "name": row["name"],
        "icon": row["icon"], "description": row["description"], "parent_id": row["parent_id"],
        "position": row["position"], "archived": bool(row["archived"]),
        "counts": counts.get(row["path"]) or _zero_counts(), "children": [],
    }


def _zero_counts() -> dict:
    return {"open": 0, "total": 0, "by_status": {s: 0 for s in model.STATUSES}}


class TopicsMixin:
    def topics(self, *, root: str | None = None, depth: int | None = None,
               include_archived: bool = False) -> list[dict]:
        """Root nodes (or [root]) with nested children; counts roll up each whole subtree."""
        if depth is not None and int(depth) < 1:
            raise InvalidRequest("depth must be at least 1")
        with self._read() as conn:
            base = self._topic_row(conn, root)["path"] if root else None
            rows = conn.execute(f"SELECT {TOPIC_COLUMNS} FROM topics ORDER BY length(path) - "
                                f"length(replace(path, '/', '')), position, slug").fetchall()
            counts = self._rolled_counts(conn)
        return self._tree(rows, counts, base, depth, include_archived)

    @staticmethod
    def _rolled_counts(conn) -> dict:
        """{path: counts} where each node counts the issues of its whole subtree."""
        counts: dict[str, dict] = {}
        rows = conn.execute("SELECT t.path, i.status, count(*) FROM issues i JOIN topics t "
                            "ON t.id = i.topic_id GROUP BY t.path, i.status").fetchall()
        for path, status, n in rows:
            for node in model.topic_ancestors(path) + [path]:
                c = counts.setdefault(node, _zero_counts())
                c["total"] += n
                c["by_status"][status] += n
                if status not in model.CLOSED:
                    c["open"] += n
        return counts

    @staticmethod
    def _tree(rows, counts: dict, base: str | None, depth: int | None,
              include_archived: bool) -> list[dict]:
        nodes: dict[int, dict] = {}
        roots: list[dict] = []
        base_depth = base.count("/") if base else 0
        for row in rows:  # parents come before children (ordered by depth)
            path = row["path"]
            if base and path != base and not path.startswith(base + "/"):
                continue
            if depth is not None and path.count("/") - base_depth >= int(depth):
                continue
            if row["archived"] and not include_archived:
                continue
            node = _node(row, counts)
            nodes[row["id"]] = node
            if path == base or (base is None and row["parent_id"] is None):
                roots.append(node)
            elif row["parent_id"] in nodes:
                nodes[row["parent_id"]]["children"].append(node)
        return roots

    def _topic_node(self, conn, topic_id: int) -> dict:
        row = conn.execute(f"SELECT {TOPIC_COLUMNS} FROM topics WHERE id = ?", (topic_id,)).fetchone()
        counts = self._rolled_counts(conn)
        node = _node(row, counts)
        child_rows = conn.execute(f"SELECT {TOPIC_COLUMNS} FROM topics WHERE parent_id = ? "
                                  f"ORDER BY position, slug", (topic_id,)).fetchall()
        node["children"] = [_node(r, counts) for r in child_rows]
        return node

    def add_topic(self, path: str, *, name: str | None = None, icon: str | None = None,
                  description: str | None = None) -> dict:
        path = model.parse_topic_path(path)
        with self._tx() as tx:
            if tx.conn.execute("SELECT 1 FROM topics WHERE path = ?", (path,)).fetchone():
                raise Conflict(f"topic {path!r} already exists", hint=f"earmark topic edit {path}")
            parent_id = self._ensure_ancestors(tx, path)
            topic_id = self._insert_topic(tx, path, parent_id, name=name, icon=icon,
                                          description=description)
            return self._topic_node(tx.conn, topic_id)

    def _ensure_ancestors(self, tx: Tx, path: str) -> int | None:
        parent_id = None
        for ancestor in model.topic_ancestors(path):
            row = tx.conn.execute("SELECT id FROM topics WHERE path = ?", (ancestor,)).fetchone()
            parent_id = row[0] if row else self._insert_topic(tx, ancestor, parent_id)
        return parent_id

    def _insert_topic(self, tx: Tx, path: str, parent_id: int | None, *, name: str | None = None,
                      icon: str | None = None, description: str | None = None) -> int:
        slug = path.rsplit("/", 1)[-1]
        position = tx.conn.execute(
            "SELECT coalesce(max(position) + 1, 0) FROM topics WHERE parent_id IS ?",
            (parent_id,)).fetchone()[0]
        cur = tx.conn.execute(
            "INSERT INTO topics(parent_id, slug, path, name, icon, description, position, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (parent_id, slug, path, (name or "").strip() or slug, (icon or "").strip() or None,
             description or "", position, tx.ts))
        self._event(tx, None, "topic_add", None, path)
        return cur.lastrowid

    def edit_topic(self, ref: str | int, *, name: str | None = None, icon: str | None = None,
                   description: str | None = None, position: int | None = None,
                   archived: bool | None = None) -> dict:
        wanted = {"name": None if name is None else (name.strip() or None),
                  "icon": None if icon is None else (icon.strip() or ""),
                  "description": description,
                  "position": None if position is None else int(position),
                  "archived": None if archived is None else int(model.parse_bool(archived))}
        if name is not None and not name.strip():
            raise InvalidRequest("topic name cannot be empty")
        with self._tx() as tx:
            row = self._topic_row(tx.conn, ref)
            for column, value in wanted.items():
                if value is None:
                    continue
                if column == "icon" and value == "":
                    value = None
                if row[column] != value:
                    tx.conn.execute(f"UPDATE topics SET {column} = ? WHERE id = ?", (value, row["id"]))
                    self._event(tx, None, "topic_edit", f"{row['path']} {column}={row[column]}",
                                f"{row['path']} {column}={value}")
            return self._topic_node(tx.conn, row["id"])

    def move_topic(self, ref: str | int, new_path: str) -> dict:
        new_path = model.parse_topic_path(new_path)
        with self._tx() as tx:
            row = self._topic_row(tx.conn, ref)
            old = row["path"]
            if new_path == old:
                return self._topic_node(tx.conn, row["id"])
            if new_path.startswith(old + "/"):
                raise InvalidRequest(f"cannot move {old!r} inside itself")
            if tx.conn.execute("SELECT 1 FROM topics WHERE path = ?", (new_path,)).fetchone():
                raise Conflict(f"topic {new_path!r} already exists")
            depth_below = tx.conn.execute(
                "SELECT coalesce(max(length(path) - length(replace(path, '/', ''))), 0) FROM topics "
                "WHERE path LIKE ?", (old + "/%",)).fetchone()[0] - old.count("/")
            if new_path.count("/") + 1 + max(depth_below, 0) > model.MAX_TOPIC_DEPTH:
                raise InvalidRequest(f"moving {old!r} to {new_path!r} would exceed "
                                     f"{model.MAX_TOPIC_DEPTH} levels")
            parent_id = self._ensure_ancestors(tx, new_path)
            if parent_id != row["parent_id"]:
                position = tx.conn.execute(
                    "SELECT coalesce(max(position) + 1, 0) FROM topics WHERE parent_id IS ?",
                    (parent_id,)).fetchone()[0]
            else:
                position = row["position"]
            tx.conn.execute("UPDATE topics SET path = ? || substr(path, ?) WHERE path LIKE ?",
                            (new_path, len(old) + 1, old + "/%"))
            tx.conn.execute("UPDATE topics SET path = ?, slug = ?, parent_id = ?, position = ? "
                            "WHERE id = ?", (new_path, new_path.rsplit("/", 1)[-1], parent_id,
                                             position, row["id"]))
            self._event(tx, None, "topic_move", old, new_path)
            return self._topic_node(tx.conn, row["id"])

    def remove_topic(self, ref: str | int, *, archive: bool = False) -> dict:
        with self._tx() as tx:
            row = self._topic_row(tx.conn, ref)
            path = row["path"]
            if archive:
                if not row["archived"]:
                    tx.conn.execute("UPDATE topics SET archived = 1 WHERE id = ?", (row["id"],))
                    self._event(tx, None, "topic_edit", f"{path} archived=0", f"{path} archived=1")
                return {"archived": row["id"], "path": path}
            children = tx.conn.execute("SELECT count(*) FROM topics WHERE parent_id = ?",
                                       (row["id"],)).fetchone()[0]
            issues = tx.conn.execute(
                "SELECT count(*) FROM issues WHERE topic_id IN (SELECT id FROM topics WHERE "
                "path = ? OR path LIKE ?)", (path, path + "/%")).fetchone()[0]
            if children or issues:
                raise InvalidRequest(f"topic {path!r} is not empty ({children} subtopics, "
                                     f"{issues} issues)", hint=f"earmark topic rm {path} --archive",
                                     details={"children": children, "issues": issues})
            tx.conn.execute("DELETE FROM topics WHERE id = ?", (row["id"],))
            self._event(tx, None, "topic_remove", path, None)
            return {"deleted": row["id"], "path": path}
