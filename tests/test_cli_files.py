"""Attachment commands: the commands.md sample byte for byte, renderers, and attach, attachments,
attachment, detach, comment --attach, show/comments/ls markers, backup DIR, doctor --fix and
config max_attachment_mb end to end on a real board."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import (DAY, CliBoardCase, comment, contract_block, contract_line,  # noqa: E402
                              fake_board, issue, run_main, summary)

from earmark.cli import render as R  # noqa: E402


def att(id: int, kind: str, size: int, name: str, author: str, comment_id, path: str,
        mime: str = "application/octet-stream") -> dict:
    return {"id": id, "issue": "GAME-42", "comment_id": comment_id, "name": name, "mime": mime,
            "kind": kind, "size": size, "sha256": "3f" * 32, "author": author,
            "created_at": "2026-11-03T14:05:09Z", "url": f"/api/attachments/{id}/raw", "path": path}


SAMPLE = [att(12, "image", 188416, "eye-picker-bug.png", "claude-ui", 1187,
              "/work/game/.earmark/files/3f/3fa1…c9.png", "image/png"),
          att(13, "audio", 2202010, "ambience-v2.ogg", "agy-audio", None,
              "/work/game/.earmark/files/a0/a07e…41.ogg", "audio/ogg")]


class RenderTests(unittest.TestCase):
    def test_contract_sample(self):
        self.assertEqual(R.render_attachments(SAMPLE), contract_block("ID   KIND"))

    def test_empty(self):
        self.assertEqual(R.render_attachments([], key="GAME-42"), "attachments: none on GAME-42")

    def test_sizes(self):
        cases = {0: "0 B", 512: "512 B", 1023: "1023 B", 1024: "1.0 KB", 7680: "7.5 KB",
                 188416: "184 KB", 2202010: "2.1 MB", 52428800: "50 MB", 3 * 1024 ** 3: "3.0 GB"}
        for n, text in cases.items():
            self.assertEqual(R.file_size(n), text, n)

    def test_show_line_and_flags(self):
        line = contract_line("attachments: 2 (")
        self.assertEqual(R.attachments_summary(issue(attachments=SAMPLE)), line)
        out = R.render_show(issue(attachments=SAMPLE), day=DAY).splitlines()
        self.assertEqual(out[out.index("comments: 12, last #1199, 3 new for you") + 1], line)
        self.assertNotIn("attachments:", R.render_show(issue(attachments=[]), day=DAY))
        three = SAMPLE + [att(14, "image", 1, "b.png", "x", None, "/p")]
        self.assertEqual(R.attachments_summary({"attachments": three}),
                         "attachments: 3 (2 images, 1 audio)")
        self.assertIn("a:2", R.issue_flags(summary(attachment_count=2), DAY))
        self.assertEqual(R.issue_flags(summary(attachment_count=2, comment_count=0, due=None), DAY), ["a:2"])
        self.assertEqual(R.render_fields(issue(attachments=SAMPLE, attachment_count=2),
                                         R.show_fields("attachment_count,attachments")),
                         "attachment_count: 2\n" + contract_block("ID   KIND"))
        self.assertEqual(R.render_fields(issue(attachments=[]), ["attachments"]), "attachments: none")

    def test_comment_header(self):
        c = comment(1190, "codex-qa", "note", "see", "2026-11-03T15:10:44Z", reply_to=1187)
        c["attachments"] = SAMPLE
        self.assertEqual(R.comment_lines(c)[0],
                         "#1190  codex-qa  note  11-03 15:10  reply-to:#1187  " + "files:@12,@13")
        self.assertIn("files:@12,@13", contract_line("files:@"))

    def test_attachment_detail(self):
        self.assertEqual(R.render_attachment(SAMPLE[0]).splitlines(), [
            "@12  eye-picker-bug.png",
            "issue: GAME-42   comment: #1187   author: claude-ui   created: 2026-11-03T14:05:09Z",
            "kind: image   mime: image/png   size: 184 KB (188416 bytes)",
            "sha256: " + "3f" * 32,
            "path: /work/game/.earmark/files/3f/3fa1…c9.png"])


class FakeBoardTests(unittest.TestCase):
    def test_attach_maps_arguments(self):
        with fake_board(attach=SAMPLE) as boards:
            code, out, _ = run_main(["attach", "42", "a.png", "b.ogg", "--comment", "#1187",
                                     "--as", "w-1"])
        self.assertEqual((code, out), (0, "@12\n@13\n"))
        self.assertEqual(boards[0].called("attach"), [(("42", ["a.png", "b.ogg"]), {"comment": 1187})])
        with fake_board(attach=SAMPLE[:1]) as boards:
            run_main(["attach", "42", "a.png", "--as", "w-1"])
        self.assertEqual(boards[0].called("attach"), [(("42", ["a.png"]), {})])
        code, _, err = run_main(["attach", "42", "--as", "w-1"])
        self.assertEqual(code, 1)
        self.assertIn("FILE", err)

    def test_comment_attach_is_repeatable(self):
        made = dict(comment(1190, "w-1", "note", "x", "2026-11-03T15:10:44Z"), attachments=SAMPLE)
        with fake_board(add_comment=made) as boards:
            code, out, _ = run_main(["comment", "42", "shots", "--attach", "a.png", "--attach",
                                     "b.ogg", "--as", "w-1"])
        self.assertEqual((code, out), (0, "#1190 @12 @13\n"))
        self.assertEqual(boards[0].called("add_comment"),
                         [(("42", "shots"), {"attach": ["a.png", "b.ogg"]})])
        code, _, err = run_main(["comment", "--edit", "5", "x", "--attach", "a.png", "--as", "w-1"])
        self.assertEqual(code, 1)
        self.assertIn("--comment 5", err)

    def test_attachment_and_detach(self):
        with fake_board(get_attachment=SAMPLE[0], save_attachment={
                "attachment": SAMPLE[0], "path": "/tmp/out/eye.png", "bytes": 188416}) as boards:
            _, shown, _ = run_main(["attachment", "@12"])
            _, saved, _ = run_main(["attachment", "12", "--out", "/tmp/out"])
        self.assertTrue(shown.startswith("@12  eye-picker-bug.png\n"))
        self.assertEqual(saved, "@12 copied to /tmp/out/eye.png · 184 KB\n")
        self.assertEqual(boards[1].called("save_attachment"), [(("12", "/tmp/out"), {})])
        with fake_board(detach={"deleted": 12, "issue": "GAME-42", "name": "eye.png"}):
            _, out, _ = run_main(["detach", "@12", "--as", "w-1"])
        self.assertEqual(out, "detached @12 eye.png from GAME-42\n")

    def test_doctor_fix_and_backup_flags(self):
        with fake_board(doctor=[{"name": "python", "ok": True, "detail": "3"}]) as boards:
            run_main(["doctor", "--fix", "--as", "hector"])
            run_main(["doctor"])
        self.assertEqual(boards[0].called("doctor"), [((), {"fix": True})])
        self.assertEqual(boards[1].called("doctor"), [((), {"fix": False})])
        res = {"path": "/b/earmark.db", "bytes": 2048, "dir": "/b", "files": 3,
               "files_bytes": 2202010, "missing": ["x.png"]}
        with fake_board(backup=res):
            _, out, _ = run_main(["backup", "/b"])
        self.assertEqual(out, "backup: /b/earmark.db · 2.0 KB · files: 3 (2.1 MB) · "
                              "missing: 1 (see earmark doctor)\n")


class EndToEndTests(CliBoardCase):
    def setUp(self):
        super().setUp()
        self.ok("join", "worker", "--cli", "claude-code", as_="claude-ui")
        self.ok("join", "worker", as_="agy-audio")
        self.ok("new", "Eye color widget resets on reload", as_="hector")
        self.src = os.path.join(self.tmp, "src")
        os.mkdir(self.src)

    def file(self, name: str, data: bytes) -> str:
        path = os.path.join(self.src, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_attach_list_show_detach(self):
        png = self.file("eye-picker-bug.png", b"\x89PNG" + b"\0" * 188412)
        ogg = self.file("ambience-v2.ogg", b"OggS" + b"\1" * 2202006)
        self.assertEqual(self.ok("comment", "1", "Moved the widget state.", "--kind", "progress",
                                 "--attach", png, as_="claude-ui"), "#1 @1\n")
        self.assertEqual(self.ok("attach", "GAME-1", ogg, as_="agy-audio"), "@2\n")
        root = os.path.join(self.tmp, ".earmark", "files")

        def where(data_path, ext):
            with open(data_path, "rb") as fh:
                sha = hashlib.sha256(fh.read()).hexdigest()
            return os.path.join(root, sha[:2], sha + ext)

        expected = "\n".join([
            "ID  KIND   SIZE    NAME                AUTHOR     COMMENT  PATH",
            f"@1  image  184 KB  eye-picker-bug.png  claude-ui  #1       {where(png, '.png')}",
            f"@2  audio  2.1 MB  ambience-v2.ogg     agy-audio  -        {where(ogg, '.ogg')}",
        ]) + "\n"
        self.assertEqual(self.ok("attachments", "1"), expected)
        show = self.ok("show", "1").splitlines()
        self.assertEqual(show[show.index("comments: 1, last #1") + 1], "attachments: 2 (1 image, 1 audio)")
        self.assertEqual(self.ok("comments", "1").splitlines()[0],
                         "#1  claude-ui  progress  11-03 10:02  files:@1")
        self.assertTrue(self.ok("ls").splitlines()[1].endswith("c:1 a:2"))
        detail = self.ok("attachment", "@2").splitlines()
        self.assertEqual(detail[0], "@2  ambience-v2.ogg")
        self.assertEqual(detail[-1], f"path: {where(ogg, '.ogg')}")
        out = os.path.join(self.tmp, "out")
        os.mkdir(out)
        self.assertEqual(self.ok("attachment", "2", "--out", out),
                         f"@2 copied to {os.path.join(out, 'ambience-v2.ogg')} · 2.1 MB\n")
        env = self.json("attachments", "1")
        self.assertEqual([a["id"] for a in env["data"]], [1, 2])
        self.assertTrue(all(os.path.isabs(a["path"]) for a in env["data"]))
        err = self.fails(1, "detach", "@2", as_="claude-ui")
        self.assertIn("error: @2 was attached by agy-audio", err)
        self.assertEqual(self.ok("detach", "@2", as_="agy-audio"),
                         "detached @2 ambience-v2.ogg from GAME-1\n")
        self.assertFalse(os.path.exists(where(ogg, ".ogg")))
        self.assertEqual(self.ok("attach", "1", png, "--comment", "1", as_="hector"), "@3\n")
        self.fails(2, "attachment", "99")
        self.fails(1, "attach", "1", os.path.join(self.src, "missing.png"), as_="hector")

    def test_limit_backup_and_doctor(self):
        self.assertEqual(self.ok("config", "max_attachment_mb"), "max_attachment_mb: 50\n")
        self.assertEqual(self.ok("config", "max_attachment_mb", "1", as_="hector"), "max_attachment_mb: 1\n")
        big = self.file("big.bin", b"\0" * (1024 * 1024 + 1))
        err = self.fails(1, "attach", "1", big, as_="hector")
        self.assertIn("over the 1 MB attachment limit", err)
        self.assertIn("hint: the owner can raise it: earmark config max_attachment_mb N", err)
        env = self.json("attach", "1", big, as_="hector")
        self.assertEqual((env["ok"], env["error"]["code"]), (False, "invalid_request"))
        self.ok("attach", "1", self.file("log.txt", b"line\n"), as_="hector")
        dest = os.path.join(self.tmp, "backup")
        out = self.ok("backup", dest)
        self.assertTrue(out.rstrip().endswith("· files: 1 (5 B)"), out)
        self.assertTrue(os.path.isdir(os.path.join(dest, "files")))
        orphan = os.path.join(self.tmp, ".earmark", "files", "ab", "ab" * 32 + ".png")
        os.makedirs(os.path.dirname(orphan))
        with open(orphan, "wb") as fh:
            fh.write(b"leftover")
        code, report, _ = self.cli("doctor")
        self.assertEqual(code, 1)
        self.assertIn("FAIL  unreferenced_files  1 files (8 bytes) not referenced by any attachment; "
                      "earmark doctor --fix removes them", report)
        fixed = self.ok("doctor", "--fix", as_="hector")
        self.assertIn("ok    unreferenced_files  removed 1 unreferenced files (8 bytes)", fixed)
        self.assertFalse(os.path.exists(orphan))
        env = json.loads(self.ok("export"))
        self.assertEqual([a["name"] for a in env["attachments"]], ["log.txt"])


if __name__ == "__main__":
    unittest.main()
