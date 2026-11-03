"""`earmark check [--peek]`: the commands.md samples byte for byte from fixture dicts shaped like
Board.check(), plus each kind of change, the lease warning and the cut list."""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import (DAY, CliBoardCase, contract_block, contract_line,  # noqa: E402
                              fake_board, run_main)

from earmark.board import Board  # noqa: E402

from earmark.cli import render as R  # noqa: E402

SINCE = "2026-11-03T14:05:09Z"
HOLDING = [{"key": "GAME-1", "lease_until": "2026-11-03T15:45:09Z", "lease_left_min": 100}]


def item(key, *changes, priority="medium", blocks_open=0, comments=0, questions=0, files=0):
    return {"key": key, "priority": priority, "effective_priority": priority, "blocks_open": blocks_open,
            "action": None, "changes": list(changes), "comments": comments, "questions": questions,
            "files": files}


def feed(changed=(), todo=9, more=0, holding=HOLDING, since=SINCE, waiting=0, reviews=0,
         role="worker") -> dict:
    return {"agent": "claude-ui", "role": role, "since": since, "holding": holding,
            "ready_for_you": todo, "waiting_on_blockers": waiting, "reviews_waiting": reviews,
            "todo_for_you": todo, "changed": list(changed), "more": more, "cursor": 42}


SAMPLE = feed([
    item("GAME-77", {"kind": "assigned", "by": "grok-pm"}, priority="high", blocks_open=1),
    item("GAME-3", {"kind": "rejected", "by": "codex-review"}, comments=2),
    item("GAME-1", comments=1, questions=1, files=1),
    item("GAME-12", {"kind": "unblocked", "by": "codex-art", "by_issue": "GAME-31",
                     "old": "GAME-31 done", "new": "todo"}),
    item("GAME-9", {"kind": "field", "field": "priority", "old": "medium", "new": "high", "by": "grok-pm"},
         {"kind": "field", "field": "title", "by": "grok-pm"}),
], todo=10, waiting=1)


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

    def test_new_for_you_leads(self):
        self.assertEqual(R.check_parts(item("GAME-1", {"kind": "field", "field": "links"},
                                            {"kind": "assigned", "by": "pm"}, priority="high", blocks_open=2)),
                         ["new for you (assigned by pm)", "high", "blocks:2", "links changed"])

    def test_lease_warning_and_more(self):
        res = feed([item("GAME-5", comments=1)], holding=[
            {"key": "GAME-1", "lease_until": "x", "lease_left_min": 12},
            {"key": "GAME-2", "lease_until": "x", "lease_left_min": 120},
            {"key": "GAME-4", "lease_until": "x", "lease_left_min": -3}], more=4)
        self.assertEqual(R.render_check(res, day=DAY).splitlines(), [
            "check: since 14:05:09Z · holding GAME-1 (lease 12m, comment to renew), GAME-2 (lease 2h), "
            "GAME-4 (lease expired, comment to renew) · ready for you: 9",
            "GAME-5  1 new comment",
            "+4 more"])
        self.assertIn("(lease 12m, comment to renew)", contract_line("(lease 12m"))

    def test_header_without_holding_and_from_another_day(self):
        self.assertEqual(R.render_check(feed(holding=[], todo=0, since="2026-11-02T23:59:00Z"), day=DAY),
                         "check: nothing new since 11-02 23:59:00Z · ready for you: 0")
        self.assertEqual(R.render_check(feed(holding=[], since=None), day=DAY),
                         "check: nothing new · ready for you: 9")

    def test_header_counts_by_role(self):
        """Workers and pms: what `next` could claim, plus their issues parked on open blockers;
        reviewers: what `next --review` could claim. Payloads from before 1.3 still render."""
        def head(**kw):
            return R.render_check(feed(holding=[], **kw), day=DAY)
        self.assertEqual(head(todo=1, waiting=1),
                         "check: nothing new since 14:05:09Z · ready for you: 1 · 1 waiting on blockers")
        self.assertEqual(head(todo=0, role="pm", reviews=4), "check: nothing new since 14:05:09Z · "
                                                             "ready for you: 0")
        self.assertEqual(head(todo=1, waiting=2, reviews=0, role="reviewer"),
                         "check: nothing new since 14:05:09Z · reviews waiting: 0")
        old = {"agent": "a", "since": SINCE, "holding": [], "todo_for_you": 3, "changed": [],
               "more": 0}
        self.assertEqual(R.render_check(old, day=DAY), "check: nothing new since 14:05:09Z · "
                                                       "ready for you: 3")


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


@unittest.skipUnless(hasattr(Board, "check") and hasattr(Board, "pending_changes"),
                     "the board has no check feed yet")
class RealCheckTests(CliBoardCase):
    def test_feed_peek_and_hint(self):
        self.ok("join", "worker", as_="w1")
        self.assertTrue(self.ok("check", as_="w1").startswith("check: nothing new since 10:02:11Z"))
        self.ok("new", "Eye", "-p", "high", as_="hector")
        self.ok("assign", "1", "w1", as_="hector")
        self.ok("comment", "1", "which tab? @w1", "--kind", "question", as_="hector")
        peek = self.ok("check", "--peek", as_="w1").splitlines()
        self.assertEqual(peek[0], "check: since 10:02:11Z · ready for you: 1")
        self.assertEqual(peek[1], "GAME-1  new for you (assigned by hector) · high · "
                                  "1 new comment, question for you")
        self.assertEqual(self.ok("claim", "1", as_="w1", hint=True).splitlines()[-1],
                         "· 1 change for you: earmark check")
        self.assertEqual(self.ok("check", as_="w1").splitlines()[1:], peek[1:])
        self.assertTrue(self.ok("check", as_="w1").startswith("check: nothing new"))
        self.assertEqual(self.ok("comment", "1", "on it", as_="w1", hint=True).count("changes for you"), 0)
        env = self.json("check", "--peek", as_="w1")
        self.assertEqual((env["data"]["changed"], env["data"]["holding"][0]["key"]), ([], "GAME-1"))

    def test_a_comment_next_or_show_new_printed_is_not_reported_again(self):
        self.ok("join", "worker", as_="w1")
        self.ok("join", "reviewer", as_="r1")
        self.ok("new", "Eye", as_="hector")
        self.ok("claim", "1", as_="w1")
        self.ok("review", "1", "-m", "ready", as_="w1")
        self.ok("next", as_="r1")
        self.ok("reject", "1", "-m", "fails on reload", as_="r1")
        out = self.ok("next", as_="w1", hint=True).splitlines()
        self.assertEqual(out[-3:], ["#2  r1  changes  11-03 10:02", "  fails on reload",
                                    "· 1 change for you: earmark check"])  # the reject, not the note
        self.assertEqual(self.ok("check", as_="w1").splitlines()[1:], ["GAME-1  rejected (r1)"])
        self.ok("comment", "1", "also the hover state", as_="hector")
        self.assertIn("also the hover state", self.ok("show", "1", "--new", as_="w1"))
        self.assertTrue(self.ok("check", as_="w1").startswith("check: nothing new"))
        self.ok("comment", "1", "and the focus ring", as_="hector")
        self.assertEqual(self.ok("check", as_="w1").splitlines()[1:], ["GAME-1  1 new comment"])


if __name__ == "__main__":
    unittest.main()
