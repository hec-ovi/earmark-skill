"""The dirty hint: writes by an actor and `next` end with `· N changes for you: earmark check`
(meta.changes in JSON) when Board.pending_changes() says the actor's check feed has something."""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import CliBoardCase, contract_line, fake_board, issue, run_main, summary  # noqa: E402

from earmark import errors  # noqa: E402

AS = ["--as", "claude-ui"]
COMMENT = {"id": 1191, "issue": "GAME-42", "attachments": []}


def boom():
    raise RuntimeError("feed broke")


class HintTests(unittest.TestCase):
    def test_contract_line(self):
        line = contract_line("· N changes for you")
        with fake_board(add_comment=COMMENT, pending_changes=3):
            code, out, _ = run_main(["comment", "42", "hi"] + AS)
        self.assertEqual((code, out), (0, "#1191\n" + line.replace("N", "3") + "\n"))
        with fake_board(add_comment=COMMENT, pending_changes=1):
            _, out, _ = run_main(["comment", "42", "hi"] + AS)
        self.assertEqual(out.splitlines()[-1], "· 1 change for you: earmark check")

    def test_writes_and_next_ask(self):
        cases = [(["claim", "42"], {"claim": summary()}), (["new", "x"], {"create_issue": issue()}),
                 (["review", "42", "-m", "x"], {"submit_review": summary(status="in_review")}),
                 (["assign", "42", "codex-qa"], {"assign": summary()}),
                 (["topic", "add", "ui-ux"], {"add_topic": {"path": "ui-ux"}}),
                 (["next"], {})]
        for argv, results in cases:
            with self.subTest(argv=argv), fake_board(pending_changes=2, **results) as boards:
                code, out, _ = run_main(argv + AS)
            self.assertEqual(code, 0)
            self.assertEqual(out.splitlines()[-1], "· 2 changes for you: earmark check")
            self.assertEqual(len(boards[0].called("pending_changes")), 1)

    def test_reads_and_check_do_not_ask(self):
        for argv in (["ls"], ["show", "42"], ["comments", "42"], ["inbox"], ["whoami"], ["check"]):
            with self.subTest(argv=argv), fake_board(pending_changes=2, check={"changed": []}) as boards:
                _, out, _ = run_main(argv + AS)
            self.assertEqual(boards[0].called("pending_changes"), [])
            self.assertNotIn("changes for you", out)

    def test_nothing_pending_no_actor_or_a_failing_feed(self):
        for value in (0, None, boom):
            with self.subTest(value=value), fake_board(claim=summary(), pending_changes=value):
                code, out, _ = run_main(["claim", "42"] + AS)
            self.assertEqual((code, out.count("\n")), (0, 1))

    def test_json_meta(self):
        with fake_board(claim=summary(), pending_changes=4):
            _, out, _ = run_main(["claim", "42", "--json"] + AS)
        self.assertEqual(json.loads(out)["meta"]["changes"], 4)
        with fake_board(claim=summary(), pending_changes=0):
            _, out, _ = run_main(["claim", "42", "--json"] + AS)
        self.assertNotIn("changes", json.loads(out)["meta"])

    def test_next_with_nothing_to_claim_still_hints(self):
        empty = errors.Empty("nothing", details={"review_queue": 0, "inbox": 1, "stale": 0, "mode": "work"})

        def raise_(**kw):
            raise empty
        with fake_board(next=raise_, pending_changes=2):
            code, out, _ = run_main(["next"] + AS)
        self.assertEqual(code, 4)
        self.assertEqual(out, "next: nothing ready in your scope · review queue: 0 · inbox: 1 new · stale: 0\n"
                              "· 2 changes for you: earmark check\n")
        with fake_board(next=raise_, pending_changes=2):
            code, out, _ = run_main(["next", "--json"] + AS)
        env = json.loads(out)
        self.assertEqual((code, env["error"]["code"], env["meta"]["changes"]), (4, "empty", 2))
        with fake_board(approve=summary(status="done"), next=raise_, pending_changes=2):
            code, out, _ = run_main(["approve", "42", "--next"] + AS)
        self.assertEqual((code, out.splitlines()[-1]), (4, "· 2 changes for you: earmark check"))

    def test_errors_do_not_hint(self):
        def conflict(*a, **k):
            raise errors.Conflict("held")
        with fake_board(claim=conflict, pending_changes=2):
            code, out, _ = run_main(["claim", "42"] + AS)
        self.assertEqual((code, out), (3, ""))


class RealBoardTests(CliBoardCase):
    def test_no_hint_before_the_board_has_a_feed(self):
        self.ok("join", "worker", as_="claude-ui")
        out = self.ok("new", "x", as_="claude-ui")
        self.assertEqual(out, "GAME-1\n")


if __name__ == "__main__":
    unittest.main()
