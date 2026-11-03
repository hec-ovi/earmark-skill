"""End to end on a real temp board: the claim, review and status flow through the CLI."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import CliBoardCase  # noqa: E402


class WorkFlowTests(CliBoardCase):
    def setUp(self):
        super().setUp()
        self.ok("topic", "add", "ui-ux/video-calls", as_="hector")
        self.ok("join", "worker", as_="claude-ui")
        self.ok("join", "reviewer", as_="codex-qa")
        self.ok("new", "Eye color widget resets on reload", "-p", "high", as_="hector")
        self.ok("new", "Mute button state", "-t", "ui-ux/video-calls", as_="hector")

    def test_next_claims_in_show_format(self):
        out = self.ok("next", as_="claude-ui").splitlines()
        self.assertEqual(out[0], "GAME-1  Eye color widget resets on reload")
        self.assertEqual(out[1], "status: in_progress   priority: high   type: task   version: 2")
        self.assertEqual(out[3], "assignee: claude-ui (lease until 2026-11-03T12:02:11Z)   "
                                 "reviewer: -   reporter: hector")
        dry = self.ok("next", "--dry-run", "--topic", "ui-ux", as_="claude-ui")
        self.assertTrue(dry.startswith("GAME-2  Mute button state\nstatus: todo"))
        self.assertTrue(dry.endswith("\n\ndry run: not claimed · earmark claim GAME-2\n"))
        self.assertEqual(self.ok("show", "2", "-f", "status"), "status: todo\n")

    def test_full_review_cycle(self):
        self.assertEqual(self.ok("claim", "1", as_="claude-ui"),
                         "GAME-1 claimed · in_progress · assignee claude-ui · lease until 2026-11-03T12:02:11Z\n")
        self.assertEqual(self.ok("review", "1", "-m", "state in store · test_reload", as_="claude-ui"),
                         "GAME-1 sent to review · in_review · assignee claude-ui\n")
        out = self.ok("next", as_="codex-qa").splitlines()
        self.assertEqual(out[3], "assignee: claude-ui   reviewer: codex-qa (lease until "
                                 "2026-11-03T12:02:11Z)   reporter: hector")
        self.assertIn("handoff", self.ok("comments", "1", "--new", as_="codex-qa"))
        self.assertEqual(self.ok("reject", "1", "-m", "tabs reset: menu.js:42", as_="codex-qa"),
                         "GAME-1 rejected · todo · assignee claude-ui\n")
        self.assertTrue(self.ok("next", as_="claude-ui").startswith("GAME-1  "))
        self.ok("review", "1", "-m", "fixed tabs", as_="claude-ui")
        self.ok("claim", "1", as_="codex-qa")
        self.assertEqual(self.ok("approve", "1", "-m", "verified", as_="codex-qa"), "GAME-1 approved · done\n")
        self.assertEqual(self.ok("reopen", "1", "-m", "regressed", as_="hector"),
                         "GAME-1 reopened · todo · assignee claude-ui\n")
        kinds = [ln.split()[2] for ln in self.ok("comments", "1").splitlines() if ln.startswith("#")]
        self.assertEqual(kinds, ["handoff", "changes", "handoff", "approval", "note"])

    def test_release_move_done_cancel(self):
        self.ok("claim", "2", as_="claude-ui")
        self.assertEqual(self.ok("release", "2", "-m", "too big", as_="claude-ui"), "GAME-2 released · todo\n")
        self.assertEqual(self.ok("move", "2", "backlog", as_="hector"), "GAME-2 moved · backlog\n")
        self.assertEqual(self.ok("move", "2", "todo", "-m", "triaged", as_="hector"), "GAME-2 moved · todo\n")
        self.ok("claim", "2", as_="claude-ui")
        self.assertEqual(self.ok("move", "2", "blocked", "-m", "needs api", as_="claude-ui"),
                         "GAME-2 moved · blocked · assignee claude-ui\n")
        self.assertEqual(self.ok("done", "2", as_="hector"), "GAME-2 done\n")
        self.assertEqual(self.ok("cancel", "1", "-m", "dup of 2", as_="hector"), "GAME-1 canceled\n")
        self.assertEqual(self.ok("ls"), "0 of 0 open issues\n")

    def test_errors_and_exit_codes(self):
        self.ok("claim", "1", as_="claude-ui")
        self.ok("join", "worker", as_="grok-1")
        err = self.fails(3, "claim", "1", as_="grok-1")
        self.assertTrue(err.startswith("error: ") and "claude-ui" in err, err)
        env = self.json("claim", "1", as_="grok-1")
        self.assertEqual((env["ok"], env["error"]["code"]), (False, "conflict"))
        err = self.fails(1, "done", "1", as_="claude-ui")
        self.assertIn("review", err)
        self.assertIn("error: ", self.fails(1, "move", "1", "flying", as_="claude-ui"))
        self.assertIn("error: no issue GAME-99", self.fails(2, "claim", "99", as_="claude-ui"))
        self.assertIn("is not on this board", self.fails(2, "claim", "OTHER-1", as_="claude-ui"))

    def test_nothing_to_claim_is_exit_4_on_stdout(self):
        self.ok("claim", "1", as_="claude-ui")
        self.ok("review", "1", "-m", "done", as_="claude-ui")
        self.ok("claim", "2", as_="claude-ui")
        code, out, err = self.cli("next", as_="claude-ui")
        self.assertEqual((code, err), (4, ""))
        self.assertEqual(out, "next: nothing ready in your scope · review queue: 0 · inbox: 0 new · stale: 0\n")
        self.ok("join", "worker", as_="grok-1")
        code, out, _ = self.cli("next", as_="grok-1")
        self.assertEqual((code, out), (4, "next: nothing ready in your scope · review queue: 1 · "
                                          "inbox: 0 new · stale: 0\n"))
        self.advance(hours=3)
        code, out, _ = self.cli("next", "--review", as_="claude-ui")
        self.assertEqual(code, 4)
        self.assertTrue(out.startswith("next: nothing to review in your scope"), out)
        self.assertIn("stale: 1", out)
        env = self.json("next", "--review", as_="claude-ui")
        self.assertEqual((env["error"]["code"], env["error"]["details"]["mode"]), ("empty", "review"))

    def test_stale_lease_flags(self):
        self.ok("claim", "1", as_="claude-ui")
        self.advance(hours=3)
        line = [ln for ln in self.ok("ls").splitlines() if ln.startswith("GAME-1 ")][0]
        self.assertTrue(line.endswith("  stale"), line)
        self.assertIn("(lease until 2026-11-03T12:02:11Z, stale)", self.ok("show", "1"))
        self.ok("join", "worker", as_="grok-1")
        self.assertTrue(self.ok("claim", "1", as_="grok-1").startswith("GAME-1 claimed · in_progress · "
                                                                    "assignee grok-1"))


if __name__ == "__main__":
    unittest.main()
