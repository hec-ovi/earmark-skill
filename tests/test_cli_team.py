"""Topic and team commands against the recording fake board."""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import (TEAM_NOW, contract_block, fake_board, run_main,  # noqa: E402
                              summary, topic)

from unittest import mock  # noqa: E402

AS = ["--as", "pm-1"]


class TopicTests(unittest.TestCase):
    def test_add_edit_mv_rm(self):
        t = topic("ui-ux/video-calls", "Video calls", 0)
        cases = [
            (["topic", "add", "ui-ux/video-calls", "--name", "Video calls", "--icon", "video"],
             "add_topic", (("ui-ux/video-calls",), {"name": "Video calls", "icon": "video"}),
             "topic ui-ux/video-calls added"),
            (["topic", "edit", "ui-ux/video-calls", "--description", "calls"], "edit_topic",
             (("ui-ux/video-calls",), {"description": "calls"}), "topic ui-ux/video-calls updated: description"),
            (["topic", "mv", "ui-ux/video", "ui-ux/video-calls"], "move_topic",
             (("ui-ux/video", "ui-ux/video-calls"), {}), "topic ui-ux/video moved to ui-ux/video-calls"),
            (["topic", "rm", "ui-ux/video-calls"], "remove_topic",
             (("ui-ux/video-calls",), {"archive": False}), "topic ui-ux/video-calls removed"),
            (["topic", "rm", "ui-ux/video-calls", "--archive"], "remove_topic",
             (("ui-ux/video-calls",), {"archive": True}), "topic ui-ux/video-calls archived"),
        ]
        for argv, method, call, line in cases:
            with fake_board(**{method: t}) as boards:
                code, out, err = run_main(argv + AS)
            self.assertEqual((code, err), (0, ""), argv)
            self.assertEqual(boards[0].called(method), [call], argv)
            self.assertEqual(out, line + "\n", argv)

    def test_edit_needs_a_field(self):
        with fake_board():
            code, _, err = run_main(["topic", "edit", "ui-ux"] + AS)
        self.assertEqual(code, 1)
        self.assertIn("topic edit: nothing to change", err)

    def test_meta_command(self):
        with fake_board(add_topic=topic("a", "a", 0)):
            _, out, _ = run_main(["topic", "add", "a", "--json"] + AS)
        self.assertEqual(json.loads(out)["meta"]["command"], "topic add")


class TeamTests(unittest.TestCase):
    def test_join_uses_as_as_handle_without_touch(self):
        brief = {"agent": {"handle": "claude-ui"}, "profile": {"slug": "worker"}, "board": {}}
        with fake_board(join=brief) as boards:
            code, out, _ = run_main(["join", "worker", "--as", "claude-ui", "--cli", "claude-code",
                                     "--json"])
        b = boards[0]
        self.assertEqual(b.calls[0], ("open", (None, None), {}))
        self.assertEqual(b.called("join"), [(("worker",), {"handle": "claude-ui", "cli": "claude-code"})])
        self.assertEqual(b.called("touch"), [])
        self.assertEqual(json.loads(out)["meta"]["actor"], "claude-ui")

    def test_join_without_handle_prints_the_new_one(self):
        brief = {"agent": {"handle": "worker-3", "profile": "worker"},
                 "profile": {"slug": "worker", "role": "worker", "prompt": "You are a worker."},
                 "scope": ["ui-ux"], "assigned": [], "inbox_new": 0, "loop": "Worker loop.",
                 "board": {"prefix": "GAME", "require_review": False, "lease_minutes": 60}}
        with fake_board(join=brief) as boards:
            _, out, _ = run_main(["join", "worker"])
        self.assertEqual(boards[0].called("join"), [(("worker",), {"handle": None, "cli": None})])
        lines = out.splitlines()
        self.assertEqual(lines[:4], ["you are worker-3 · profile worker (worker) · board GAME",
                                     "pass --as worker-3 on every earmark command", "scope: ui-ux",
                                     "review required: no · lease: 60m"])

    def test_team_contract_text(self):
        from test_cli_support import TEAM
        with fake_board(team=TEAM), mock.patch("earmark.cli.render.now_epoch", return_value=TEAM_NOW):
            _, out, _ = run_main(["team"])
        self.assertEqual(out, contract_block("HANDLE      ROLE") + "\n")

    def test_whoami_leave_triage(self):
        who = {"agent": {"handle": "claude-ui", "profile": "worker", "role": "worker", "presence": "active",
                         "working": [summary()], "reviewing": [], "assigned": [summary()]},
               "profile": {"slug": "worker", "role": "worker", "topics": []}, "scope": [], "is_owner": False}
        with fake_board(whoami=who):
            _, out, _ = run_main(["whoami", "--as", "claude-ui"])
        lines = out.splitlines()
        self.assertEqual(lines[0], "handle: claude-ui · profile: worker · role: worker · state: active")
        self.assertEqual(lines[2], "your issues (1):")
        with fake_board(leave={"agent": {"handle": "claude-ui"}, "returned": ["GAME-42"]}) as boards:
            _, out, _ = run_main(["leave", "--keep", "--as", "claude-ui"])
        self.assertEqual(boards[0].called("leave"), [((), {"keep": True})])
        self.assertEqual(out, "claude-ui offline · returned: GAME-42\n")
        with fake_board(triage={"backlog": [summary(status="backlog")], "counts": {"backlog": 1}}):
            code, out, _ = run_main(["triage"] + AS)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("triage: untriaged backlog 1 · "))

    def test_profiles(self):
        prof = {"slug": "ui-lead", "name": "UI lead", "role": "worker", "prompt": "p", "topics": ["ui-ux"],
                "version": 2, "agents": []}
        with fake_board(profiles=[prof]):
            _, out, _ = run_main(["profiles"])
        self.assertEqual(out.splitlines()[1], "ui-lead  worker  UI lead  ui-ux  -")
        with fake_board(get_profile=prof) as boards:
            _, out, _ = run_main(["profile", "show", "ui-lead"])
        self.assertEqual(boards[0].called("get_profile"), [(("ui-lead",), {})])
        self.assertTrue(out.endswith("\n\np\n"))
        with fake_board(add_profile=prof) as boards:
            _, out, _ = run_main(["profile", "add", "ui-lead", "--role", "worker", "--name", "UI lead",
                                  "--prompt", "-", "-t", "ui-ux"] + AS, stdin="You lead UI.\n")
        self.assertEqual(boards[0].called("add_profile"),
                         [(("ui-lead",), {"name": "UI lead", "role": "worker", "prompt": "You lead UI.",
                                          "topics": ["ui-ux"]})])
        self.assertEqual(out, "profile ui-lead added\n")
        with fake_board(edit_profile=prof) as boards:
            _, out, _ = run_main(["profile", "edit", "ui-lead", "--whole-board", "--role", "pm",
                                  "--if-version", "2"] + AS)
        self.assertEqual(boards[0].called("edit_profile"),
                         [(("ui-lead",), {"if_version": 2, "role": "pm", "topics": []})])
        self.assertEqual(out, "profile ui-lead updated: role, topics · version 2\n")
        with fake_board(remove_profile={"deleted": "ui-lead"}) as boards:
            _, out, _ = run_main(["profile", "rm", "ui-lead"] + AS)
        self.assertEqual(out, "profile ui-lead removed\n")
        with fake_board():
            code, _, err = run_main(["profile", "add", "x", "--role", "boss", "--name", "X"] + AS)
        self.assertEqual(code, 1)
        self.assertIn("invalid choice", err)


if __name__ == "__main__":
    unittest.main()
