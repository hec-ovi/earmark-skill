"""Attachments: files on an issue or on one of its comments (docs/ARCHITECTURE.md, Attachments).

Bytes live beside the database in files/<sha[0:2]>/<sha256><ext>, content-addressed and written
once; SQLite holds only the `attachments` row.

Write order. Each input is streamed into a temp file inside files/ (same filesystem, unique
name) while it is hashed, with no lock held. Inside the write transaction the temp file is moved
onto its final name, or dropped when that name already holds the same bytes, and only then is
the row inserted. Files are removed by `_collect`, after the commit that dropped their last row,
in a write transaction of its own: an attach of the same bytes places its file and inserts its
row in one transaction, so it either finds the file gone and puts its own copy in place, or its
row keeps the file. A crash in between leaves at most an unreferenced file (`doctor --fix`).
"""

from __future__ import annotations

import hashlib
import os
import re

from earmark import db, model, views
from earmark.errors import (Busy, Forbidden, InternalError, InvalidRequest, NotFound,
                            PayloadTooLarge)
from earmark.service import Tx

MB = 1024 * 1024
CHUNK = 1024 * 1024
TEMP_PREFIX = ".upload-"
TEMP_SUFFIX = ".part"
STALE_TEMP_S = 3600       # doctor --fix removes upload temp files older than this
FILE_RE = re.compile(r"([0-9a-f]{64})((?:\.[a-z0-9]{1,10})?)")


class Staged:
    """One input streamed into files/ under a temp name: what the row will say about it."""

    __slots__ = ("name", "mime", "ext", "sha256", "size", "temp", "final")

    def __init__(self, name: str, mime: str, ext: str, sha256: str, size: int, temp: str | None,
                 final: str):
        self.name, self.mime, self.ext = name, mime, ext
        self.sha256, self.size, self.temp, self.final = sha256, size, temp, final


def too_large(name: str, limit_mb: int, size: int | None = None) -> PayloadTooLarge:
    what = f"{name} ({size} bytes)" if size is not None else name
    return PayloadTooLarge(f"{what} is over the {limit_mb} MB attachment limit",
                           hint="the owner can raise it: earmark config max_attachment_mb N",
                           details={"limit_mb": limit_mb, "limit_bytes": limit_mb * MB,
                                    "size": size})


def stage(root: str, name: str, stream, limit_mb: int, hint: str | None = None) -> Staged:
    """Stream `stream` (binary, .read(n)) into a temp file in root while hashing it; fsync it.
    Raises PayloadTooLarge past the limit; the temp file is removed on any failure."""
    import tempfile

    clean = model.parse_file_name(name)
    limit = limit_mb * MB
    os.makedirs(root, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=root)
    digest, size = hashlib.sha256(), 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise too_large(clean, limit_mb)
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        remove(temp)
        raise
    sha = digest.hexdigest()
    ext = model.file_ext(clean)
    return Staged(clean, model.file_mime(clean, hint), ext, sha, size, temp,
                  views.file_path(root, sha, ext))


def place(staged: Staged) -> None:
    """Under the write lock: make the final file exist. Identical bytes already there are kept
    (the temp file is dropped after commit); otherwise the temp file is renamed onto the name."""
    if _has(staged.final, staged.size):
        return
    os.makedirs(os.path.dirname(staged.final), exist_ok=True)
    try:
        os.replace(staged.temp, staged.final)
    except OSError as exc:
        # Windows refuses to replace a file another process holds open; it holds these bytes.
        if not _has(staged.final, staged.size):
            raise InternalError(f"cannot store {staged.name}: {exc.strerror or exc}") from None
        return
    staged.temp = None


def discard(staged: list[Staged]) -> None:
    for item in staged:
        if item.temp:
            remove(item.temp)
            item.temp = None


def remove(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except OSError:  # gone already, or (Windows) open elsewhere: doctor --fix finds it later
        return False


def copy_file(src: str, dst: str, size: int) -> bool:
    """Copy src to dst through a temp name (kept when dst already holds `size` bytes).
    False when src is gone."""
    import shutil

    if _has(dst, size):
        return True
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    part = dst + TEMP_SUFFIX
    try:
        shutil.copyfile(src, part)
    except FileNotFoundError:
        remove(part)
        return False
    os.replace(part, dst)
    return True


def _has(path: str, size: int) -> bool:
    try:
        return os.path.getsize(path) == size
    except OSError:
        return False


class AttachmentsMixin:
    # ---- reads --------------------------------------------------------------------------

    def list_attachments(self, key: str) -> list[dict]:
        """[Attachment] of the issue, comment ones included, oldest first."""
        with self._read() as conn:
            issue_id = self._issue_id(conn, key)
            rows = conn.execute(f"SELECT {views.ATTACHMENT_COLUMNS} FROM attachments a "
                                f"WHERE a.issue_id = ? ORDER BY a.id", (issue_id,)).fetchall()
            return views.attachments(rows, self.prefix, self.files_dir)

    def get_attachment(self, attachment_id: str | int) -> dict:
        with self._read() as conn:
            return self._attachment(conn, model.parse_attachment_id(attachment_id))

    def _attachment(self, conn, attachment_id: int) -> dict:
        row = conn.execute(f"SELECT {views.ATTACHMENT_COLUMNS} FROM attachments a WHERE a.id = ?",
                           (attachment_id,)).fetchone()
        if row is None:
            raise NotFound(f"no attachment @{attachment_id}", hint="earmark attachments KEY")
        return views.attachment(row, self.prefix, self.files_dir)

    def save_attachment(self, attachment_id: str | int, dest: str) -> dict:
        """Copy the file to dest (a new file, or a directory: dest/<name>), checking its sha256.
        {attachment, path, bytes}."""
        item = self.get_attachment(attachment_id)
        target = os.path.abspath(os.path.expanduser(dest))
        if os.path.isdir(target):
            target = os.path.join(target, item["name"])
        if os.path.exists(target):
            raise InvalidRequest(f"{target} already exists; choose a new path")
        if not os.path.isdir(os.path.dirname(target)):
            raise InvalidRequest(f"directory {os.path.dirname(target)} does not exist")
        digest = hashlib.sha256()
        try:
            src = open(item["path"], "rb")
        except FileNotFoundError:
            raise self._missing(item) from None
        with src, open(target, "xb") as out:
            while chunk := src.read(CHUNK):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != item["sha256"]:
            remove(target)
            raise InternalError(f"@{item['id']} does not match its sha256 on disk "
                                f"({item['path']})", hint="earmark doctor")
        return {"attachment": item, "path": target, "bytes": os.path.getsize(target)}

    @staticmethod
    def _missing(item: dict) -> NotFound:
        return NotFound(f"the file of @{item['id']} ({item['name']}) is missing from {item['path']}",
                        hint="earmark doctor")

    # ---- writes -------------------------------------------------------------------------

    def attach(self, key: str, files, *, comment: str | int | None = None) -> list[dict]:
        """Attach files to an issue, or to one of its comments. `files` holds paths, or
        (name, binary stream) / (name, binary stream, mime hint) tuples. One write for all of
        them; returns [Attachment] in the order given. Any joined actor; renews the holder's
        lease and moves updated_at (not version)."""
        staged = self._prepare(key, files, comment=comment)
        try:
            with self._tx() as tx:
                row = self._issue_row(tx.conn, key)
                comment_id = None
                if comment is not None:
                    comment_id = self._attachment_comment(tx.conn, row["id"], comment)
                ids = self._insert_attachments(tx, row["id"], comment_id, staged)
                self._apply(tx, row["id"], self._renewal(tx, row), bump=False)
                rows = tx.conn.execute(
                    f"SELECT {views.ATTACHMENT_COLUMNS} FROM attachments a WHERE a.id IN "
                    f"({','.join('?' * len(ids))}) ORDER BY a.id", ids).fetchall()
                return views.attachments(rows, self.prefix, self.files_dir)
        finally:
            discard(staged)

    def _comment_with_files(self, key: str, body: str, kind: str, reply_to, files) -> dict:
        """add_comment(..., attach=files): the comment and its files in one write."""
        staged = self._prepare(key, files, reply_to=reply_to)
        try:
            with self._tx() as tx:
                row = self._issue_row(tx.conn, key)
                if reply_to is not None:
                    self._check_reply(tx.conn, row["id"], reply_to)
                comment_id = self._insert_comment(tx, row["id"], body, kind, reply_to)
                self._insert_attachments(tx, row["id"], comment_id, staged)
                self._apply(tx, row["id"], self._renewal(tx, row), bump=False)
                return self._comment(tx.conn, comment_id)
        finally:
            discard(staged)

    def detach(self, attachment_id: str | int) -> dict:
        """Remove an attachment (its author, the owner or a pm); the file goes after commit
        when no other row references it. {deleted, issue, name}."""
        wanted = model.parse_attachment_id(attachment_id)
        with self._tx() as tx:
            row = tx.conn.execute("SELECT * FROM attachments WHERE id = ?", (wanted,)).fetchone()
            if row is None:
                raise NotFound(f"no attachment @{wanted}", hint="earmark attachments KEY")
            if row["author"] != tx.actor and not tx.privileged:
                raise Forbidden(f"@{wanted} was attached by {row['author']}; only they, the owner "
                                f"or a pm can detach it")
            issue = self._issue_row(tx.conn, row["issue_id"])
            tx.conn.execute("DELETE FROM attachments WHERE id = ?", (wanted,))
            self._event(tx, issue["id"], "detach", f"@{wanted} {row['name']}", None)
            self._apply(tx, issue["id"], self._renewal(tx, issue), bump=False)
        self._collect([(row["sha256"], row["ext"])])
        return {"deleted": wanted, "issue": self._key(row["issue_id"]), "name": row["name"]}

    # ---- internals ----------------------------------------------------------------------

    def _prepare(self, key: str, files, *, comment=None, reply_to=None) -> list[Staged]:
        """Fail fast on identity, the issue, the comment and the files, then stage every file."""
        self._need_actor()
        sources = [_source(item) for item in (files or ())]
        if not sources:
            raise InvalidRequest("no files to attach", hint="earmark attach KEY FILE...")
        with self._read() as conn:
            self._actor_row(conn)
            issue_id = self._issue_id(conn, key)
            if comment is not None:
                self._attachment_comment(conn, issue_id, comment)
            if reply_to is not None:
                self._check_reply(conn, issue_id, reply_to)
        limit_mb = self.max_attachment_mb
        for name, _, size, _ in sources:
            if size is not None and size > limit_mb * MB:
                raise too_large(model.parse_file_name(name), limit_mb, size)
        staged: list[Staged] = []
        try:
            for name, opener, _, hint in sources:
                stream, close = opener()
                try:
                    staged.append(stage(self.files_dir, name, stream, limit_mb, hint))
                finally:
                    if close:
                        stream.close()
        except BaseException:
            discard(staged)
            raise
        return staged

    def _attachment_comment(self, conn, issue_id: int, comment) -> int:
        try:
            comment_id = int(str(comment).strip().lstrip("#"))
        except ValueError:
            raise InvalidRequest(f"invalid comment id {comment!r}; use 1190 or #1190") from None
        found = conn.execute("SELECT issue_id FROM comments WHERE id = ?", (comment_id,)).fetchone()
        if found is None:
            raise NotFound(f"no comment #{comment_id}", hint=f"earmark comments {self._key(issue_id)}")
        if found[0] != issue_id:
            raise InvalidRequest(f"comment #{comment_id} is on {self._key(found[0])}, not "
                                 f"{self._key(issue_id)}",
                                 hint=f"earmark attach {self._key(found[0])} FILE --comment {comment_id}")
        return comment_id

    def _insert_attachments(self, tx: Tx, issue_id: int, comment_id: int | None,
                            staged: list[Staged]) -> list[int]:
        ids = []
        for item in staged:
            place(item)
            cur = tx.conn.execute(
                "INSERT INTO attachments(issue_id, comment_id, name, mime, size, sha256, ext, "
                "author, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (issue_id, comment_id, item.name, item.mime, item.size, item.sha256, item.ext,
                 tx.actor, tx.ts))
            ids.append(cur.lastrowid)
            self._event(tx, issue_id, "attach", None, f"@{cur.lastrowid} {item.name}")
        return ids

    def _collect(self, files) -> list[str]:
        """After commit: remove each (sha256, ext) file that no row references any more, holding
        the write lock (see the module docstring). Busy leaves them for `doctor --fix`."""
        wanted = sorted(set(files))
        removed: list[str] = []
        if not wanted:
            return removed
        try:
            with db.write(self.conn) as conn:
                for sha, ext in wanted:
                    if conn.execute("SELECT 1 FROM attachments WHERE sha256 = ? AND ext = ? LIMIT 1",
                                    (sha, ext)).fetchone():
                        continue
                    path = views.file_path(self.files_dir, sha, ext)
                    if remove(path):
                        removed.append(path)
        except Busy:
            pass
        return removed

    def _file_checks(self, fix: bool = False) -> list[dict]:
        """doctor: every row has its file; no unreferenced files or stale upload temps. With
        fix, unreferenced files and stale temps are removed under the write lock."""
        if fix:
            with db.write(self.conn) as conn:
                return self._scan_files(conn, fix=True)
        return self._scan_files(self.conn, fix=False)

    def _scan_files(self, conn, *, fix: bool) -> list[dict]:
        import time

        root = self.files_dir
        rows = conn.execute("SELECT id, name, sha256, ext, size FROM attachments ORDER BY id").fetchall()
        on_disk: dict[tuple[str, str], tuple[str, int]] = {}
        temps: list[tuple[str, int]] = []
        cutoff = time.time() - STALE_TEMP_S
        for entry in _entries(root):
            if entry.is_dir(follow_symlinks=False) and len(entry.name) == 2:
                for item in _entries(entry.path):
                    match = FILE_RE.fullmatch(item.name)
                    if match and match.group(1)[:2] == entry.name and item.is_file(follow_symlinks=False):
                        on_disk[(match.group(1), match.group(2))] = (item.path, item.stat().st_size)
            elif (entry.name.startswith(TEMP_PREFIX) and entry.name.endswith(TEMP_SUFFIX)
                  and entry.is_file(follow_symlinks=False)):
                stat = entry.stat()
                if stat.st_mtime < cutoff:
                    temps.append((entry.path, stat.st_size))
        referenced = {(r["sha256"], r["ext"]) for r in rows}
        missing = [r for r in rows if on_disk.get((r["sha256"], r["ext"]), ("", -1))[1] != r["size"]]
        extra = [on_disk[k] for k in sorted(set(on_disk) - referenced)] + temps
        stored = sum(size for k, (_, size) in on_disk.items() if k in referenced)
        files_ok = not missing
        if missing:
            shown = ", ".join(f"@{r['id']} {r['name']}" for r in missing[:5])
            more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
            files_detail = f"{len(missing)} of {len(rows)} attachments have no file: {shown}{more}"
        else:
            files_detail = f"{len(rows)} attachments, {len(referenced)} files, {stored} bytes in {root}"
        extra_bytes = sum(size for _, size in extra)
        if not extra:
            extra_ok, extra_detail = True, "none"
        elif fix:
            gone = [(path, size) for path, size in extra if remove(path)]
            left = len(extra) - len(gone)
            extra_ok = not left
            extra_detail = (f"removed {len(gone)} unreferenced files "
                            f"({sum(size for _, size in gone)} bytes)"
                            + (f"; {left} could not be removed" if left else ""))
        else:
            extra_ok = False
            extra_detail = (f"{len(extra)} files ({extra_bytes} bytes) not referenced by any "
                            f"attachment; earmark doctor --fix removes them")
        return [{"name": "attachments", "ok": files_ok, "detail": files_detail},
                {"name": "unreferenced_files", "ok": extra_ok, "detail": extra_detail}]


def _entries(path: str) -> list:
    try:
        with os.scandir(path) as it:
            return sorted(it, key=lambda e: e.name)
    except (FileNotFoundError, NotADirectoryError):
        return []


def _source(item) -> tuple:
    """(name, opener, size or None, mime hint) for a path or a (name, stream[, hint]) tuple.
    opener() returns (stream, close_it)."""
    if isinstance(item, tuple):
        if len(item) not in (2, 3):
            raise InvalidRequest("attachments are paths or (name, stream[, mime]) tuples")
        name, stream = item[0], item[1]
        hint = item[2] if len(item) == 3 else None
        return str(name), (lambda: (stream, False)), None, hint
    path = os.fspath(item)
    try:
        stat = os.stat(path)
    except OSError as exc:
        raise InvalidRequest(f"cannot read {path}: {exc.strerror or exc}") from None
    import stat as stat_mod

    if not stat_mod.S_ISREG(stat.st_mode):
        raise InvalidRequest(f"{path} is not a regular file")

    def opener():
        try:
            return open(path, "rb"), True
        except OSError as exc:
            raise InvalidRequest(f"cannot read {path}: {exc.strerror or exc}") from None

    return os.path.basename(path), opener, stat.st_size, None
