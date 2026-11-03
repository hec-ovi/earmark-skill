"""End to end on a real temp board: topics, profiles, join/leave, team, whoami, triage."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import CliBoardCase, contract_block  # noqa: E402


class TopicTests(CliBoardCase):
    def test_tree_matches_the_contract_sample(self):
        names = {"ui-ux": "UI & UX", "ui-ux/character-menu": "Character menu",
                 "ui-ux/character-menu/face-layer": "Face customization layer",
                 "ui-ux/character-menu/face-layer/eye-color": "Eye color widget",
                 "ui-ux/video-calls": "Video calls"}
        self.assertEqual(self.ok("topic", "add", "ui-ux/character-menu/face-layer/eye-color", as_="hector"),
                         "topic ui-ux/character-menu/face-layer/eye-color added\n")
        self.ok("topic", "add", "ui-ux/video-calls", "--name", "Video calls", as_="hector")
        for path, name in names.items():
            self.ok("topic", "edit", path, "--name", name, as_="hector")
        counts = {"ui-ux/character-menu/face-layer/eye-color": 2, "ui-ux/character-menu/face-layer": 2,
                  "ui-ux/character-menu": 5, "ui-ux/video-calls": 5}
        for path, n in counts.items():
            for i in range(n):
                self.ok("new", f"{path} {i}", "-t", path, as_="hector")
        self.ok("new", "closed one", "-t", "ui-ux", as_="hector")
        self.ok("cancel", "15", "-m", "x", as_="hector")
        self.assertEqual(self.ok("topics"), contract_block("ui-ux    ") + "\n")
        out = self.ok("topics", "ui-ux/character-menu", "--depth", "2").splitlines()
        self.assertEqual([ln.split()[0] for ln in out], ["ui-ux/character-menu", "face-layer"])

    def test_mv_rm_archive(self):
        self.ok("topic", "add", "web/old", as_="hector")
        self.ok("topic", "add", "web/empty", as_="hector")
        self.ok("new", "x", "-t", "web/old", as_="hector")
        self.assertEqual(self.ok("topic", "mv", "web/old", "web/new", as_="hector"),
                         "topic web/old moved to web/new\n")
        self.assertEqual(self.ok("show", "1", "-f", "topic"), "topic: web/new\n")
        self.assertIn("error:", self.fails(1, "topic", "rm", "web/new", as_="hector"))
        self.assertEqual(self.ok("topic", "rm", "web/empty", as_="hector"), "topic web/empty removed\n")
        self.assertEqual(self.ok("topic", "rm", "web/new", "--archive", as_="hector"),
                         "topic web/new archived\n")
        self.assertNotIn("web/new", self.ok("topics"))
        self.assertTrue(self.ok("topics", "--all").splitlines()[1].endswith("archived"))
        self.assertTrue(self.json("topics")["ok"])

    def test_empty_tree(self):
        self.assertEqual(self.ok("topics"), "topics: none · add one: earmark topic add PATH\n")


class TeamTests(CliBoardCase):
    def test_join_briefing_and_identity(self):
        out = self.ok("join", "worker", "--cli", "claude-code").splitlines()
        self.assertEqual(out[0], "you are worker-1 · profile worker (worker) · board GAME")
        self.assertEqual(out[1], "pass --as worker-1 on every earmark command")
        self.assertIn("assigned to you: none", out)
        self.assertIn("inbox: 0 new", out)
        self.assertTrue(any(ln.startswith("1. earmark next") for ln in out), out)
        self.assertTrue(self.ok("join", "worker").startswith("you are worker-2 "))
        env = self.json("join", "reviewer", "--cli", "codex", as_="codex-qa")
        self.assertEqual((env["ok"], env["meta"]["actor"], env["data"]["agent"]["handle"]),
                         (True, "codex-qa", "codex-qa"))
        self.assertIn("error:", self.fails(2, "join", "nope", as_="x-1"))

    def test_team_whoami_leave(self):
        self.ok("join", "worker", "--cli", "claude-code", as_="claude-ui")
        self.ok("join", "reviewer", "--cli", "codex", as_="codex-qa")
        self.ok("new", "Eye color", as_="hector")
        self.ok("claim", "1", as_="claude-ui")
        self.advance(minutes=2)
        out = self.ok("team").splitlines()
        self.assertEqual(out[0], "HANDLE      ROLE      PROFILE   CLI          SEEN  STATE   WORKING")
        self.assertEqual(out[1], "claude-ui   worker    worker    claude-code  2m    active  GAME-1 in_progress")
        self.assertEqual(out[2], "codex-qa    reviewer  reviewer  codex        2m    active  -")
        self.assertEqual(out[3], "hector      owner     -         human        2m    active  -")
        who = self.ok("whoami", as_="claude-ui").splitlines()
        self.assertEqual(who[0], "handle: claude-ui · profile: worker · role: worker · state: active")
        self.assertEqual(who[1], "scope: whole board")
        self.assertEqual(who[2], "your issues (1):")
        self.assertTrue(who[4].startswith("GAME-1  in_progress"), who)
        self.assertIn("hint:", self.fails(1, "whoami"))
        self.assertEqual(self.ok("whoami", as_="hector").splitlines()[0],
                         "handle: hector · profile: - · role: owner · state: active")
        self.assertEqual(self.ok("leave", as_="claude-ui"), "claude-ui offline · returned: GAME-1\n")
        self.assertEqual(self.ok("show", "1", "-f", "status,assignee"), "status: todo\nassignee: -\n")
        self.assertIn("claude-ui   worker    worker    claude-code  0s    offline  -", self.ok("team"))

    def test_profiles_crud(self):
        self.ok("topic", "add", "ui-ux", as_="hector")
        out = self.ok("profiles").splitlines()
        self.assertEqual(out[0].split(), ["SLUG", "ROLE", "NAME", "SCOPE", "AGENTS"])
        self.assertEqual({ln.split()[0] for ln in out[1:]}, {"worker", "reviewer", "pm"})
        self.assertEqual(self.ok("profile", "add", "ui-lead", "--role", "worker", "--name", "UI lead",
                                 "-t", "ui-ux", "--prompt-file", "-", stdin="You lead the UI.\n",
                                 as_="hector"), "profile ui-lead added\n")
        show = self.ok("profile", "show", "ui-lead").splitlines()
        self.assertEqual(show[:3], ["ui-lead  UI lead", "role: worker   version: 1   agents: -", "scope: ui-ux"])
        self.assertEqual(show[-1], "You lead the UI.")
        self.assertEqual(self.ok("profile", "edit", "ui-lead", "--name", "UI", "--if-version", "1", as_="hector"),
                         "profile ui-lead updated: name · version 2\n")
        self.assertIn("error:", self.fails(3, "profile", "edit", "ui-lead", "--name", "X", "--if-version", "1",
                                           as_="hector"))
        self.ok("join", "ui-lead", as_="claude-ui")
        self.assertEqual(self.ok("whoami", as_="claude-ui").splitlines()[1], "scope: ui-ux")
        self.assertIn("error:", self.fails(1, "profile", "add", "x", "--role", "worker", "--name", "X",
                                           as_="claude-ui"))
        self.ok("leave", as_="claude-ui")
        self.assertEqual(self.ok("profile", "rm", "ui-lead", as_="hector"), "profile ui-lead removed\n")
        self.assertIn("error:", self.fails(2, "profile", "show", "ui-lead"))

    def test_triage_tables(self):
        self.ok("join", "worker", "--cli", "claude-code", as_="claude-ui")
        self.ok("new", "Untriaged", "--status", "backlog", as_="hector")
        self.ok("new", "Open todo", "-p", "high", as_="hector")
        self.ok("new", "Late", "--due", "2026-11-01", as_="hector")
        out = self.ok("triage", as_="hector").splitlines()
        self.assertTrue(out[0].startswith("triage: untriaged backlog 1 · unassigned todo 2 · "), out[0])
        self.assertIn("untriaged backlog (1):", out)
        self.assertIn("overdue (1):", out)
        header = out[out.index("unassigned todo (2):") + 1]
        self.assertEqual(header.split(), ["KEY", "STATUS", "PRIO", "TYPE", "ASSIGNEE", "TOPIC", "TITLE"])
        self.assertFalse(any("=" in ln for ln in out), out)
        if "idle agents (1):" in out:
            i = out.index("idle agents (1):")
            self.assertEqual(out[i + 1].split(), ["HANDLE", "ROLE", "PROFILE", "CLI", "SEEN", "STATE"])
            self.assertTrue(out[i + 2].startswith("claude-ui  worker"))


if __name__ == "__main__":
    unittest.main()
