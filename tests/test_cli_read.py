"""Read commands against the recording fake board: argument mapping and exact text."""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import (DAY, contract_block, fake_board, issue, run_main,  # noqa: E402
                              summary)


def at_day():
    """Freeze the renderers' today to the samples' day."""
    return mock.patch("earmark.cli.render.today", return_value=DAY)


class LsTests(unittest.TestCase):
    def test_contract_text(self):
        with fake_board(), at_day():
            code, out, err = run_main(["ls"])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, contract_block("KEY      STATUS") + "\n")

    def test_filters_reach_the_board(self):
        with fake_board() as boards:
            run_main(["ls", "-t", "ui-ux", "-t", "web", "--exact", "-s", "todo,in_review",
                      "-p", "urgent,high", "--min-priority", "high", "-a", "me", "--reviewer", "none",
                      "--reporter", "hector", "--type", "bug,chore", "-l", "regression", "-l", "a,b",
                      "--due-before", "+3d", "--overdue", "--updated-since", "2h", "--created-since",
                      "1w", "--ready", "--blocked", "--stale", "-q", "eye", "--parent", "30",
                      "--sort", "due", "--desc", "--limit", "5", "--offset", "10", "--as", "w-1"])
        (args, kw), = boards[0].called("list_issues")
        self.assertEqual(kw, dict(topic=["ui-ux", "web"], exact=True, status="todo,in_review",
                                  priority=["urgent", "high"], min_priority="high", assignee="me",
                                  reviewer="none", reporter="hector", type=["bug", "chore"],
                                  label=["regression", "a", "b"], q="eye", due_before="+3d",
                                  overdue=True, updated_since="2h", created_since="1w", ready=True,
                                  blocked=True, stale=True, parent="30", sort="due", desc=True,
                                  limit=5, offset=10))

    def test_defaults(self):
        with fake_board() as boards:
            run_main(["ls"])
        (_, kw), = boards[0].called("list_issues")
        self.assertEqual((kw["status"], kw["sort"], kw["limit"], kw["offset"], kw["topic"]),
                         (None, "priority", 50, 0, []))

    def test_count(self):
        with fake_board(list_issues={"items": [summary()], "total": 212, "limit": 1, "offset": 0}) as boards:
            code, out, _ = run_main(["ls", "--count", "-s", "all"])
        self.assertEqual((code, out), (0, "212\n"))
        kw = boards[0].called("list_issues")[0][1]
        self.assertEqual((kw["status"], kw["limit"], kw["offset"]), ("all", 1, 0))
        with fake_board(list_issues={"items": [], "total": 7, "limit": 1, "offset": 0}):
            _, out, _ = run_main(["ls", "--count", "--json"])
        self.assertEqual(json.loads(out)["data"], {"total": 7})

    def test_fields_and_bad_field(self):
        with fake_board(), at_day():
            _, out, _ = run_main(["ls", "--fields", "key,status"])
        self.assertEqual(out.splitlines()[:2], ["KEY      STATUS", "GAME-42  in_progress"])
        with fake_board() as boards:
            code, out, err = run_main(["ls", "--fields", "key,nope"])
        self.assertEqual(code, 1)
        self.assertIn("error: ls: unknown field 'nope'", err)
        self.assertIn("hint: fields: key,", err)
        self.assertEqual(boards, [], "validated before opening the board")

    def test_closed_noun(self):
        page = {"items": [summary(status="done")], "total": 1, "limit": 50, "offset": 0}
        with fake_board(list_issues=page), at_day():
            _, out, _ = run_main(["ls", "-s", "closed"])
        self.assertEqual(out.splitlines()[-1], "1 of 1 closed issues")


class ShowTests(unittest.TestCase):
    def test_contract_text(self):
        with fake_board() as boards, at_day():
            code, out, _ = run_main(["show", "game-42"])
        self.assertEqual(code, 0)
        self.assertEqual(out, contract_block("GAME-42  Eye color") + "\n")
        self.assertEqual(boards[0].called("get_issue"), [(("game-42",), {"comments": 0})])

    def test_fields(self):
        with fake_board():
            _, out, _ = run_main(["show", "42", "-f", "status,assignee,version"])
        self.assertEqual(out, "status: in_progress\nassignee: claude-ui\nversion: 7\n")
        with fake_board():
            _, out, _ = run_main(["show", "42", "-f", "status,blocked_by,children", "--json"])
        data = json.loads(out)["data"]
        self.assertEqual(list(data), ["status", "blocked_by", "children"])
        self.assertEqual((data["status"], data["blocked_by"]), ("in_progress", ["GAME-77"]))
        self.assertEqual([c["key"] for c in data["children"]], ["GAME-43", "GAME-44"])
        with fake_board():
            code, _, err = run_main(["show", "42", "-f", "statu"])
        self.assertEqual(code, 1)
        self.assertIn("unknown field 'statu'", err)

    def test_last_comments(self):
        with fake_board() as boards, at_day():
            _, out, _ = run_main(["show", "42", "-c"])
        self.assertEqual(boards[0].called("get_issue"), [(("42",), {"comments": 5})])
        self.assertIn("last 2 of 12 comments:\n#1187  claude-ui  progress  11-03 14:05\n  Moved", out)

    def test_json_is_the_issue(self):
        with fake_board(get_issue=lambda key, comments=0: issue()):
            _, out, _ = run_main(["show", "42", "--json"])
        self.assertEqual(json.loads(out)["data"]["links"]["blocks"], ["GAME-51"])


class CommentsInboxTests(unittest.TestCase):
    def test_comments_contract_text(self):
        with fake_board() as boards:
            code, out, _ = run_main(["comments", "42"])
        self.assertEqual(out, contract_block("#1187  claude-ui") + "\n")
        self.assertEqual(boards[0].called("list_comments"),
                         [(("42",), {"since": None, "new": False, "last": None, "limit": 100})])

    def test_comments_json_keeps_iso_times(self):
        with fake_board():
            _, out, _ = run_main(["comments", "42", "--json"])
        self.assertEqual(json.loads(out)["data"]["items"][0]["created_at"], "2026-11-03T14:05:09Z")

    def test_comments_flags(self):
        with fake_board() as boards:
            run_main(["comments", "42", "--since", "#1187", "--last", "3", "--limit", "9"])
        self.assertEqual(boards[0].called("list_comments")[0][1],
                         {"since": 1187, "new": False, "last": 3, "limit": 9})
        with fake_board(list_comments={"items": [], "last_id": 1190, "has_more": False}) as boards:
            _, out, _ = run_main(["comments", "42", "--new", "--as", "w-1"])
        self.assertTrue(boards[0].called("list_comments")[0][1]["new"])
        self.assertEqual(out, "comments: nothing new after #1190\n")
        code, _, err = run_main(["comments", "42", "--since", "abc"])
        self.assertEqual(code, 1)
        self.assertIn("--since must be a number", err)
        code, _, err = run_main(["comments", "42", "--since", "1", "--new"])
        self.assertEqual(code, 1)
        self.assertIn("not allowed with argument", err)

    def test_inbox(self):
        with fake_board() as boards:
            _, out, _ = run_main(["inbox", "--as", "claude-ui"])
        self.assertEqual(boards[0].called("inbox"), [((), {"peek": False, "limit": 50})])
        self.assertEqual(out.splitlines()[0], "GAME-42  #1187  claude-ui  progress  11-03 14:05")
        with fake_board(inbox={"items": [], "last_id": None, "has_more": False, "cursor": 1190}) as boards:
            code, out, _ = run_main(["inbox", "--peek", "--as", "claude-ui"])
        self.assertEqual(boards[0].called("inbox"), [((), {"peek": True, "limit": 50})])
        self.assertEqual((code, out), (0, "inbox: nothing new after #1190\n"))


class TopicsTests(unittest.TestCase):
    def test_contract_text(self):
        with fake_board() as boards:
            _, out, _ = run_main(["topics"])
        self.assertEqual(out, contract_block("ui-ux    ") + "\n")
        self.assertEqual(boards[0].called("topics"),
                         [((), {"root": None, "depth": None, "include_archived": False})])

    def test_root_depth_all(self):
        with fake_board() as boards:
            run_main(["topics", "ui-ux", "--depth", "2", "--all"])
        self.assertEqual(boards[0].called("topics")[0][1],
                         {"root": "ui-ux", "depth": 2, "include_archived": True})


if __name__ == "__main__":
    unittest.main()
