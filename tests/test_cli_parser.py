"""Command grammar: global flags anywhere, one subparser per run, argparse errors as exit 1."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_cli_support  # noqa: E402,F401  (puts src/ on sys.path)

from earmark.cli import parser as P  # noqa: E402
from earmark.errors import InvalidRequest  # noqa: E402


def parse(*argv):
    return P.parse(list(argv))


class ScanTests(unittest.TestCase):
    def test_skips_global_flags(self):
        self.assertEqual(P.scan(["--as", "ls", "--db", "x", "--json", "show", "1"]), ("show", None))
        self.assertEqual(P.scan(["--as=me", "topic", "add", "a/b"]), ("topic", "add"))
        self.assertEqual(P.scan(["profile", "--json", "show", "worker"]), ("profile", "show"))
        self.assertEqual(P.scan(["--json"]), (None, None))
        self.assertEqual(P.scan([]), (None, None))

    def test_wants_json(self):
        self.assertTrue(P.wants_json(["ls", "--json"]))
        self.assertFalse(P.wants_json(["comment", "1", "--", "--json"]))

    def test_label_removal_is_not_an_option(self):
        self.assertEqual(P.normalize(["edit", "1", "-l", "-old", "--label", "+new", "-l", "x"]),
                         ["edit", "1", "--label=-old", "--label", "+new", "-l", "x"])
        self.assertEqual(P.normalize(["comment", "1", "--", "-l", "-x"]), ["comment", "1", "--", "-l", "-x"])


class ParseTests(unittest.TestCase):
    def test_globals_before_and_after(self):
        for argv in (["--as", "w-1", "--db", "b.db", "--json", "ls"],
                     ["ls", "--as", "w-1", "--db", "b.db", "--json"],
                     ["--json", "ls", "--as=w-1", "--db=b.db"]):
            name, ns = parse(*argv)
            self.assertEqual(name, "ls")
            self.assertEqual((ns.actor, ns.db, ns.json), ("w-1", "b.db", True), argv)

    def test_globals_absent(self):
        _, ns = parse("ls")
        self.assertFalse(hasattr(ns, "actor") or hasattr(ns, "db") or hasattr(ns, "json"))

    def test_nested_commands(self):
        name, ns = parse("topic", "add", "ui-ux/video-calls", "--name", "Video calls", "--as", "pm-1")
        self.assertEqual((name, ns.path, ns.name, ns.actor), ("topic add", "ui-ux/video-calls",
                                                               "Video calls", "pm-1"))
        name, ns = parse("profile", "add", "ui-lead", "--role", "worker", "--name", "UI lead",
                         "-t", "ui-ux", "-t", "web")
        self.assertEqual((name, ns.topic), ("profile add", ["ui-ux", "web"]))
        name, ns = parse("--json", "profile", "show", "worker")
        self.assertEqual((name, ns.slug, ns.json), ("profile show", "worker", True))

    def test_ls_flags(self):
        _, ns = parse("ls", "-t", "ui-ux", "-t", "web", "--exact", "-s", "active", "-p", "urgent,high",
                      "-a", "me", "-l", "regression", "--ready", "--sort", "due", "--desc",
                      "--limit", "10", "--offset", "20", "-q", "eye")
        self.assertEqual(ns.topic, ["ui-ux", "web"])
        self.assertEqual((ns.status, ns.priority, ns.assignee, ns.sort, ns.limit, ns.offset),
                         ("active", "urgent,high", "me", "due", 10, 20))
        self.assertTrue(ns.exact and ns.ready and ns.desc)

    def test_show_comment_count_default(self):
        self.assertEqual(parse("show", "42", "-c")[1].comments, 5)
        self.assertEqual(parse("show", "42", "-c", "2")[1].comments, 2)
        self.assertIsNone(parse("show", "42")[1].comments)

    def test_edit_labels(self):
        _, ns = parse("edit", "42", "-l", "+new", "-l", "-old", "--no-due", "--if-version", "7")
        self.assertEqual(ns.label, ["+new", "-old"])
        self.assertTrue(ns.no_due)
        self.assertEqual(ns.if_version, 7)

    def test_comment_forms(self):
        ns = parse("comment", "GAME-42", "looks good", "--kind", "progress", "--reply-to", "#1187")[1]
        self.assertEqual((ns.key, ns.text, ns.kind, ns.reply_to), ("GAME-42", "looks good", "progress", "#1187"))
        ns = parse("comment", "--edit", "1190", "new text")[1]
        self.assertEqual((ns.edit, ns.key, ns.text), ("1190", "new text", None))
        ns = parse("comment", "42", "-")[1]
        self.assertEqual(ns.text, "-")
        ns = parse("comment", "42", "- a bullet with spaces")[1]
        self.assertEqual(ns.text, "- a bullet with spaces")

    def test_errors_are_invalid_request(self):
        cases = [("ls", "--bogus"), ("show",), ("rank", "42"), ("rank", "42", "--top", "--bottom"),
                 ("ls", "--limit", "x"), ("link", "1", "fixes", "2"), ("review", "42"),
                 ("topic",), ("profile", "nope"), ("edit", "1", "--due", "x", "--no-due")]
        for argv in cases:
            with self.assertRaises(InvalidRequest, msg=argv) as cm:
                with contextlib.redirect_stderr(io.StringIO()):
                    parse(*argv)
            self.assertEqual(cm.exception.exit_code, 1)
            self.assertTrue(cm.exception.hint.endswith("-h"), argv)

    def test_unknown_command(self):
        with self.assertRaises(InvalidRequest) as cm:
            parse("shwo", "42")
        self.assertIn("unknown command 'shwo'", cm.exception.message)
        self.assertEqual(cm.exception.hint, "did you mean: earmark show")

    def test_every_command_builds_with_help(self):
        for cmd in P.COMMANDS:
            argv = [cmd, P.SUBCOMMANDS[cmd][0]] if cmd in P.SUBCOMMANDS else [cmd]
            out = io.StringIO()
            with self.assertRaises(P.ParseExit) as cm, contextlib.redirect_stdout(out):
                parse(*argv, "-h")
            self.assertEqual(cm.exception.status, 0, cmd)
            self.assertIn(f"usage: earmark {cmd}", out.getvalue())
            self.assertLess(len(out.getvalue().splitlines()), 60, cmd)

    def test_top_help(self):
        for argv in ([], ["-h"], ["--help"], ["-h", "ls"], ["--json"]):
            out = io.StringIO()
            with self.assertRaises(P.ParseExit) as cm, contextlib.redirect_stdout(out):
                parse(*argv)
            self.assertEqual(cm.exception.status, 0)
            text = out.getvalue()
            for group in P.GROUPS:
                self.assertIn(f"  {group}  ", text)
            self.assertIn("exit: 0 ok · 1 invalid · 2 not found · 3 conflict · 4 empty · 5 busy", text)
            self.assertLessEqual(sum(1 for ln in text.splitlines() if ln.startswith("  earmark ")), 5)
            self.assertLess(len(text.splitlines()), 30)

    def test_every_command_is_in_top_help(self):
        listed = set(P.TOP_HELP.replace("|", " ").split())
        for cmd in P.COMMANDS:
            self.assertIn(cmd, listed)
        for cmd, subs in P.SUBCOMMANDS.items():
            for sub in subs:
                self.assertIn(sub, listed)

    def test_version(self):
        out = io.StringIO()
        with self.assertRaises(P.ParseExit), contextlib.redirect_stdout(out):
            parse("--version")
        self.assertRegex(out.getvalue(), r"^earmark \d+\.\d+\.\d+\n$")


if __name__ == "__main__":
    unittest.main()
