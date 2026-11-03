"""`need` (the commands.md sample byte for byte), blockers with their status in `show`, the
effective priority note, the `blocks:N` flag and triage's work of agents that left."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import (DAY, CliBoardCase, contract_block, fake_board, issue,  # noqa: E402
                              run_main, summary)

from earmark.cli import render as R  # noqa: E402

NEED = {"created": issue(key="GAME-77", id=77, topic="artwork/icons", assignee="codex-art",
                         status="todo"),
        "blocked": issue(key="GAME-42", status="blocked", assignee="claude-ui", lease_until=None)}


class RenderTests(unittest.TestCase):
    def test_need_sample(self):
        self.assertEqual(R.render_need(NEED, actor="claude-ui"), contract_block("GAME-77 created in"))

    def test_need_without_assignee_or_hold(self):
        res = {"created": dict(NEED["created"], assignee=None), "blocked": dict(NEED["blocked"], status="in_review")}
        self.assertEqual(R.render_need(res, actor="claude-ui").splitlines(),
                         ["GAME-77 created in artwork/icons · blocks GAME-42",
                          "GAME-42 in_review · waits on GAME-77"])

    def test_effective_priority_note_and_flag(self):
        low = issue(priority="low", effective_priority="high", blocks_open=1,
                    link_status={"GAME-51": "todo", "GAME-77": "done"})
        self.assertEqual(R.render_show(low, day=DAY).splitlines()[1],
                         "status: in_progress   priority: low (effective high, blocks GAME-51)   "
                         "type: bug   version: 7")
        same = issue(effective_priority="high")
        self.assertEqual(R.render_show(same, day=DAY).splitlines()[1],
                         "status: in_progress   priority: high   type: bug   version: 7")
        self.assertEqual(R.issue_flags(summary(blocks_open=2, comment_count=0, due=None), DAY), ["blocks:2"])
        self.assertEqual(R.render_fields(low, R.show_fields("priority,effective_priority,blocks_open")),
                         "priority: low\neffective_priority: high\nblocks_open: 1")

    def test_triage_offline_section(self):
        res = {"offline": [summary(status="todo", assignee="w1", comment_count=0, due=None)],
               "counts": {"offline": 12}}
        out = R.render_triage(res, day=DAY).splitlines()
        self.assertIn("assigned to offline agents 12", out[0])
        self.assertIn("assigned to offline agents (12):", out)
        self.assertEqual(out[-1], "+11 more: earmark triage --limit 12")
        self.assertNotIn("offline", R.render_triage({"counts": {}}, day=DAY).splitlines()[0])


    def test_triage_exhausted_reviews_section(self):
        parked = summary(status="blocked", assignee="w1", comment_count=0, due=None)
        res = {"stale": [summary(stale=True, comment_count=0, due=None)], "review_cap": [parked],
               "blocked": [], "counts": {"stale": 1, "review_cap": 4}}
        out = R.render_triage(res, day=DAY).splitlines()
        self.assertIn("stale claims 1 · exhausted reviews 4 · review queue", out[0])
        i = out.index("exhausted reviews (4):")
        self.assertGreater(i, out.index("stale claims (1):"))
        self.assertNotIn("STATUS", out[i + 1].split())  # blocked on every row
        self.assertEqual(out[i + 3], "+3 more: earmark triage --limit 4")
        self.assertNotIn("exhausted", R.render_triage({"counts": {}}, day=DAY).splitlines()[0])


class ArgumentTests(unittest.TestCase):
    def test_need_maps_flags(self):
        with fake_board(need=NEED) as boards:
            code, out, _ = run_main(["need", "42", "Ammo icon", "-t", "artwork/icons", "--assign", "codex-art",
                                     "-b", "24 px", "-p", "high", "--type", "feature", "-l", "hud",
                                     "--attach", "ref.png", "--as", "claude-ui"])
        self.assertEqual(code, 0)
        self.assertEqual(out, contract_block("GAME-77 created in") + "\n")
        self.assertEqual(boards[0].called("need"), [(("42", "Ammo icon"), {
            "topic": "artwork/icons", "body": "24 px", "priority": "high", "type": "feature",
            "labels": ["hud"], "assignee": "codex-art", "attach": ["ref.png"]})])
        code, _, err = run_main(["need", "42", "Ammo icon", "--as", "claude-ui"])
        self.assertEqual(code, 1)
        self.assertIn("--topic", err)


class EndToEndTests(CliBoardCase):
    def setUp(self):
        super().setUp()
        for path in ("ui-ux/hud", "artwork/icons"):
            self.ok("topic", "add", path, as_="hector")
        self.ok("profile", "add", "art", "--role", "worker", "--name", "Art", "-t", "artwork", as_="hector")
        self.ok("join", "worker", as_="claude-ui")
        self.ok("join", "art", as_="codex-art")
        self.ok("join", "reviewer", as_="codex-qa")

    def test_round_trip(self):
        self.ok("new", "HUD ammo counter", "-t", "ui-ux/hud", "-p", "high", as_="hector")
        self.ok("claim", "1", as_="claude-ui")
        ref = os.path.join(self.tmp, "ref.png")
        with open(ref, "wb") as fh:
            fh.write(b"\x89PNG ref")
        out = self.ok("need", "1", "Ammo icon at 24 px", "-t", "artwork/icons", "--assign", "codex-art",
                      "--attach", ref, as_="claude-ui")
        self.assertEqual(out, "GAME-2 created in artwork/icons · assigned to codex-art · blocks GAME-1\n"
                              "GAME-1 blocked, still yours · returns to your next when GAME-2 is done\n")
        self.assertEqual(self.ok("attachments", "2").splitlines()[1].split()[4], "ref.png")
        show = self.ok("show", "2").splitlines()
        self.assertEqual(show[1], "status: todo   priority: high   type: task   version: 2")
        listed = self.ok("ls").splitlines()
        self.assertTrue(listed[1].startswith("GAME-1  blocked  high  task  claude-ui") and
                        listed[1].endswith("c:1 blocked"), listed)
        self.assertTrue(listed[2].startswith("GAME-2  todo     high  task  codex-art") and
                        listed[2].endswith("blocks:1 a:1"), listed)
        self.assertTrue(self.ok("next", as_="codex-art").startswith("GAME-2  Ammo icon at 24 px\n"))
        self.ok("review", "2", "-m", "24 and 48 px", as_="codex-art")
        self.ok("next", "--review", as_="codex-qa")
        self.ok("approve", "2", "-m", "ok", as_="codex-qa")
        show = self.ok("show", "1").splitlines()
        self.assertEqual(show[1], "status: todo   priority: high   type: task   version: 5")
        self.assertEqual(show[3], "assignee: claude-ui   reviewer: -   reporter: hector")
        self.assertEqual(show[5], "parent: -   sub: -   blocks: -   blocked by: GAME-2 (done)")
        self.assertTrue(self.ok("next", as_="claude-ui").startswith("GAME-1  HUD ammo counter\n"))

    def test_effective_priority_in_show(self):
        self.ok("new", "Icon", "-t", "artwork/icons", "-p", "low", as_="hector")
        self.ok("new", "Screen", "-t", "ui-ux/hud", "-p", "urgent", as_="hector")
        self.ok("link", "1", "blocks", "2", as_="hector")
        self.assertEqual(self.ok("show", "1").splitlines()[1],
                         "status: todo   priority: low (effective urgent, blocks GAME-2)   type: task   "
                         "version: 2")
        self.assertEqual(self.ok("show", "1", "-f", "effective_priority"), "effective_priority: urgent\n")

    def test_triage_lists_work_of_agents_that_left(self):
        self.ok("new", "Left behind", as_="hector")
        self.ok("assign", "1", "claude-ui", as_="hector")
        self.ok("leave", "--keep", as_="claude-ui")
        out = self.ok("triage", as_="hector").splitlines()
        i = out.index("assigned to offline agents (1):")
        self.assertEqual(out[i + 2].split()[:5], ["GAME-1", "todo", "medium", "task", "claude-ui"])
        self.ok("join", "worker", as_="w-2")
        self.assertTrue(self.ok("next", as_="w-2").startswith("GAME-1  Left behind\n"))


if __name__ == "__main__":
    unittest.main()
