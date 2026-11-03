"""Text output shows control characters from board text as visible escapes; JSON is untouched."""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import CliBoardCase  # noqa: E402

from earmark.cli import render as R  # noqa: E402

TITLE = "Title \x1b]0;pwned\x07 end"
BODY = "line\x1b[2K\rstatus: done   (fake)\x1b]52;c;ZWNobyBoaQ==\x07"
COMMENT = "ok\x1b[1A\x1b[2Kapproved by owner \x9b31m \x7f"


def raw_controls(text: str) -> list[str]:
    return [ch for ch in text if (ord(ch) < 0x20 and ch not in "\n\t") or 0x7F <= ord(ch) < 0xA0]


class EscapeFunctionTests(unittest.TestCase):
    def test_c0_del_c1_become_visible(self):
        self.assertEqual(R.escape_controls("a\x1b[2Kb\x07c\x00d\x7fe\x9bf\rg"),
                         "a\\x1b[2Kb\\x07c\\x00d\\x7fe\\x9bf\\x0dg")

    def test_tab_newline_and_crlf(self):
        self.assertEqual(R.escape_controls("a\tb\nc\r\nd"), "a\tb\nc\nd")

    def test_color_keeps_only_style_codes(self):
        styled = R.Style(True).bold("GAME-1") + " x\x1b[2K\x1b]0;t\x07"
        self.assertEqual(R.escape_controls(styled, color=True),
                         "\x1b[1mGAME-1\x1b[0m x\\x1b[2K\\x1b]0;t\\x07")
        self.assertEqual(R.escape_controls(styled), "\\x1b[1mGAME-1\\x1b[0m x\\x1b[2K\\x1b]0;t\\x07")

    def test_every_style_code_survives(self):
        codes = {"0", "1", "2", *R.Style.STATUS.values(), *R.Style.PRIORITY.values(),
                 *R.Style.FLAG.values(), "32", "33"}
        for code in sorted(c for c in codes if c):
            seq = f"\x1b[{code}m"
            self.assertEqual(R.escape_controls(seq, color=True), seq, code)

    def test_one_line_values(self):
        self.assertEqual(R.dash("two\nlines"), "two\\nlines")
        self.assertEqual(R.one_line("a\r\nb\rc"), "a\\nb\\rc")

    def test_json_text(self):
        self.assertEqual(R.json_text('{"t": "a\x9bb\x7f"}'), '{"t": "a\\u009bb\\u007f"}')
        self.assertEqual(json.loads(R.json_text(json.dumps({"t": "a\x9b"}, ensure_ascii=False))),
                         {"t": "a\x9b"})


class EscapedOutputTests(CliBoardCase):
    def setUp(self):
        super().setUp()
        self.ok("join", "worker", as_="w1")
        self.ok("new", TITLE, "-b", BODY, as_="w1")
        self.ok("comment", "1", COMMENT, as_="w1")
        self.ok("comment", "1", "ping @w2 \x1b[31mred", as_="hector")

    def test_show_comments_inbox_ls(self):
        for argv in (("show", "1", "-c", "1"), ("comments", "1"), ("ls",), ("show", "1", "-f", "title,body"),
                     ("inbox", "--peek")):
            with self.subTest(argv=argv):
                out = self.ok(*argv, as_="w1")
                self.assertEqual(raw_controls(out), [], out)
        show = self.ok("show", "1", "-c", "2", as_="w1")
        self.assertIn("Title \\x1b]0;pwned\\x07 end", show)
        self.assertIn("line\\x1b[2K\\x0dstatus: done", show)
        self.assertIn("  ok\\x1b[1A\\x1b[2Kapproved by owner \\x9b31m \\x7f", show)

    def test_json_is_unchanged(self):
        env = self.json("show", "1", "-c", "2", as_="w1")
        self.assertEqual(env["data"]["title"], TITLE)
        self.assertEqual(env["data"]["body"], BODY)
        self.assertEqual(env["data"]["comments"][0]["body"], COMMENT)

    def test_errors_are_escaped(self):
        err = self.fails(1, "new", "x", "-t", "no\x1b[2Kpe", as_="w1")
        self.assertEqual(raw_controls(err), [], err)

    def test_title_with_line_break_stays_on_its_line(self):
        self.ok("new", "first\nGAME-9  done  fake row", as_="w1")
        rows = self.ok("ls", "--fields", "key,title").splitlines()
        self.assertEqual(len(rows), 4)  # header, two issues, footer
        self.assertTrue(rows[2].startswith("GAME-2  first"), rows)
        self.assertEqual(R.render_ls({"items": [{"key": "GAME-3", "title": "a\nb"}], "total": 1},
                                     fields=["key", "title"]).splitlines()[1], "GAME-3  a\\nb")

    def test_export_text_is_valid_json(self):
        self.ok("comment", "1", "c1 \x9b", as_="w1")
        out = self.ok("export")
        self.assertEqual(raw_controls(out), [])
        json.loads(out)
        self.assertIn("Title \\u001b]0;pwned\\u0007 end", out)
        self.assertIn("c1 \\u009b", out)


if __name__ == "__main__":
    unittest.main()
