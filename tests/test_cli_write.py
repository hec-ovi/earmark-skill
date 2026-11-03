"""Write commands against the recording fake board: only given fields reach the board."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import COMMENTS, fake_board, issue, run_main  # noqa: E402

AS = ["--as", "w-1"]


class NewTests(unittest.TestCase):
    def test_minimal(self):
        with fake_board(create_issue=issue(key="GAME-57")) as boards:
            code, out, _ = run_main(["new", "Mute button state"] + AS)
        self.assertEqual((code, out), (0, "GAME-57\n"))
        self.assertEqual(boards[0].called("create_issue"), [(("Mute button state",), {})])

    def test_all_flags(self):
        with fake_board(create_issue=issue(key="GAME-57")) as boards:
            run_main(["new", "T", "-b", "body", "-t", "ui-ux", "-p", "high", "--type", "bug",
                      "-l", "a", "-l", "b,c", "--due", "+3d", "--parent", "30", "--assign", "me",
                      "--status", "backlog"] + AS)
        self.assertEqual(boards[0].called("create_issue")[0][1],
                         {"body": "body", "topic": "ui-ux", "priority": "high", "type": "bug",
                          "status": "backlog", "labels": ["a", "b", "c"], "due": "+3d",
                          "parent": "30", "assignee": "me"})

    def test_stdin_tty_is_refused(self):
        class TTY:
            def isatty(self):
                return True

            def read(self):
                raise AssertionError("must not read a terminal")
        from unittest import mock
        with fake_board(), mock.patch("earmark.cli.app._isatty", lambda s: isinstance(s, TTY)):
            with mock.patch("sys.stdin", TTY()):
                from earmark.cli import main
                import contextlib
                import io
                err = io.StringIO()
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    code = main(["new", "T", "--body-file", "-"] + AS)
        self.assertEqual(code, 1)
        self.assertIn("stdin is a terminal", err.getvalue())


class EditTests(unittest.TestCase):
    def test_only_given_fields(self):
        with fake_board(update_issue=issue(version=8)) as boards:
            code, out, _ = run_main(["edit", "42", "--title", "New", "-l", "+ui", "-l", "-regression",
                                     "-l", "plain", "--no-due", "--if-version", "7"] + AS)
        self.assertEqual(code, 0)
        self.assertEqual(boards[0].called("update_issue"),
                         [(("42",), {"if_version": 7, "append": None, "add_labels": ["ui", "plain"],
                                     "remove_labels": ["regression"], "title": "New", "due": None})])
        self.assertEqual(out, "GAME-42 updated: title, due, labels · version 8\n")

    def test_clears_and_append(self):
        with fake_board(update_issue=issue()) as boards:
            run_main(["edit", "42", "--no-parent", "--no-topic", "--append", "more"] + AS)
        kw = boards[0].called("update_issue")[0][1]
        self.assertEqual((kw["parent"], kw["topic"], kw["append"]), (None, None, "more"))

    def test_nothing_to_change(self):
        with fake_board() as boards:
            code, _, err = run_main(["edit", "42"] + AS)
        self.assertEqual(code, 1)
        self.assertIn("edit: nothing to change", err)
        self.assertEqual(boards[0].called("update_issue") if boards else [], [])

    def test_body_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Title\n\nbody\n")
        self.addCleanup(os.unlink, f.name)
        with fake_board(update_issue=issue()) as boards:
            run_main(["edit", "42", "--body-file", f.name] + AS)
        self.assertEqual(boards[0].called("update_issue")[0][1]["body"], "# Title\n\nbody")


class CommentTests(unittest.TestCase):
    def test_add(self):
        with fake_board(add_comment=COMMENTS[1]) as boards:
            code, out, _ = run_main(["comment", "42", "Still resets", "--kind", "question",
                                     "--reply-to", "#1187"] + AS)
        self.assertEqual((code, out), (0, "#1190\n"))
        self.assertEqual(boards[0].called("add_comment"),
                         [(("42", "Still resets"), {"kind": "question", "reply_to": 1187})])

    def test_stdin_and_edit(self):
        with fake_board(add_comment=COMMENTS[0]) as boards:
            run_main(["comment", "42", "-"] + AS, stdin="line 1\nline 2\n")
        self.assertEqual(boards[0].called("add_comment")[0][0], ("42", "line 1\nline 2"))
        with fake_board(edit_comment=COMMENTS[0]) as boards:
            code, out, _ = run_main(["comment", "--edit", "#1187", "fixed text"] + AS)
        self.assertEqual((code, out), (0, "#1187 edited\n"))
        self.assertEqual(boards[0].called("edit_comment"), [((1187, "fixed text"), {})])

    def test_errors(self):
        for argv, msg in ((["comment", "42"], "empty body"),
                          (["comment", "--edit", "x", "t"], "--edit must be a number"),
                          (["comment", "--edit", "1", "42", "t"], "takes ID and TEXT, no KEY"),
                          (["comment", "42", "t", "--reply-to", "abc"], "--reply-to must be a number")):
            with fake_board():
                code, _, err = run_main(argv + AS)
            self.assertEqual(code, 1, argv)
            self.assertIn(msg, err, argv)


class LinkAssignRankDeleteTests(unittest.TestCase):
    def test_link_unlink(self):
        for cmd, method in (("link", "link"), ("unlink", "unlink")):
            with fake_board(**{method: issue()}) as boards:
                code, out, _ = run_main([cmd, "42", "blocks", "51"] + AS)
            self.assertEqual(boards[0].called(method), [(("42", "blocks", "51"), {})])
            self.assertEqual(out, f"{cmd}ed: GAME-42 blocks GAME-51\n")

    def test_assign(self):
        with fake_board(assign=issue(assignee=None)) as boards:
            _, out, _ = run_main(["assign", "42", "none"] + AS)
        self.assertEqual(boards[0].called("assign"), [(("42", None), {})])
        self.assertEqual(out, "GAME-42 assignee: -\n")
        with fake_board(assign=issue()) as boards:
            run_main(["assign", "42", "claude-ui"] + AS)
        self.assertEqual(boards[0].called("assign"), [(("42", "claude-ui"), {})])

    def test_rank(self):
        with fake_board(rank=issue()) as boards:
            _, out, _ = run_main(["rank", "42", "--before", "game-40"] + AS)
        self.assertEqual(boards[0].called("rank")[0][1],
                         {"before": "game-40", "after": None, "top": False, "bottom": False})
        self.assertEqual(out, "GAME-42 ranked: before GAME-40\n")

    def test_delete_needs_yes_before_opening(self):
        with fake_board() as boards:
            code, _, err = run_main(["delete", "42"] + AS)
        self.assertEqual((code, boards), (1, []))
        self.assertIn("needs --yes", err)
        self.assertIn("earmark cancel 42", err)
        with fake_board(delete_issue={"deleted": "GAME-42"}) as boards:
            code, out, _ = run_main(["delete", "42", "--yes"] + AS)
        self.assertEqual((code, out), (0, "deleted GAME-42\n"))


if __name__ == "__main__":
    unittest.main()
