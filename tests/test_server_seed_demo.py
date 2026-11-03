"""scripts/seed-demo.py: refuses unsafe targets, seeds a varied board the server can serve."""

from __future__ import annotations

import collections
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.test_server_fixtures import ServerHarness, core_ready  # first: src/ on sys.path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "seed-demo.py")
CORE = core_ready()


def run_seed(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=120)


class SeedGuardTests(unittest.TestCase):
    def test_dir_is_required(self):
        proc = run_seed()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--dir", proc.stderr)

    def test_refuses_the_repository(self):
        for target in (REPO, os.path.join(REPO, "build", "demo")):
            with self.subTest(target=target):
                proc = run_seed("--dir", target)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("refusing", proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(REPO, "build", "demo")))
        self.assertFalse(os.path.exists(os.path.join(REPO, ".earmark")))


@unittest.skipUnless(CORE, "earmark.board is not implemented yet")
class SeedDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="earmark-seed-test-")
        cls.proc = run_seed("--dir", cls.tmp)
        cls.db = os.path.join(cls.tmp, ".earmark", "earmark.db")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def board(self):
        from earmark.board import Board

        board = Board.open(db=self.db)
        self.addCleanup(board.close)
        return board

    def test_output(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertEqual(self.proc.stderr, "")
        lines = self.proc.stdout.strip().splitlines()
        self.assertIn(f"board: {self.db}", lines)
        self.assertIn(f"earmark ui --db {self.db}", lines[-1])

    def test_existing_board_needs_force(self):
        proc = run_seed("--dir", self.tmp)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--force", proc.stderr)

    def test_content(self):
        b = self.board()
        info = b.info()
        self.assertEqual((info["prefix"], info["owner"]), ("GAME", "dana"))
        items = b.list_issues(status="all", limit=500)["items"]
        self.assertGreaterEqual(len(items), 45)
        statuses = collections.Counter(i["status"] for i in items)
        self.assertEqual(set(statuses), {"backlog", "todo", "in_progress", "in_review", "blocked", "done",
                                         "canceled"})
        self.assertEqual(set(i["priority"] for i in items), {"urgent", "high", "medium", "low"})
        self.assertEqual(set(i["type"] for i in items), {"bug", "feature", "task", "chore"})
        stale = [i for i in items if i["stale"]]
        self.assertEqual([(i["status"], i["assignee"]) for i in stale], [("in_progress", "agy-art")])
        self.assertTrue(any(i["parent"] for i in items))
        self.assertTrue(any(i["blocked"] for i in items))
        self.assertTrue(b.list_issues(overdue=True)["items"])
        self.assertTrue(all(i["topic"] for i in items))

        kinds = collections.Counter()
        replies = mentions = 0
        for i in items:
            for c in b.list_comments(i["key"], limit=200)["items"]:
                kinds[c["kind"]] += 1
                replies += c["reply_to"] is not None
                mentions += bool(c["mentions"])
        self.assertEqual(set(kinds), {"note", "progress", "question", "handoff", "approval", "changes"})
        self.assertGreater(replies, 5)
        self.assertGreater(mentions, 5)

        cycle = b.get_issue("GAME-2", comments=20)
        self.assertEqual(cycle["status"], "done")
        kinds_in_order = [c["kind"] for c in b.list_comments("GAME-2")["items"]
                          if c["kind"] in ("handoff", "changes", "approval")]
        self.assertEqual(kinds_in_order, ["handoff", "changes", "handoff", "approval"])

    def test_team_and_topics(self):
        b = self.board()
        agents = {a["handle"]: a for a in b.team()["agents"]}
        expected = {"claude-ui": ("claude-code", "ui-lead"), "codex-api": ("codex", "api-engineer"),
                    "agy-art": ("agy", "art-director"), "opencode-audio": ("opencode", "audio-lead"),
                    "grok-writer": ("grok", "writer"), "codex-review": ("codex", "reviewer"),
                    "grok-pm": ("grok", "pm")}
        for handle, (cli, profile) in expected.items():
            self.assertEqual((agents[handle]["cli"], agents[handle]["profile"]), (cli, profile))
        profiles = {p["slug"]: p for p in b.profiles()}
        self.assertEqual(profiles["api-engineer"]["topics"], ["api", "integration"])
        self.assertEqual(profiles["pm"]["role"], "pm")
        for slug in ("ui-lead", "api-engineer", "art-director", "audio-lead", "writer", "reviewer", "pm"):
            lines = profiles[slug]["prompt"].splitlines()
            self.assertTrue(3 <= len(lines) <= 6, (slug, lines))

        def walk(nodes):
            for node in nodes:
                yield node
                yield from walk(node["children"])

        topics = {t["path"]: t for t in walk(b.topics())}
        eye = topics["ui-ux/character-menu/face-layer/eye-color"]
        self.assertEqual((eye["name"], eye["icon"]), ("Eye color widget", "eye"))
        self.assertEqual(topics["ui-ux"]["name"], "UI & UX")
        self.assertTrue(all(t["icon"] for t in topics.values()))

    def test_icons_exist_in_the_ui_sprite(self):
        sprite = os.path.join(REPO, "src", "earmark", "ui", "icons.svg")
        if not os.path.exists(sprite):
            self.skipTest("ui/icons.svg not built")
        with open(sprite, encoding="utf-8") as f:
            text = f.read()
        b = self.board()
        icons = {p["icon"] for p in b.profiles() if p["icon"]}

        def walk(nodes):
            for node in nodes:
                yield node
                yield from walk(node["children"])

        icons |= {t["icon"] for t in walk(b.topics()) if t["icon"]}
        missing = sorted(i for i in icons if f'id="{i}"' not in text and f'id="i-{i}"' not in text)
        self.assertEqual(missing, [])

    def test_deterministic(self):
        tmp = tempfile.mkdtemp(prefix="earmark-seed-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assertEqual(run_seed("--dir", tmp).returncode, 0)
        from earmark.board import Board

        def snapshot(path):
            b = Board.open(db=path)
            try:
                items = b.list_issues(status="all", limit=500)["items"]
                return [(i["key"], i["title"], i["status"], i["assignee"], i["comment_count"]) for i in items]
            finally:
                b.close()

        self.assertEqual(snapshot(os.path.join(tmp, ".earmark", "earmark.db")), snapshot(self.db))

    def test_served(self):
        h = ServerHarness(self.db)
        try:
            resp = h.request("GET", "/api/issues?status=all&limit=200")
            self.assertEqual(resp.status, 200)
            self.assertGreaterEqual(resp.data["total"], 45)
            team = h.request("GET", "/api/team").data["agents"]
            self.assertEqual(len([a for a in team if a["kind"] == "agent"]), 7)
        finally:
            h.stop()


if __name__ == "__main__":
    unittest.main()
