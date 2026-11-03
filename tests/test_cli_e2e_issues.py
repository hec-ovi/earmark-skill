"""End to end on a real temp board: issue reads and writes, comments, inbox, exact text."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import CliBoardCase, contract_block  # noqa: E402


class SampleBoard(CliBoardCase):
    """Topics and agents of the commands.md samples."""

    def setUp(self):
        super().setUp()
        for path in ("ui-ux/character-menu/face-layer/eye-color", "ui-ux/video-calls"):
            self.ok("topic", "add", path, as_="hector")
        self.ok("join", "worker", "--cli", "claude-code", as_="claude-ui")
        self.ok("join", "reviewer", "--cli", "codex", as_="codex-qa")


class LsSampleTests(SampleBoard):
    def test_ls_matches_the_contract_sample(self):
        for n in range(1, 52):
            self.ok("new", f"filler {n}", as_="hector")
        for n in range(1, 52):
            if n not in (42, 51):
                self.ok("delete", str(n), "--yes", as_="hector")
        self.ok("edit", "42", "--title", "Eye color widget resets on reload", "-t",
                "ui-ux/character-menu/face-layer/eye-color", "-p", "high", "--type", "bug",
                "--due", "2026-11-07", as_="hector")
        self.ok("edit", "51", "--title", "Mute button state", "-t", "ui-ux/video-calls", as_="hector")
        self.ok("link", "GAME-42", "blocks", "game-51", as_="hector")
        self.ok("claim", "42", as_="claude-ui")
        for text in ("one", "two", "three"):
            self.ok("comment", "42", text, as_="claude-ui")
        self.assertEqual(self.ok("ls"), contract_block("KEY      STATUS") + "\n")


class IssueWriteTests(SampleBoard):
    def test_new_prints_the_key_and_sets_fields(self):
        out = self.ok("new", "Eye color widget resets on reload", "-b", "Steps: reload.", "-t",
                      "ui-ux/character-menu/face-layer/eye-color", "-p", "high", "--type", "bug",
                      "-l", "regression", "-l", "ui", "--due", "2026-11-07", "--assign", "claude-ui",
                      as_="hector")
        self.assertEqual(out, "GAME-1\n")
        child = self.ok("new", "Child", "--parent", "1", "--status", "backlog", as_="hector")
        self.assertEqual(child, "GAME-2\n")
        show = self.ok("show", "GAME-1").splitlines()
        self.assertEqual(show[0], "GAME-1  Eye color widget resets on reload")
        self.assertEqual(show[1], "status: todo   priority: high   type: bug   version: 1")
        self.assertEqual(show[2], "topic: ui-ux/character-menu/face-layer/eye-color")
        self.assertEqual(show[3], "assignee: claude-ui   reviewer: -   reporter: hector")
        self.assertEqual(show[4], "labels: regression, ui   due: 2026-11-07")
        self.assertEqual(show[5], "parent: -   sub: GAME-2   blocks: -   blocked by: -")
        self.assertEqual(show[6], "created: 2026-11-03T10:02:11Z   updated: 2026-11-03T10:02:11Z")
        self.assertEqual(show[7], "comments: 0")
        self.assertEqual(show[8:], ["", "Steps: reload."])
        self.assertEqual(self.ok("show", "2", "-f", "status,parent"), "status: backlog\nparent: GAME-1\n")

    def test_body_inputs(self):
        path = os.path.join(self.tmp, "body.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("from a file\n- bullet\n\n")
        self.ok("new", "A", "--body-file", path, as_="hector")
        self.ok("new", "B", "--body-file", "-", stdin="from stdin\n", as_="hector")
        self.assertEqual(self.ok("show", "1", "-f", "body"), "body: from a file\n  - bullet\n")
        self.assertEqual(self.json("show", "2")["data"]["body"], "from stdin")
        err = self.fails(1, "new", "C", "-b", "x", "--body-file", path, as_="hector")
        self.assertIn("not allowed with argument", err)
        err = self.fails(1, "new", "C", "--body-file", os.path.join(self.tmp, "missing"), as_="hector")
        self.assertIn("cannot read", err)

    def test_edit(self):
        self.ok("new", "Old", "-l", "regression", as_="hector")
        out = self.ok("edit", "1", "--title", "New", "-l", "-regression", "-l", "+ui", "--due", "+3d",
                      "-p", "urgent", "--type", "feature", as_="hector")
        self.assertEqual(out, "GAME-1 updated: title, priority, type, due, labels · version 2\n")
        data = self.json("show", "1")["data"]
        self.assertEqual((data["title"], data["labels"], data["due"], data["priority"], data["type"]),
                         ("New", ["ui"], "2026-11-06", "urgent", "feature"))
        self.ok("edit", "1", "--append", "more text", as_="hector")
        self.ok("edit", "1", "--no-due", "-t", "ui-ux", as_="hector")
        self.assertEqual(self.ok("show", "1", "-f", "due,topic,body"), "due: -\ntopic: ui-ux\nbody: more text\n")
        self.ok("edit", "1", "--no-topic", as_="hector")
        self.assertEqual(self.ok("show", "1", "-f", "topic"), "topic: -\n")
        err = self.fails(3, "edit", "1", "--title", "X", "--if-version", "1", as_="hector")
        self.assertTrue(err.startswith("error: GAME-1 changed"), err)
        self.assertIn("hint:", err)
        self.assertIn("nothing to change", self.fails(1, "edit", "1", as_="hector"))
        err = self.fails(2, "edit", "1", "-t", "ui-ux/video-call", as_="hector")
        self.assertIn("ui-ux/video-calls", err)

    def test_link_assign_rank_delete(self):
        for t in ("A", "B", "C"):
            self.ok("new", t, as_="hector")
        self.assertEqual(self.ok("link", "1", "blocks", "2", as_="hector"), "linked: GAME-1 blocks GAME-2\n")
        self.ok("link", "1", "relates", "3", as_="hector")
        self.ok("link", "3", "duplicates", "2", as_="hector")
        show = self.ok("show", "2").splitlines()
        self.assertEqual(show[5], "parent: -   sub: -   blocks: -   blocked by: GAME-1 (todo)")
        self.assertEqual(show[6], "duplicates: -   duplicated by: GAME-3   relates: -")
        self.assertIn("blocked", self.ok("ls", "--blocked"))
        self.assertEqual(self.ok("unlink", "1", "blocks", "2", as_="hector"),
                         "unlinked: GAME-1 blocks GAME-2\n")
        self.assertEqual(self.ok("assign", "1", "claude-ui", as_="hector"), "GAME-1 assignee: claude-ui\n")
        self.assertEqual(self.ok("assign", "1", "none", as_="hector"), "GAME-1 assignee: -\n")
        self.assertEqual(self.ok("rank", "3", "--top", as_="hector"), "GAME-3 ranked: top\n")
        self.assertEqual(self.ok("rank", "1", "--after", "3", as_="hector"), "GAME-1 ranked: after GAME-3\n")
        keys = [ln.split()[0] for ln in self.ok("ls", "--sort", "rank").splitlines()[1:-1]]
        self.assertEqual(keys, ["GAME-3", "GAME-1", "GAME-2"])
        self.assertIn("needs --yes", self.fails(1, "delete", "2", as_="hector"))
        self.assertEqual(self.ok("delete", "2", "--yes", as_="hector"), "deleted GAME-2\n")
        self.assertIn("error: no issue GAME-2", self.fails(2, "show", "2"))
        self.assertIn("error:", self.fails(1, "delete", "1", "--yes", as_="claude-ui"))

    def test_an_over_long_title_hints_at_the_body(self):
        spec = "Eye color widget: " + "add a hue slider, a preset row and a reset button; " * 4
        hint = 'error: title must be 1 to 200 characters\nhint: keep the title short; put details in -b "…"\n'
        self.assertEqual(self.fails(1, "new", spec, as_="claude-ui"), hint)
        key = self.ok("new", "Eye color widget", as_="claude-ui").strip()
        self.assertEqual(self.fails(1, "need", key, spec, "-t", "ui-ux/video-calls", as_="claude-ui"), hint)
        self.assertEqual(self.fails(1, "new", " ", as_="claude-ui"),
                         "error: title must be 1 to 200 characters\n")  # nothing to shorten
        self.assertEqual(self.ok("ls", "--count"), "1\n")

    def test_writes_need_identity(self):
        err = self.fails(1, "new", "x")
        self.assertTrue(err.startswith("error: "), err)
        self.assertIn("hint: ", err)
        self.assertEqual(self.json("new", "x")["error"]["code"], "identity_required")
        self.assertEqual(self.json("new", "x", as_="ghost")["error"]["code"], "not_joined")

    def test_ls_filters_paging_and_count(self):
        for n in range(1, 8):
            self.ok("new", f"issue {n}", "-p", "high" if n % 2 else "low", as_="hector")
        self.ok("cancel", "7", "-m", "dup", as_="hector")
        self.assertEqual(self.ok("ls", "--count"), "6\n")
        self.assertEqual(self.ok("ls", "--count", "-s", "all"), "7\n")
        out = self.ok("ls", "--limit", "4").splitlines()
        self.assertEqual(out[-1], "4 of 6 open issues · next: --offset 4")
        out = self.ok("ls", "--limit", "4", "--offset", "4").splitlines()
        self.assertEqual(out[-1], "2 of 6 open issues · offset 4")
        self.assertEqual(self.ok("ls", "-s", "closed").splitlines()[-1], "1 of 1 closed issues")
        out = self.ok("ls", "-p", "high", "--fields", "key,priority").splitlines()
        self.assertEqual(out[0], "KEY     PRIO")
        self.assertEqual({ln.split()[1] for ln in out[1:-1]}, {"high"})
        self.assertEqual(self.ok("ls", "-q", "nothing-like-this"), "0 of 0 open issues\n")
        env = self.json("ls", "--limit", "2")
        self.assertEqual((env["ok"], env["data"]["total"], len(env["data"]["items"])), (True, 6, 2))
        self.assertEqual(env["meta"], {"command": "ls", "board": "GAME", "actor": None})


class CommentTests(SampleBoard):
    def setUp(self):
        super().setUp()
        self.ok("new", "Eye color", as_="hector")
        self.ok("claim", "1", as_="claude-ui")

    def test_comment_forms(self):
        self.assertEqual(self.ok("comment", "1", "Moved the state.", "--kind", "progress",
                                 as_="claude-ui"), "#1\n")
        self.advance(minutes=5)
        self.assertEqual(self.ok("comment", "GAME-1", "Still resets. @claude-ui", "--reply-to", "#1",
                                 as_="codex-qa"), "#2\n")
        self.assertEqual(self.ok("comment", "1", "-", stdin="from stdin\n", as_="hector"), "#3\n")
        path = os.path.join(self.tmp, "c.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("from file")
        self.assertEqual(self.ok("comment", "1", "--body-file", path, as_="hector"), "#4\n")
        self.assertEqual(self.ok("comment", "--edit", "1", "Moved the widget state.", as_="claude-ui"),
                         "#1 edited\n")
        out = self.ok("comments", "1").splitlines()
        self.assertEqual(out[:4], ["#1  claude-ui  progress  11-03 10:02  edited",
                                   "  Moved the widget state.",
                                   "#2  codex-qa  note  11-03 10:07  reply-to:#1",
                                   "  Still resets. @claude-ui"])
        self.assertEqual(out[-1], "last: #4 · 4 comments")
        self.assertIn("error:", self.fails(1, "comment", "--edit", "1", "hijack", as_="codex-qa"))
        self.assertIn("empty body", self.fails(1, "comment", "1", "-", stdin="  \n", as_="hector"))
        self.assertIn("KEY is required", self.fails(1, "comment", as_="hector"))

    def test_cursors(self):
        for n in range(1, 4):
            self.ok("comment", "1", f"note {n}", as_="hector")
        out = self.ok("comments", "1", "--new", as_="claude-ui").splitlines()
        self.assertEqual(out[-1], "last: #3 · 3 comments")
        self.assertEqual(self.ok("comments", "1", "--new", as_="claude-ui"),
                         "comments: nothing new after #3\n")
        self.assertEqual(self.ok("comments", "1", "--since", "2").splitlines()[0],
                         "#3  hector  note  11-03 10:02")
        self.assertEqual(self.ok("comments", "1", "--last", "1").splitlines()[0][:2], "#3")
        out = self.ok("comments", "1", "--limit", "2").splitlines()
        self.assertEqual(out[-1], "last: #2 · 2 comments · more: --since 2")
        show = self.ok("show", "1", "-c", "2", as_="codex-qa")
        self.assertIn("comments: 3, last #3, 3 new for you", show)
        self.assertIn("last 2 of 3 comments:\n#2  hector  note", show)

    def test_inbox(self):
        self.ok("comment", "1", "ping @claude-ui", as_="hector")
        self.ok("comment", "1", "mine", as_="claude-ui")
        out = self.ok("inbox", "--peek", as_="claude-ui").splitlines()
        self.assertEqual(out, ["GAME-1  #1  hector  note  11-03 10:02", "  ping @claude-ui",
                               "last: #1 · 1 comment"])
        self.assertEqual(self.ok("inbox", as_="claude-ui").splitlines()[-1], "last: #1 · 1 comment")
        self.assertEqual(self.ok("inbox", as_="claude-ui"), "inbox: nothing new after #1\n")
        env = self.json("inbox", "--peek", as_="claude-ui")
        self.assertEqual((env["data"]["items"], env["meta"]["actor"]), ([], "claude-ui"))


if __name__ == "__main__":
    unittest.main()
