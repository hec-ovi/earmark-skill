"""Work commands against the recording fake board: argument mapping, one-line results, exit 4."""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import contract_line, fake_board, issue, run_main, summary  # noqa: E402

from earmark import errors  # noqa: E402


def raiser(exc):
    def raise_(*a, **k):
        raise exc
    return raise_


EMPTY = errors.Empty("nothing ready in your scope", hint="earmark next --review",
                     details={"review_queue": 3, "inbox": 2, "stale": 0, "scope": [], "mode": "work"})


class NextTests(unittest.TestCase):
    def test_prints_show_format(self):
        with fake_board() as boards:
            code, out, _ = run_main(["next", "--as", "w-1"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("GAME-42  Eye color widget resets on reload\nstatus: in_progress"))
        self.assertEqual(boards[0].called("next"), [((), {"review": None, "topics": [], "dry_run": False})])

    def test_flags(self):
        with fake_board() as boards:
            _, out, _ = run_main(["next", "--review", "-t", "ui-ux", "--topic", "web", "--dry-run",
                                  "--as", "r-1"])
        self.assertEqual(boards[0].called("next")[0][1],
                         {"review": True, "topics": ["ui-ux", "web"], "dry_run": True})
        self.assertTrue(out.rstrip().endswith("dry run: not claimed · earmark claim GAME-42"))

    def test_empty_is_an_answer_on_stdout(self):
        with fake_board(next=raiser(EMPTY)):
            code, out, err = run_main(["next", "--as", "w-1"])
        self.assertEqual((code, err), (4, ""))
        self.assertEqual(out.rstrip("\n"), contract_line("next: nothing ready"))
        review = errors.Empty("x", details=dict(EMPTY.details, mode="review"))
        with fake_board(next=raiser(review)):
            code, out, _ = run_main(["next", "--as", "r-1"])
        self.assertEqual(out, "next: nothing to review in your scope · review queue: 3 · inbox: 2 new · stale: 0\n")
        with fake_board(next=raiser(EMPTY)):
            code, out, _ = run_main(["next", "--as", "w-1", "--json"])
        env = json.loads(out)
        self.assertEqual((code, env["ok"], env["error"]["code"], env["error"]["details"]["review_queue"]),
                         (4, False, "empty", 3))

    def test_empty_without_counts_prints_message(self):
        with fake_board(next=raiser(errors.Empty("nothing here"))):
            code, out, _ = run_main(["next", "--as", "w-1"])
        self.assertEqual((code, out), (4, "nothing here\n"))


class MoveTests(unittest.TestCase):
    CASES = [
        (["claim", "42"], "claim", ((("42",), {})), "claimed"),
        (["release", "42", "-m", "too big"], "release", (("42",), {"note": "too big"}), "released"),
        (["review", "42", "-m", "done; run tests"], "submit_review", (("42",), {"note": "done; run tests"}),
         "sent to review"),
        (["approve", "42"], "approve", (("42",), {"note": None}), "approved"),
        (["reject", "42", "-m", "menu.js:42 wrong"], "reject", (("42",), {"note": "menu.js:42 wrong"}),
         "rejected"),
        (["done", "42", "-m", "shipped"], "done", (("42",), {"note": "shipped"}), "done"),
        (["move", "42", "Blocked", "-m", "waiting on api"], "move",
         (("42", "blocked"), {"note": "waiting on api"}), "moved"),
        (["reopen", "42", "-m", "regressed"], "reopen", (("42",), {"note": "regressed"}), "reopened"),
        (["cancel", "42", "-m", "dup of 7"], "cancel", (("42",), {"note": "dup of 7"}), "canceled"),
    ]

    def test_mapping_and_result_line(self):
        for argv, method, call, verb in self.CASES:
            result = summary(status="todo", assignee=None, lease_until=None)
            with fake_board(**{method: result}) as boards:
                code, out, err = run_main(argv + ["--as", "w-1"])
            self.assertEqual((code, err), (0, ""), argv)
            self.assertEqual(boards[0].called(method), [call], argv)
            self.assertTrue(out.startswith(f"GAME-42 {verb}"), (argv, out))
            self.assertEqual(out.count("\n"), 1, argv)

    def test_note_required(self):
        for cmd in ("review", "reject", "reopen", "cancel"):
            code, _, err = run_main([cmd, "42", "--as", "w-1"])
            self.assertEqual(code, 1, cmd)
            self.assertIn("-m/--note", err)

    def test_note_from_stdin(self):
        with fake_board(submit_review=summary(status="in_review")) as boards:
            run_main(["review", "42", "-m", "-", "--as", "w-1"], stdin="what changed\nhow to verify\n")
        self.assertEqual(boards[0].called("submit_review")[0][1], {"note": "what changed\nhow to verify"})

    def test_conflict_exit_3(self):
        exc = errors.Conflict("GAME-42 is held by codex-qa until 2026-11-03T16:05:00Z",
                              hint="earmark next", details={"holder": "codex-qa"})
        with fake_board(claim=raiser(exc)):
            code, out, err = run_main(["claim", "42", "--as", "w-1"])
        self.assertEqual((code, out), (3, ""))
        self.assertEqual(err, "error: GAME-42 is held by codex-qa until 2026-11-03T16:05:00Z\nhint: earmark next\n")

    def test_json_is_the_issue(self):
        with fake_board(claim=issue()):
            _, out, _ = run_main(["claim", "42", "--as", "w-1", "--json"])
        env = json.loads(out)
        self.assertEqual((env["data"]["key"], env["meta"]["command"], env["meta"]["actor"]),
                         ("GAME-42", "claim", "w-1"))


if __name__ == "__main__":
    unittest.main()
