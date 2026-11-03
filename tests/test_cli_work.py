"""Work commands against the recording fake board: argument mapping, one-line results, exit 4."""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unittest import mock  # noqa: E402

from test_cli_support import (DAY, contract_block, contract_line, fake_board, issue,  # noqa: E402
                              run_main, summary)

from earmark import errors  # noqa: E402


def raiser(exc):
    def raise_(*a, **k):
        raise exc
    return raise_


EMPTY = errors.Empty("nothing ready in your scope", hint="earmark next --review",
                     details={"review_queue": 3, "changes": 2, "inbox": 0, "stale": 0, "scope": [],
                              "mode": "work"})


class NextTests(unittest.TestCase):
    def test_prints_show_format(self):
        with fake_board() as boards:
            code, out, _ = run_main(["next", "--as", "w-1"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("GAME-42  Eye color widget resets on reload\nstatus: in_progress"))
        self.assertEqual(boards[0].called("next"), [((), {"review": None, "topics": [], "dry_run": False})])

    def test_then_the_comments_not_seen(self):
        with fake_board() as boards, mock.patch("earmark.cli.render.today", return_value=DAY):
            code, out, _ = run_main(["next", "--as", "claude-ui"])
        self.assertEqual(code, 0)
        self.assertEqual(out, contract_block("GAME-42  Eye color") + "\n\n2 new comments:\n"
                         + contract_block("#1187  claude-ui").rsplit("\n", 1)[0] + "\n")
        self.assertEqual(boards[0].called("list_comments"), [(("GAME-42",), {"new": True})])
        empty = {"items": [], "last_id": 1199, "has_more": False}
        with fake_board(list_comments=empty), mock.patch("earmark.cli.render.today", return_value=DAY):
            _, out, _ = run_main(["next", "--as", "claude-ui"])
        self.assertEqual(out, contract_block("GAME-42  Eye color") + "\n")
        with fake_board():
            _, out, _ = run_main(["next", "--as", "claude-ui", "--json"])
        self.assertEqual([c["id"] for c in json.loads(out)["data"]["comments"]], [1187, 1190])

    def test_dry_run_reads_no_comments(self):
        with fake_board() as boards:
            run_main(["next", "--dry-run", "--as", "w-1"])
        self.assertEqual(boards[0].called("list_comments"), [])

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
        self.assertEqual(out.rstrip("\n"), contract_line("next: nothing to review"))
        self.assertEqual(out, "next: nothing to review for you (your own handoffs excluded) · "
                              "changes for you: 2\n")
        legacy = errors.Empty("x", details={"review_queue": 1, "inbox": 2, "stale": 0, "mode": "work"})
        with fake_board(next=raiser(legacy)):  # details from before 1.3: no changes count
            _, out, _ = run_main(["next", "--as", "w-1"])
        self.assertEqual(out, "next: nothing ready for you · reviews for you: 1 · changes for you: 0 · "
                              "stale: 0\n")
        with fake_board(next=raiser(EMPTY)):
            code, out, _ = run_main(["next", "--as", "w-1", "--json"])
        env = json.loads(out)
        self.assertEqual((code, env["ok"], env["error"]["code"], env["error"]["details"]["review_queue"]),
                         (4, False, "empty", 3))

    def test_empty_without_counts_prints_message(self):
        with fake_board(next=raiser(errors.Empty("nothing here"))):
            code, out, _ = run_main(["next", "--as", "w-1"])
        self.assertEqual((code, out), (4, "nothing here\n"))


class ThenNextTests(unittest.TestCase):
    LINE = "GAME-42 approved · done"

    def test_action_then_next(self):
        for argv, method in ((["approve", "42"], "approve"), (["review", "42", "-m", "x"], "submit_review"),
                             (["reject", "42", "-m", "x"], "reject"), (["done", "42"], "done"),
                             (["release", "42"], "release")):
            with self.subTest(argv=argv), fake_board(**{method: summary(status="done")}) as boards, \
                    mock.patch("earmark.cli.render.today", return_value=DAY):
                code, out, err = run_main(argv + ["--next", "--as", "claude-ui"])
            self.assertEqual((code, err), (0, ""))
            self.assertEqual([c[0] for c in boards[0].calls if c[0] in (method, "next", "list_comments")],
                             [method, "next", "list_comments"])
            first, rest = out.split("\n\n", 1)
            self.assertTrue(first.startswith("GAME-42 "), first)
            self.assertTrue(rest.startswith(contract_block("GAME-42  Eye color") + "\n\n2 new comments:"))

    def test_nothing_left_exits_4_after_the_action(self):
        with fake_board(approve=summary(status="done"), next=raiser(EMPTY)):
            code, out, err = run_main(["approve", "42", "--next", "--as", "codex-qa"])
        self.assertEqual((code, err), (4, ""))
        self.assertEqual(out, self.LINE + "\n" + contract_line("next: nothing ready") + "\n")
        with fake_board(approve=summary(status="done"), next=raiser(EMPTY)):
            code, out, _ = run_main(["approve", "42", "--next", "--as", "codex-qa", "--json"])
        env = json.loads(out)
        self.assertEqual((code, env["ok"], env["error"]["code"]), (4, False, "empty"))
        self.assertEqual(env["error"]["details"]["review_queue"], 3)
        self.assertEqual(env["error"]["details"]["action"]["status"], "done")

    def test_other_next_errors_keep_the_action_line(self):
        busy = errors.Busy("board is busy", hint="retry")
        with fake_board(approve=summary(status="done"), next=raiser(busy)):
            code, out, err = run_main(["approve", "42", "--next", "--as", "codex-qa"])
        self.assertEqual((code, out), (5, self.LINE + "\n"))
        self.assertEqual(err, "error: board is busy\nhint: retry\n")

    def test_json_on_claim(self):
        with fake_board(approve=summary(status="done")):
            code, out, _ = run_main(["approve", "42", "--next", "--as", "codex-qa", "--json"])
        data = json.loads(out)["data"]
        self.assertEqual((code, data["action"]["status"], data["next"]["key"]), (0, "done", "GAME-42"))
        self.assertEqual(len(data["next"]["comments"]), 2)

    def test_a_failed_action_runs_no_next(self):
        exc = errors.Forbidden("only the reviewer approves")
        with fake_board(approve=raiser(exc)) as boards:
            code, out, _ = run_main(["approve", "42", "--next", "--as", "w"])
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(boards[0].called("next"), [])

    def test_only_the_handoff_commands_take_it(self):
        for cmd in (["claim", "42"], ["move", "42", "todo"], ["cancel", "42", "-m", "x"]):
            code, _, err = run_main(cmd + ["--next", "--as", "w"])
            self.assertEqual(code, 1, cmd)


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
