"""Attachments: content-addressed storage, dedupe, limits, names, kinds, comment links, events,
lease renewal, permissions, removal after commit, backup, export, doctor --fix, two processes."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import time
import unittest

from tests.support import BoardCase

from earmark import model, views
from earmark.errors import (Forbidden, IdentityRequired, InvalidRequest, NotFound, NotJoined,
                            PayloadTooLarge)

ATTACHMENT_KEYS = {"id", "issue", "comment_id", "name", "mime", "kind", "size", "sha256", "author",
                   "created_at", "url", "path"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 56


class AttachCase(BoardCase):
    def write(self, name: str, data: bytes) -> str:
        folder = os.path.join(self.tmp, "src")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def files_on_disk(self) -> list[str]:
        root = self.board.files_dir
        out = []
        for dirpath, _, names in os.walk(root):
            out += [os.path.relpath(os.path.join(dirpath, n), root) for n in names]
        return sorted(out)


class Storing(AttachCase):
    def test_shape_path_and_bytes(self):
        key = self.issue("a")["key"]
        [att] = self.board.attach(key, [self.write("eye-picker-bug.png", PNG)])
        sha = hashlib.sha256(PNG).hexdigest()
        self.assertEqual(set(att), ATTACHMENT_KEYS)
        self.assertEqual((att["id"], att["issue"], att["comment_id"], att["name"], att["mime"],
                          att["kind"], att["size"], att["sha256"], att["author"], att["url"]),
                         (1, key, None, "eye-picker-bug.png", "image/png", "image", len(PNG), sha,
                          "owner", "/api/attachments/1/raw"))
        root = os.path.join(self.tmp, ".earmark", "files")
        self.assertEqual(att["path"], os.path.join(root, sha[:2], sha + ".png"))
        self.assertTrue(os.path.isabs(att["path"]))
        with open(att["path"], "rb") as fh:
            self.assertEqual(fh.read(), PNG)
        self.assertEqual(self.files_on_disk(), [os.path.join(sha[:2], sha + ".png")])
        self.assertEqual(self.board.get_attachment("@1"), att)
        self.assertEqual(self.board.get_attachment(1), att)
        self.assertEqual(self.board.list_attachments(key), [att])

    def test_init_creates_files_dir_and_v1_has_the_table(self):
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, ".earmark", "files")))
        names = {r[0] for r in self.board.conn.execute("SELECT name FROM sqlite_master")}
        self.assertTrue({"attachments", "attachments_issue", "attachments_comment",
                         "attachments_sha"} <= names)
        self.assertEqual(self.board.meta["max_attachment_mb"], "50")

    def test_identical_bytes_share_one_file(self):
        a, b = self.issue("a")["key"], self.issue("b")["key"]
        first = self.board.attach(a, [self.write("one.log", b"same\n")])[0]
        second = self.board.attach(b, [self.write("two.log", b"same\n")])[0]
        self.assertEqual(first["path"], second["path"])
        self.assertEqual(len(self.files_on_disk()), 1)
        other_ext = self.board.attach(b, [self.write("three.txt", b"same\n")])[0]
        self.assertNotEqual(other_ext["path"], first["path"])
        self.assertEqual(len(self.files_on_disk()), 2)

    def test_streams_and_several_files_in_one_write(self):
        key = self.issue("a")["key"]
        events = len(self.board.issue_events(key))
        out = self.board.attach(key, [("notes.txt", io.BytesIO(b"hello")),
                                      ("clip", io.BytesIO(b"RIFF"), "audio/wav"),
                                      self.write("empty.bin", b"")])
        self.assertEqual([(a["name"], a["kind"], a["size"]) for a in out],
                         [("notes.txt", "text", 5), ("clip", "audio", 4), ("empty.bin", "file", 0)])
        self.assertEqual(out[1]["mime"], "audio/wav")
        self.assertTrue(out[1]["path"].endswith(out[1]["sha256"]))  # no extension, no suffix
        new = self.board.issue_events(key)[events:]
        self.assertEqual([(e["action"], e["new"]) for e in new],
                         [("attach", "@1 notes.txt"), ("attach", "@2 clip"), ("attach", "@3 empty.bin")])

    def test_no_temp_files_are_left(self):
        key = self.issue("a")["key"]
        self.board.attach(key, [("a.txt", io.BytesIO(b"x")), ("b.txt", io.BytesIO(b"x"))])
        self.assertEqual(self.files_on_disk(), [os.path.join(*_shard(b"x", ".txt"))])

    def test_summary_and_detail_counts(self):
        key = self.issue("a")["key"]
        self.issue("b")
        comment = self.board.add_comment(key, "see file")
        self.board.attach(key, [("a.png", io.BytesIO(PNG))])
        self.board.attach(key, [("b.txt", io.BytesIO(b"t"))], comment=comment["id"])
        counts = {i["key"]: i["attachment_count"] for i in self.board.list_issues()["items"]}
        self.assertEqual(counts, {key: 2, "GAME-2": 0})
        issue = self.board.get_issue(key, comments=5)
        self.assertEqual([a["name"] for a in issue["attachments"]], ["a.png", "b.txt"])
        self.assertEqual(issue["attachment_count"], 2)
        self.assertEqual([a["name"] for a in issue["comments"][0]["attachments"]], ["b.txt"])
        listed = self.board.list_comments(key)["items"][0]
        self.assertEqual([a["id"] for a in listed["attachments"]], [2])


class Names(AttachCase):
    def test_base_name_only_and_caps(self):
        key = self.issue("a")["key"]
        cases = [("../../etc/passwd", "passwd"), ("C:\\Users\\x\\shot.PNG", "shot.PNG"),
                 ("dir/sub/a b.txt", "a b.txt"), ("tab\there.txt", "tab_here.txt"),
                 ("  spaced.md ", "spaced.md")]
        for raw, want in cases:
            with self.subTest(raw=raw):
                self.assertEqual(self.board.attach(key, [(raw, io.BytesIO(b"x"))])[0]["name"], want)
        long = "n" * 300 + ".png"
        att = self.board.attach(key, [(long, io.BytesIO(PNG))])[0]
        self.assertEqual(len(att["name"]), 255)
        self.assertTrue(att["name"].endswith("n.png"))
        for bad in ("", "  ", "dir/", "..", "a/."):
            with self.subTest(bad=bad), self.assertRaises(InvalidRequest):
                self.board.attach(key, [(bad, io.BytesIO(b"x"))])

    def test_extensions_and_kinds(self):
        self.assertEqual(model.file_ext("Shot.PNG"), ".png")
        self.assertEqual(model.file_ext("archive.tar.gz"), ".gz")
        for name in ("noext", ".bashrc", "weird.ex-t", "long.abcdefghijk", "x.", "é.ünï"):
            self.assertEqual(model.file_ext(name), "", name)
        kinds = {"a.png": "image", "b.jpg": "image", "c.svg": "image", "d.wav": "audio",
                 "e.ogg": "audio", "f.mp4": "video", "g.pdf": "pdf", "h.txt": "text",
                 "i.log": "text", "j.json": "text", "k.md": "text", "l.zip": "file",
                 "m.bin": "file", "n": "file", "o.tar.gz": "file"}
        for name, kind in kinds.items():
            with self.subTest(name=name):
                self.assertEqual(model.attachment_kind(model.file_mime(name)), kind)
        self.assertEqual(model.file_mime("o.tar.gz"), "application/gzip")
        self.assertEqual(model.file_mime("n"), "application/octet-stream")
        self.assertEqual(model.file_mime("n", "Image/PNG; x=1"), "image/png")
        self.assertEqual(model.file_mime("n", "bad type\r\nX: y"), "application/octet-stream")
        self.assertEqual(model.file_mime("a.png", "text/html"), "image/png")  # the name wins


class Limits(AttachCase):
    def test_limit_from_config(self):
        key = self.issue("a")["key"]
        self.board.config("max_attachment_mb", "1")
        big = self.write("big.bin", b"\0" * (1024 * 1024 + 1))
        with self.assertRaises(PayloadTooLarge) as cm:
            self.board.attach(key, [big])
        self.assertEqual(cm.exception.http_status, 413)
        self.assertEqual(cm.exception.code, "invalid_request")
        self.assertEqual(cm.exception.details["limit_mb"], 1)
        self.assertIn("max_attachment_mb", cm.exception.hint)
        with self.assertRaises(PayloadTooLarge):  # streams are capped while they are read
            self.board.attach(key, [("big.bin", io.BytesIO(b"\0" * (1024 * 1024 + 1)))])
        self.assertEqual(self.files_on_disk(), [])
        self.assertEqual(self.board.list_attachments(key), [])
        exact = self.board.attach(key, [("ok.bin", io.BytesIO(b"\0" * 1024 * 1024))])[0]
        self.assertEqual(exact["size"], 1024 * 1024)

    def test_bad_inputs_leave_nothing(self):
        key = self.issue("a")["key"]
        with self.assertRaises(InvalidRequest):
            self.board.attach(key, [os.path.join(self.tmp, "nope.png")])
        with self.assertRaises(InvalidRequest):
            self.board.attach(key, [self.tmp])  # a directory
        with self.assertRaises(InvalidRequest):
            self.board.attach(key, [])
        with self.assertRaises(NotFound):
            self.board.attach("GAME-9", [("a.txt", io.BytesIO(b"x"))])

        class Broken(io.RawIOBase):
            def read(self, n=-1):
                raise OSError("disk went away")

        with self.assertRaises(OSError):
            self.board.attach(key, [("a.txt", Broken())])
        self.assertEqual(self.files_on_disk(), [])


class Linking(AttachCase):
    def test_comment_must_be_on_the_same_issue(self):
        a, b = self.issue("a")["key"], self.issue("b")["key"]
        on_b = self.board.add_comment(b, "elsewhere")
        with self.assertRaises(InvalidRequest) as cm:
            self.board.attach(a, [("x.txt", io.BytesIO(b"x"))], comment=on_b["id"])
        self.assertIn(f"--comment {on_b['id']}", cm.exception.hint)
        with self.assertRaises(NotFound):
            self.board.attach(a, [("x.txt", io.BytesIO(b"x"))], comment=999)
        with self.assertRaises(InvalidRequest):
            self.board.attach(a, [("x.txt", io.BytesIO(b"x"))], comment="abc")
        att = self.board.attach(b, [("x.txt", io.BytesIO(b"x"))], comment=f"#{on_b['id']}")[0]
        self.assertEqual(att["comment_id"], on_b["id"])
        self.assertEqual(self.files_on_disk(), [os.path.join(*_shard(b"x", ".txt"))])

    def test_comment_with_files_is_one_write(self):
        w1 = self.agent("w1")
        key = self.issue("a")["key"]
        before = self.board.heads()
        comment = w1.add_comment(key, "screenshots @owner", kind="progress",
                                 attach=[("a.png", io.BytesIO(PNG)), self.write("b.log", b"log")])
        self.assertEqual([a["name"] for a in comment["attachments"]], ["a.png", "b.log"])
        self.assertTrue(all(a["comment_id"] == comment["id"] for a in comment["attachments"]))
        self.assertEqual((comment["kind"], comment["mentions"]), ("progress", ["owner"]))
        self.assertEqual(self.board.heads()["comment_id"], before["comment_id"] + 1)
        with self.assertRaises(PayloadTooLarge):
            self.board.config("max_attachment_mb", "1")
            w1.add_comment(key, "too big", attach=[("big", io.BytesIO(b"\0" * (1024 * 1024 + 1)))])
        self.assertEqual(len(self.board.list_comments(key)["items"]), 1)  # no comment without its files
        with self.assertRaises(NotFound):
            w1.add_comment(key, "bad reply", reply_to=999, attach=[("c.txt", io.BytesIO(b"c"))])
        self.assertEqual(len(self.board.list_attachments(key)), 2)

    def test_identity_and_joined_actor(self):
        key = self.issue("a")["key"]
        with self.assertRaises(IdentityRequired):
            self.open_as(None).attach(key, [("x.txt", io.BytesIO(b"x"))])
        with self.assertRaises(NotJoined):
            self.open_as("ghost").attach(key, [("x.txt", io.BytesIO(b"x"))])
        self.assertEqual(self.files_on_disk(), [])


class Lease(AttachCase):
    def test_holder_attach_renews_lease_and_moves_updated_at_not_version(self):
        w1 = self.agent("w1")
        key = self.issue("a")["key"]
        claimed = w1.claim(key)
        self.clock.advance(minutes=30)
        w1.attach(key, [("x.txt", io.BytesIO(b"x"))])
        issue = self.board.get_issue(key)
        self.assertEqual(issue["lease_until"], "2026-11-03T11:30:00Z")
        self.assertNotEqual(issue["lease_until"], claimed["lease_until"])
        self.assertEqual(issue["updated_at"], "2026-11-03T09:30:00Z")
        self.assertEqual(issue["version"], claimed["version"])
        self.clock.advance(minutes=10)
        self.agent("w2").attach(key, [("y.txt", io.BytesIO(b"y"))])  # not the holder: no renewal
        self.assertEqual(self.board.get_issue(key)["lease_until"], "2026-11-03T11:30:00Z")


class Detaching(AttachCase):
    def test_permissions(self):
        w1, w2, pm = self.agent("w1"), self.agent("w2"), self.agent("pm-1", "pm")
        key = self.issue("a")["key"]
        ids = [w1.attach(key, [(f"{n}.txt", io.BytesIO(n.encode()))])[0]["id"] for n in "abc"]
        with self.assertRaises(Forbidden):
            w2.detach(ids[0])
        self.assertEqual(w1.detach(f"@{ids[0]}"), {"deleted": ids[0], "issue": key, "name": "a.txt"})
        self.assertEqual(pm.detach(ids[1])["deleted"], ids[1])
        self.assertEqual(self.board.detach(ids[2])["deleted"], ids[2])
        with self.assertRaises(NotFound):
            w1.detach(ids[0])
        with self.assertRaises(InvalidRequest):
            w1.detach("x")
        actions = [(e["action"], e["old"]) for e in self.board.issue_events(key)
                   if e["action"] == "detach"]
        self.assertEqual(actions, [("detach", "@1 a.txt"), ("detach", "@2 b.txt"), ("detach", "@3 c.txt")])
        self.assertEqual(self.files_on_disk(), [])

    def test_file_stays_while_another_row_uses_it(self):
        a, b = self.issue("a")["key"], self.issue("b")["key"]
        one = self.board.attach(a, [("x.txt", io.BytesIO(b"shared"))])[0]
        two = self.board.attach(b, [("y.txt", io.BytesIO(b"shared"))])[0]
        self.board.detach(one["id"])
        self.assertTrue(os.path.exists(two["path"]))
        self.board.detach(two["id"])
        self.assertFalse(os.path.exists(two["path"]))

    def test_delete_issue_collects_its_files(self):
        a, b = self.issue("a")["key"], self.issue("b")["key"]
        comment = self.board.add_comment(a, "c")
        only_a = self.board.attach(a, [("a.png", io.BytesIO(PNG))], comment=comment["id"])[0]
        shared = self.board.attach(a, [("s.txt", io.BytesIO(b"s"))])[0]
        self.board.attach(b, [("s.txt", io.BytesIO(b"s"))])
        self.board.delete_issue(a)
        self.assertFalse(os.path.exists(only_a["path"]))
        self.assertTrue(os.path.exists(shared["path"]))
        self.assertEqual(self.board.conn.execute("SELECT count(*) FROM attachments").fetchone()[0], 1)

    def test_detach_and_attach_of_the_same_bytes_keep_the_file(self):
        """The file a concurrent attach relies on must never be collected under it."""
        key = self.issue("a")["key"]
        first = self.board.attach(key, [("x.txt", io.BytesIO(b"race"))])[0]
        staged = self.board._prepare(key, [("y.txt", io.BytesIO(b"race"))])  # bytes written
        self.board.detach(first["id"])                                        # file collected
        self.assertFalse(os.path.exists(first["path"]))
        try:
            with self.board._tx() as tx:
                self.board._insert_attachments(tx, 1, None, staged)           # puts its copy back
        finally:
            from earmark import attachments
            attachments.discard(staged)
        with open(first["path"], "rb") as fh:
            self.assertEqual(fh.read(), b"race")


class Copying(AttachCase):
    def test_save_attachment_checks_the_hash(self):
        key = self.issue("a")["key"]
        att = self.board.attach(key, [("shot.png", io.BytesIO(PNG))])[0]
        out = os.path.join(self.tmp, "out")
        os.mkdir(out)
        saved = self.board.save_attachment(att["id"], out)
        self.assertEqual((saved["path"], saved["bytes"]), (os.path.join(out, "shot.png"), len(PNG)))
        with self.assertRaises(InvalidRequest):
            self.board.save_attachment(att["id"], out)  # shot.png exists now
        other = self.board.save_attachment(att["id"], os.path.join(out, "copy.png"))
        self.assertEqual(other["attachment"]["id"], att["id"])
        os.chmod(att["path"], 0o644)
        with open(att["path"], "wb") as fh:
            fh.write(b"tampered")
        with self.assertRaises(Exception) as cm:
            self.board.save_attachment(att["id"], os.path.join(out, "bad.png"))
        self.assertEqual(cm.exception.code, "internal_error")
        self.assertFalse(os.path.exists(os.path.join(out, "bad.png")))
        os.remove(att["path"])
        with self.assertRaises(NotFound):
            self.board.save_attachment(att["id"], os.path.join(out, "gone.png"))


class AdminFiles(AttachCase):
    def test_export_lists_rows_and_info_counts_bytes(self):
        key = self.issue("a")["key"]
        self.board.attach(key, [("a.txt", io.BytesIO(b"aaa")), ("b.txt", io.BytesIO(b"aaa"))])
        dump = json.loads(json.dumps(self.board.export()))
        self.assertEqual([a["name"] for a in dump["attachments"]], ["a.txt", "b.txt"])
        self.assertEqual(dump["issues"][0]["attachment_count"], 2)
        self.assertEqual(self.board.info()["files"]["attachments"], 3)  # one file, shared

    def test_backup_copies_referenced_files(self):
        key = self.issue("a")["key"]
        att = self.board.attach(key, [("a.png", io.BytesIO(PNG)), ("b.txt", io.BytesIO(b"b"))])
        with open(os.path.join(self.board.files_dir, "stray.txt"), "w") as fh:
            fh.write("not ours")
        target = os.path.join(self.tmp, "backup")
        result = self.board.backup(target)
        self.assertEqual((result["files"], result["files_bytes"], result["missing"]),
                         (2, len(PNG) + 1, []))
        for item in att:
            rel = os.path.relpath(item["path"], self.board.files_dir)
            with open(os.path.join(target, "files", rel), "rb") as fh, open(item["path"], "rb") as src:
                self.assertEqual(fh.read(), src.read())
        self.assertFalse(os.path.exists(os.path.join(target, "files", "stray.txt")))
        from earmark.board import Board
        copy = Board.open(db=target)
        self.addCleanup(copy.close)
        self.assertEqual([a["path"] for a in copy.list_attachments(key)],
                         [os.path.join(target, "files", os.path.relpath(a["path"], self.board.files_dir))
                          for a in att])
        self.assertTrue(all(c["ok"] for c in copy.doctor() if c["name"] in
                            ("attachments", "unreferenced_files")))
        os.remove(att[1]["path"])
        again = self.board.backup(os.path.join(self.tmp, "backup2"))
        self.assertEqual((again["files"], again["missing"]), (1, [att[1]["sha256"] + ".txt"]))

    def test_doctor_reports_and_fix_removes_unreferenced_files(self):
        checks = {c["name"]: c for c in self.board.doctor()}
        self.assertTrue(checks["attachments"]["ok"])
        self.assertEqual(checks["unreferenced_files"], {"name": "unreferenced_files", "ok": True,
                                                        "detail": "none"})
        key = self.issue("a")["key"]
        kept = self.board.attach(key, [("k.txt", io.BytesIO(b"keep"))])[0]
        orphan = views.file_path(self.board.files_dir, "ab" * 32, ".png")
        os.makedirs(os.path.dirname(orphan), exist_ok=True)
        with open(orphan, "wb") as fh:
            fh.write(b"crash leftover")
        old_temp = os.path.join(self.board.files_dir, ".upload-old.part")
        new_temp = os.path.join(self.board.files_dir, ".upload-live.part")
        for path in (old_temp, new_temp):
            with open(path, "wb") as fh:
                fh.write(b"partial")
        stale = time.time() - 2 * 3600
        os.utime(old_temp, (stale, stale))
        checks = {c["name"]: c for c in self.board.doctor()}
        self.assertFalse(checks["unreferenced_files"]["ok"])
        self.assertIn("2 files (21 bytes)", checks["unreferenced_files"]["detail"])
        self.assertIn("doctor --fix", checks["unreferenced_files"]["detail"])
        self.assertTrue(os.path.exists(orphan))  # reporting never removes
        fixed = {c["name"]: c for c in self.board.doctor(fix=True)}
        self.assertEqual(fixed["unreferenced_files"],
                         {"name": "unreferenced_files", "ok": True,
                          "detail": "removed 2 unreferenced files (21 bytes)"})
        self.assertFalse(os.path.exists(orphan) or os.path.exists(old_temp))
        self.assertTrue(os.path.exists(new_temp) and os.path.exists(kept["path"]))
        os.remove(kept["path"])
        checks = {c["name"]: c for c in self.board.doctor()}
        self.assertFalse(checks["attachments"]["ok"])
        self.assertIn("@1 k.txt", checks["attachments"]["detail"])


WORKER = r"""
import io, sys
sys.path.insert(0, sys.argv[1])
from earmark.board import Board
board = Board.open(db=sys.argv[2], actor=sys.argv[3])
data = bytes(range(256)) * 4096          # 1 MiB, the same in both processes
with open(sys.argv[4] + ".ready", "w"):
    pass
import os, time
while not os.path.exists(sys.argv[4] + ".go"):
    time.sleep(0.005)
ids = []
for n in range(5):
    ids += [a["id"] for a in board.attach("GAME-1", [(f"{sys.argv[3]}-{n}.bin", io.BytesIO(data))])]
print(" ".join(map(str, ids)))
"""


class TwoProcesses(AttachCase):
    freeze_clock = False

    def test_identical_bytes_from_two_processes_share_one_file(self):
        self.issue("target")
        self.agent("p1")
        self.agent("p2")
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
        flag = os.path.join(self.tmp, "start")
        procs = [subprocess.Popen([sys.executable, "-c", WORKER, src, self.board.path, who,
                                   flag + who], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True) for who in ("p1", "p2")]
        deadline = time.monotonic() + 30
        while not all(os.path.exists(flag + who + ".ready") for who in ("p1", "p2")):
            self.assertLess(time.monotonic(), deadline, "workers did not start")
            time.sleep(0.01)
        for who in ("p1", "p2"):
            open(flag + who + ".go", "w").close()
        results = [p.communicate(timeout=60) for p in procs]
        for proc, (out, err) in zip(procs, results):
            self.assertEqual(proc.returncode, 0, err)
            self.assertEqual(len(out.split()), 5)
        rows = self.board.list_attachments("GAME-1")
        self.assertEqual(len(rows), 10)
        self.assertEqual(len({a["path"] for a in rows}), 1)
        self.assertEqual(self.files_on_disk(), [os.path.relpath(rows[0]["path"], self.board.files_dir)])
        with open(rows[0]["path"], "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), rows[0]["sha256"])


def _shard(data: bytes, ext: str) -> tuple[str, str]:
    sha = hashlib.sha256(data).hexdigest()
    return sha[:2], sha + ext


if __name__ == "__main__":
    unittest.main()
