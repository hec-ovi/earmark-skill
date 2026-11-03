"""`show KEY… [-f FIELDS] [--new | --since ID | --last N] [-c N]`: the commands.md selection table,
byte for byte, against the recording fake board, then end to end on a real board."""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import (COMMENTS, DAY, CliBoardCase, contract_block, fake_board,  # noqa: E402
                              issue, run_main, summary)

from earmark.errors import NotFound  # noqa: E402

ISSUES = {
    "GAME-42": issue(),
    "GAME-43": issue(key="GAME-43", id=43, title="Picker", status="todo", assignee=None,
                     lease_until=None, links={}, children=[], body="one\ntwo"),
    "GAME-51": issue(key="GAME-51", id=51, title="Mute", status="blocked", assignee="codex-api",
                     lease_until=None, links={}, children=[], body=""),
}


def canon(key: str) -> str:
    s = str(key).upper()
    return s if s.startswith("GAME-") else f"GAME-{s}"


def get_issue(key, comments=0):
    k = canon(key)
    if k not in ISSUES:
        raise NotFound(f"no issue {k}", hint="earmark ls")
    data = json.loads(json.dumps(ISSUES[k]))
    if comments:
        data["comments"] = COMMENTS[-comments:]
    return data


PAGE = {"items": COMMENTS, "last_id": 1190, "has_more": False}
COMMENT_LINES = contract_block("#1187  claude-ui").splitlines()[:-1]  # without the footer


def at_day():
    return mock.patch("earmark.cli.render.today", return_value=DAY)


def show(*argv, **results):
    results = {"get_issue": get_issue, "list_comments": PAGE, **results}
    with fake_board(**results) as boards, at_day():
        code, out, err = run_main(["show", *argv])
    return code, out, err, boards[0] if boards else None


class SelectionTableTests(unittest.TestCase):
    """Each row of the commands.md `show` table."""

    def test_one_field(self):
        code, out, err, _ = show("GAME-42", "-f", "status")
        self.assertEqual((code, out, err), (0, "status: in_progress\n", ""))

    def test_a_few_fields_in_the_order_named(self):
        _, out, _, _ = show("GAME-42", "-f", "status,title,assignee")
        self.assertEqual(out, "status: in_progress\ntitle: Eye color widget resets on reload\n"
                              "assignee: claude-ui\n")
        _, out, _, _ = show("GAME-42", "-f", "assignee,status")
        self.assertEqual(out, "assignee: claude-ui\nstatus: in_progress\n")

    def test_fields_and_new_comments(self):
        code, out, _, b = show("GAME-42", "-f", "status,title,comments", "--new", "--as", "claude-ui")
        self.assertEqual(code, 0)
        self.assertEqual(out, "\n".join(["status: in_progress", "title: Eye color widget resets on reload",
                                         "2 new comments:", *COMMENT_LINES]) + "\n")
        self.assertEqual(b.called("get_issue"), [(("GAME-42",), {"comments": 0})])
        self.assertEqual(b.called("list_comments"),
                         [(("GAME-42",), {"since": None, "new": True, "last": None, "limit": 100})])

    def test_only_the_files(self):
        att = [{"id": 12, "issue": "GAME-42", "comment_id": 1187, "name": "eye-picker-bug.png",
                "mime": "image/png", "kind": "image", "size": 188416, "sha256": "3f", "author": "claude-ui",
                "created_at": "2026-11-03T14:05:09Z", "url": "/api/attachments/12/raw",
                "path": "/work/game/.earmark/files/3f/3fa1…c9.png"},
               {"id": 13, "issue": "GAME-42", "comment_id": None, "name": "ambience-v2.ogg",
                "mime": "audio/ogg", "kind": "audio", "size": 2202010, "sha256": "a0", "author": "agy-audio",
                "created_at": "2026-11-03T14:05:09Z", "url": "/api/attachments/13/raw",
                "path": "/work/game/.earmark/files/a0/a07e…41.ogg"}]
        with fake_board(get_issue=lambda key, comments=0: issue(attachments=att)):
            _, out, _ = run_main(["show", "GAME-42", "-f", "attachments"])
        self.assertEqual(out, contract_block("ID   KIND") + "\n")

    def test_many_issues_one_line_each(self):
        code, out, err, b = show("GAME-42", "GAME-43", "GAME-51", "-f", "status,assignee")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, contract_block("GAME-42  status: in_progress  assignee") + "\n")
        self.assertEqual([a[0] for a, _ in b.called("get_issue")], ["GAME-42", "GAME-43", "GAME-51"])


class CommentPartTests(unittest.TestCase):
    def test_c_adds_last_n_to_fields(self):
        _, out, _, b = show("42", "-f", "status", "-c", "1")
        self.assertEqual(b.called("get_issue"), [(("42",), {"comments": 1})])
        self.assertEqual(out, "\n".join(["status: in_progress", "last 1 of 12 comments:",
                                         *COMMENT_LINES[2:]]) + "\n")

    def test_comments_default_to_the_last_five(self):
        _, out, _, b = show("42", "-f", "comments,status")
        self.assertEqual(b.called("get_issue"), [(("42",), {"comments": 5})])
        self.assertEqual(b.called("list_comments"), [])
        self.assertEqual(out.splitlines()[0], "last 2 of 12 comments:")
        self.assertEqual(out.splitlines()[-1], "status: in_progress")  # the order named

    def test_since_and_last(self):
        _, out, _, b = show("42", "-f", "comments", "--since", "#1187", "--last", "3")
        self.assertEqual(b.called("list_comments")[0][1], {"since": 1187, "new": False, "last": 3, "limit": 100})
        self.assertEqual(out.splitlines()[0], "2 comments after #1187:")
        _, out, _, b = show("42", "-f", "status", "--last", "2")
        self.assertEqual(b.called("get_issue"), [(("42",), {"comments": 2})])
        self.assertIn("last 2 of 12 comments:", out)

    def test_flags_imply_the_comments_part(self):
        _, out, _, _ = show("42", "-f", "status", "--new", "--as", "claude-ui")
        self.assertEqual(out.splitlines()[:2], ["status: in_progress", "2 new comments:"])

    def test_empty_and_cut_selections(self):
        empty = {"items": [], "last_id": 1190, "has_more": False}
        _, out, _, _ = show("42", "-f", "comments", "--new", "--as", "w", list_comments=empty)
        self.assertEqual(out, "comments: nothing new after #1190\n")
        _, out, _, _ = show("42", "-f", "comments", "--since", "1190", list_comments=empty)
        self.assertEqual(out, "comments: nothing after #1190\n")
        cut = dict(PAGE, has_more=True)
        _, out, _, _ = show("42", "-f", "comments", "--new", "--as", "w", list_comments=cut)
        self.assertEqual(out.splitlines()[-1], "more: earmark comments GAME-42 --new")
        _, out, _, _ = show("42", "-f", "comments", "--since", "1", list_comments=cut)
        self.assertEqual(out.splitlines()[-1], "more: earmark comments GAME-42 --since 1190")

    def test_new_with_last_is_refused(self):
        code, _, err, b = show("42", "--new", "--last", "2", "--as", "w")
        self.assertEqual(code, 1)
        self.assertIn("give either --new or --last", err)
        self.assertIsNone(b, "refused before opening the board")
        code, _, err, _ = show("42", "--new", "--since", "3")
        self.assertEqual(code, 1)

    def test_full_view_with_new_comments(self):
        _, out, _, _ = show("42", "--new", "--as", "claude-ui")
        self.assertTrue(out.startswith(contract_block("GAME-42  Eye color") + "\n\n2 new comments:\n#1187"), out)
        empty = {"items": [], "last_id": 1190, "has_more": False}
        _, out, _, _ = show("42", "--new", "--as", "claude-ui", list_comments=empty)
        self.assertEqual(out, contract_block("GAME-42  Eye color") + "\n")


class SeveralKeyTests(unittest.TestCase):
    def test_parts_make_blocks(self):
        _, out, _, _ = show("42", "43", "-f", "status,body")
        self.assertEqual(out, "GAME-42  status: in_progress\nbody: <description>\n\n"
                              "GAME-43  status: todo\nbody: one\n  two\n")

    def test_keys_align(self):
        ISSUES["GAME-7"] = issue(key="GAME-7", id=7, status="done")
        try:
            _, out, _, _ = show("7", "42", "-f", "status")
        finally:
            del ISSUES["GAME-7"]
        self.assertEqual(out, "GAME-7   status: done\nGAME-42  status: in_progress\n")

    def test_full_view_blocks(self):
        _, out, _, _ = show("42", "43")
        first, second = out.split("\n\nGAME-43  Picker\n")
        self.assertEqual(first, contract_block("GAME-42  Eye color"))
        self.assertTrue(second.startswith("status: todo"))

    def test_duplicates_are_shown_once(self):
        _, out, _, b = show("42", "game-42", "GAME-42", "-f", "status")
        self.assertEqual(out, "status: in_progress\n")
        self.assertEqual(len(b.called("get_issue")), 1)

    def test_missing_keys(self):
        code, out, err, _ = show("42", "99", "51", "100", "-f", "status")
        self.assertEqual(code, 2)
        self.assertEqual(out, "GAME-42  status: in_progress\nGAME-51  status: blocked\n")
        self.assertEqual(err, "error: no issue GAME-99, GAME-100\nhint: earmark ls\n")
        code, out, err, _ = show("98", "99", "-f", "status")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("error: no issue GAME-98, GAME-99", err)

    def test_json(self):
        _, out, _, _ = show("42", "43", "-f", "status,comments", "--json")
        data = json.loads(out)["data"]
        self.assertEqual([list(d) for d in data], [["key", "status", "comments"]] * 2)
        self.assertEqual(data[0]["comments"][0]["created_at"], "2026-11-03T14:05:09Z")
        _, out, _, _ = show("42", "43", "--json")
        self.assertEqual([d["key"] for d in json.loads(out)["data"]], ["GAME-42", "GAME-43"])
        code, out, _, _ = show("42", "99", "-f", "status", "--json")
        env = json.loads(out)
        self.assertEqual((code, env["ok"], env["error"]["code"]), (2, False, "not_found"))
        self.assertEqual(env["error"]["details"], {"missing": ["GAME-99"],
                                                   "found": [{"key": "GAME-42", "status": "in_progress"}]})

    def test_single_key_json_shapes(self):
        _, out, _, _ = show("42", "-f", "status,links,attachments,body", "--json")
        data = json.loads(out)["data"]
        self.assertEqual(data["links"]["blocked_by"], ["GAME-77"])
        self.assertEqual((data["attachments"], data["body"]), ([], "<description>"))
        _, out, _, _ = show("42", "--new", "--json", "--as", "w")
        self.assertEqual(json.loads(out)["data"]["comments"], COMMENTS)


class RealBoardTests(CliBoardCase):
    def setUp(self):
        super().setUp()
        self.ok("join", "worker", "--cli", "claude-code", as_="claude-ui")
        self.ok("new", "Eye color", "-b", "line one\nline two", as_="hector")
        self.ok("new", "Mute button", as_="hector")
        self.ok("comment", "1", "first", as_="hector")
        self.ok("comment", "1", "second", as_="hector")

    def test_several_keys_and_new_comments(self):
        self.assertEqual(self.ok("show", "1", "2", "-f", "status,assignee"),
                         "GAME-1  status: todo  assignee: -\nGAME-2  status: todo  assignee: -\n")
        out = self.ok("show", "1", "-f", "title,comments", "--new", as_="claude-ui")
        self.assertEqual(out, "title: Eye color\n2 new comments:\n#1  hector  note  11-03 10:02\n  first\n"
                              "#2  hector  note  11-03 10:02\n  second\n")
        self.assertEqual(self.ok("show", "1", "-f", "comments", "--new", as_="claude-ui"),
                         "comments: nothing new after #2\n")
        out = self.ok("show", "1", "2", "-f", "new_comments,body", as_="claude-ui")
        self.assertEqual(out, "GAME-1  new_comments: 0\nbody: line one\n  line two\n\n"
                              "GAME-2  new_comments: 0\nbody: -\n")

    def test_c_with_fields_and_missing_key(self):
        out = self.ok("show", "1", "-f", "status", "-c", "1")
        self.assertEqual(out, "status: todo\nlast 1 of 2 comments:\n#2  hector  note  11-03 10:02\n  second\n")
        code, out, err = self.cli("show", "1", "9", "-f", "status")
        self.assertEqual((code, out), (2, "status: todo\n".replace("status", "GAME-1  status")))
        self.assertIn("error: no issue GAME-9", err)


if __name__ == "__main__":
    unittest.main()
