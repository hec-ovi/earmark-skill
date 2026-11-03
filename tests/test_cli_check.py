"""`earmark check [--peek]`: the commands.md samples byte for byte from fixture dicts shaped like
Board.check(), plus each kind of change, the lease warning and the cut list."""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import DAY, contract_block, contract_line, fake_board, run_main  # noqa: E402

from earmark.cli import render as R  # noqa: E402

SINCE = "2026-11-03T14:05:09Z"
HOLDING = [{"key": "GAME-1", "lease_until": "2026-11-03T15:45:09Z", "lease_left_min": 100}]


def item(key, *changes, priority="medium", blocks_open=0, comments=0, questions=0, files=0):
    return {"key": key, "priority": priority, "effective_priority": priority, "blocks_open": blocks_open,
            "action": None, "changes": list(changes), "comments": comments, "questions": questions,
            "files": files}


def feed(changed=(), todo=9, more=0, holding=HOLDING, since=SINCE) -> dict:
    return {"agent": "claude-ui", "since": since, "holding": holding, "todo_for_you": todo,
            "changed": list(changed), "more": more, "cursor": 42}


SAMPLE = feed([
    item("GAME-77", {"kind": "assigned", "by": "grok-pm"}, priority="high", blocks_open=1),
    item("GAME-3", {"kind": "rejected", "by": "codex-review"}, comments=2),
    item("GAME-1", comments=1, questions=1, files=1),
    item("GAME-12", {"kind": "unblocked", "by": "codex-art", "by_issue": "GAME-31",
                     "old": "GAME-31 done", "new": "todo"}),
    item("GAME-9", {"kind": "field", "field": "priority", "old": "medium", "new": "high", "by": "grok-pm"},
         {"kind": "field", "field": "title", "by": "grok-pm"}),
], todo=10)


class CheckRenderTests(unittest.TestCase):
    def test_nothing_new_sample(self):
        self.assertEqual(R.render_check(feed(), day=DAY), contract_block("check: nothing new since"))

    def test_changes_sample(self):
        self.assertEqual(R.render_check(SAMPLE, day=DAY), contract_block("check: since 14:05:09Z"))

    def test_each_kind(self):
        cases = [
            ({"kind": "status", "old": "todo", "new": "in_progress", "by": "x"}, "status todo→in_progress"),
            ({"kind": "approved", "by": "codex-qa"}, "approved (codex-qa)"),
            ({"kind": "review_cap", "by": "codex-qa"}, "review rounds exhausted"),
            ({"kind": "takeover", "by": "codex-2"}, "taken over (codex-2)"),
            ({"kind": "field", "field": "due", "old": None, "new": "2026-11-07"}, "due changed"),
            ({"kind": "unblocked", "by_issue": "GAME-31"}, "unblocked (GAME-31 done)"),
            ({"kind": "unblocked", "old": "GAME-31 canceled"}, "unblocked (GAME-31 canceled)"),
            ({"kind": "assigned"}, "new for you"),
            ({"kind": "some_new_kind"}, "some new kind"),
        ]
        for change, text in cases:
            with self.subTest(text=text):
                self.assertEqual(R.check_change(change), text)

    def test_counts(self):
        self.assertEqual(R.check_parts(item("GAME-1", comments=3, files=2)), ["3 new comments", "2 new files"])
        self.assertEqual(R.check_parts(item("GAME-1", comments=2, questions=2)),
                         ["2 new comments, 2 questions for you"])
        self.assertEqual(R.check_parts(item("GAME-1", questions=1)), ["question for you"])
        self.assertEqual(R.check_parts(item("GAME-1", {"kind": "assigned", "by": "pm"}, priority="low")),
                         ["new for you (assigned by pm)", "low"])

    def test_lease_warning_and_more(self):
        res = feed([item("GAME-5", comments=1)], holding=[
            {"key": "GAME-1", "lease_until": "x", "lease_left_min": 12},
            {"key": "GAME-2", "lease_until": "x", "lease_left_min": 120},
            {"key": "GAME-4", "lease_until": "x", "lease_left_min": -3}], more=4)
        self.assertEqual(R.render_check(res, day=DAY).splitlines(), [
            "check: since 14:05:09Z · holding GAME-1 (lease 12m, comment to renew), GAME-2 (lease 2h), "
            "GAME-4 (lease expired, comment to renew) · todo for you: 9",
            "GAME-5  1 new comment",
            "+4 more"])
        self.assertIn("(lease 12m, comment to renew)", contract_line("(lease 12m"))

    def test_header_without_holding_and_from_another_day(self):
        self.assertEqual(R.render_check(feed(holding=[], todo=0, since="2026-11-02T23:59:00Z"), day=DAY),
                         "check: nothing new since 11-02 23:59:00Z · todo for you: 0")
        self.assertEqual(R.render_check(feed(holding=[], since=None), day=DAY),
                         "check: nothing new · todo for you: 9")


class CheckCommandTests(unittest.TestCase):
    def test_text_json_and_peek(self):
        with fake_board(check=lambda peek=False: SAMPLE) as boards:
            from unittest import mock
            with mock.patch("earmark.cli.render.today", return_value=DAY):
                code, out, err = run_main(["check", "--as", "claude-ui"])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, contract_block("check: since 14:05:09Z") + "\n")
        self.assertEqual(boards[0].called("check"), [((), {"peek": False})])
        with fake_board(check=lambda peek=False: SAMPLE) as boards:
            _, out, _ = run_main(["check", "--peek", "--json", "--as", "claude-ui"])
        self.assertEqual(boards[0].called("check"), [((), {"peek": True})])
        self.assertEqual(json.loads(out)["data"], SAMPLE)

    def test_listed_in_help(self):
        code, out, _ = run_main(["-h"])
        self.assertIn(" check ", out)


if __name__ == "__main__":
    unittest.main()
