"""Profiles, join/leave, presence, whoami, team, triage, wait_check, touch and identity."""

from __future__ import annotations

import unittest

from tests.support import BoardCase

from earmark.errors import Conflict, Forbidden, IdentityRequired, InvalidRequest, NotFound, NotJoined

PROFILE_KEYS = {"slug", "name", "role", "icon", "prompt", "topics", "version", "created_at",
                "updated_at", "agents"}
AGENT_KEYS = {"handle", "kind", "profile", "role", "cli", "online", "presence", "joined_at",
              "last_seen", "working", "reviewing", "assigned"}
BRIEFING_KEYS = {"agent", "profile", "scope", "assigned", "inbox_new", "loop", "board"}


class Profiles(BoardCase):
    def test_seeded_and_shape(self):
        profiles = self.board.profiles()
        self.assertTrue({"worker", "reviewer", "pm"} <= {p["slug"] for p in profiles})
        self.assertEqual(set(profiles[0]), PROFILE_KEYS)
        with self.assertRaises(NotFound) as ctx:
            self.board.get_profile("ghost")
        self.assertIn("worker", ctx.exception.hint)

    def test_crud_and_permissions(self):
        b = self.board
        b.add_topic("ui-ux/menu")
        worker = self.agent("w1")
        with self.assertRaises(Forbidden):
            worker.add_profile("ui-lead", name="UI lead", role="worker")
        pm = self.agent("pm-1", "pm")
        made = pm.add_profile("ui-lead", name="UI lead", role="worker", icon="brush",
                              prompt="Own the UI", topics=["ui-ux/menu"])
        self.assertEqual((made["role"], made["topics"], made["version"], made["agents"]),
                         ("worker", ["ui-ux/menu"], 1, []))
        with self.assertRaises(Conflict):
            b.add_profile("ui-lead", name="x", role="worker")
        with self.assertRaises(NotFound):
            b.add_profile("bad-scope", name="x", role="worker", topics=["nope"])
        with self.assertRaises(InvalidRequest):
            b.add_profile("x", name="x", role="boss")
        edited = b.edit_profile("ui-lead", if_version=1, prompt="Own every screen", topics=["ui-ux"])
        self.assertEqual((edited["prompt"], edited["topics"], edited["version"]),
                         ("Own every screen", ["ui-ux"], 2))
        self.assertEqual(b.edit_profile("ui-lead", prompt="Own every screen")["version"], 2)
        with self.assertRaises(Conflict) as ctx:
            b.edit_profile("ui-lead", if_version=1, name="stale")
        self.assertEqual((ctx.exception.details["current_version"], ctx.exception.details["slug"]),
                         (2, "ui-lead"))
        with self.assertRaises(InvalidRequest):
            b.edit_profile("ui-lead", kind="renamed")
        with self.assertRaises(Forbidden):
            worker.edit_profile("ui-lead", name="mine")
        self.agent("ui-1", "ui-lead")
        self.assertEqual(b.get_profile("ui-lead")["agents"], ["ui-1"])
        self.assertEqual(b.remove_profile("ui-lead"), {"deleted": "ui-lead"})
        self.assertIsNone(b.whoami() and self.open_as("ui-1").whoami()["profile"])
        actions = [e["action"] for e in b.activity()["items"]]
        for action in ("profile_add", "profile_edit", "profile_remove"):
            self.assertIn(action, actions)


class JoinLeave(BoardCase):
    def test_default_handles_reuse_offline_ones(self):
        first = self.open_as(None)
        self.assertEqual(first.join("worker")["agent"]["handle"], "worker-1")
        self.assertEqual(first.actor, "worker-1")
        second = self.open_as(None)
        self.assertEqual(second.join("worker")["agent"]["handle"], "worker-2")
        first.leave()
        third = self.open_as(None)
        self.assertEqual(third.join("worker")["agent"]["handle"], "worker-1")
        self.assertEqual(self.open_as(None).join("reviewer")["agent"]["handle"], "reviewer-1")

    def test_briefing(self):
        b = self.board
        b.add_topic("ui-ux/menu")
        b.add_profile("ui-lead", name="UI lead", role="worker", prompt="Own the UI", topics=["ui-ux"])
        key = self.issue("x", topic="ui-ux/menu")["key"]
        agent = self.agent("claude-ui", "ui-lead", cli="claude-code")
        b.assign(key, "claude-ui")
        b.add_comment(key, "context for you")
        brief = agent.join("ui-lead")
        self.assertEqual(set(brief), BRIEFING_KEYS)
        self.assertEqual(set(brief["agent"]), AGENT_KEYS)
        self.assertEqual((brief["agent"]["cli"], brief["agent"]["role"], brief["agent"]["online"]),
                         ("claude-code", "worker", True))
        self.assertEqual((brief["profile"]["prompt"], brief["scope"]), ("Own the UI", ["ui-ux"]))
        self.assertEqual([i["key"] for i in brief["assigned"]], [key])
        self.assertEqual(brief["inbox_new"], 1)
        self.assertIn("earmark next", brief["loop"])
        self.assertEqual(brief["board"], {"prefix": "GAME", "require_review": True, "lease_minutes": 120})
        with self.assertRaises(NotFound):
            agent.join("ghost")
        with self.assertRaises(InvalidRequest):
            self.open_as(None).join("worker", handle="Bad Handle!")
        self.assertIn("join", [e["action"] for e in b.activity()["items"]])

    def test_rejoin_updates_profile_and_keeps_cli(self):
        agent = self.agent("a1", cli="codex")
        brief = agent.join("reviewer")
        self.assertEqual((brief["agent"]["profile"], brief["agent"]["cli"]), ("reviewer", "codex"))
        self.assertIn("review", brief["loop"].lower())

    def test_leave_returns_work(self):
        w1, w2 = self.agent("w1"), self.agent("w2")
        b = self.board
        working = self.issue("working")["key"]
        w1.claim(working)
        queued = self.issue("queued")["key"]
        b.assign(queued, "w1")
        reviewing = self.issue("reviewing")["key"]
        w2.claim(reviewing)
        w2.submit_review(reviewing, note="ok")
        w1.claim(reviewing)
        blocked = self.issue("blocked")["key"]
        b.assign(blocked, "w1")
        b.move(blocked, "blocked")
        left = w1.leave()
        self.assertEqual(sorted(left["returned"]), sorted([working, queued, reviewing]))
        self.assertEqual((left["agent"]["online"], left["agent"]["presence"]), (False, "offline"))
        for key in (working, queued):
            issue = b.get_issue(key)
            self.assertEqual((issue["status"], issue["assignee"], issue["lease_until"]), ("todo", None, None))
        review = b.get_issue(reviewing)
        self.assertEqual((review["status"], review["reviewer"], review["assignee"]), ("in_review", None, "w2"))
        self.assertEqual(b.get_issue(blocked)["assignee"], "w1")
        self.assertEqual(b.activity(limit=1)["items"][0]["action"], "leave")

    def test_leave_keep(self):
        w1 = self.agent("w1")
        key = self.issue("x")["key"]
        w1.claim(key)
        self.assertEqual(w1.leave(keep=True)["returned"], [])
        self.assertEqual(self.board.get_issue(key)["assignee"], "w1")


class Presence(BoardCase):
    def test_active_idle_offline_and_working_lists(self):
        w1, r1 = self.agent("w1"), self.agent("r1", "reviewer")
        self.agent("gone").leave()
        work = self.issue("work")["key"]
        w1.claim(work)
        review = self.issue("review")["key"]
        self.board.move(review, "in_progress")
        self.board.submit_review(review, note="please")
        r1.next()
        assigned = self.issue("assigned")["key"]
        self.board.assign(assigned, "w1")
        self.clock.advance(minutes=16)
        r1.touch()
        agents = {a["handle"]: a for a in self.board.team()["agents"]}
        self.assertEqual(set(agents["w1"]), AGENT_KEYS)
        self.assertEqual({h: a["presence"] for h, a in agents.items()},
                         {"w1": "idle", "r1": "active", "owner": "idle", "gone": "offline"})
        self.assertEqual([i["key"] for i in agents["w1"]["working"]], [work])
        self.assertEqual([i["key"] for i in agents["w1"]["assigned"]], [assigned])
        self.assertEqual([i["key"] for i in agents["r1"]["reviewing"]], [review])
        self.assertEqual([i["key"] for i in agents["owner"]["assigned"]], [review])
        self.assertEqual(list(agents)[-1], "gone")

    def test_whoami(self):
        w1 = self.agent("w1")
        me = w1.whoami()
        self.assertEqual((me["agent"]["handle"], me["profile"]["slug"], me["scope"], me["is_owner"]),
                         ("w1", "worker", [], False))
        self.assertTrue(self.board.whoami()["is_owner"])
        self.assertIsNone(self.board.whoami()["profile"])
        with self.assertRaises(IdentityRequired):
            self.open_as(None).whoami()
        with self.assertRaises(NotJoined) as ctx:
            self.open_as("ghost").whoami()
        self.assertIn("earmark join", ctx.exception.hint)


class Touch(BoardCase):
    def last_seen(self, handle):
        return self.board.conn.execute("SELECT last_seen FROM agents WHERE handle = ?",
                                       (handle,)).fetchone()[0]

    def test_throttled_read_then_write(self):
        w1 = self.agent("w1")
        watcher = self.open_as(None)
        version = watcher.data_version()
        self.clock.advance(seconds=59)
        w1.touch()
        self.assertEqual(self.last_seen("w1"), "2026-11-03T09:00:00Z")
        self.assertEqual(watcher.data_version(), version)
        self.clock.advance(seconds=2)
        w1.touch()
        self.assertEqual(self.last_seen("w1"), "2026-11-03T09:01:01Z")
        self.assertNotEqual(watcher.data_version(), version)
        version = watcher.data_version()
        self.clock.advance(seconds=30)
        w1.touch()
        self.assertEqual(watcher.data_version(), version)
        self.open_as(None).touch()
        self.open_as("ghost").touch()

    def test_writes_refresh_last_seen(self):
        w1 = self.agent("w1")
        self.clock.advance(minutes=5)
        w1.create_issue("x")
        self.assertEqual(self.last_seen("w1"), "2026-11-03T09:05:00Z")


class Identity(BoardCase):
    def test_writes_need_a_joined_actor(self):
        key = self.issue("x")["key"]
        with self.assertRaises(IdentityRequired) as ctx:
            self.open_as(None).add_comment(key, "hi")
        self.assertIn("--as", ctx.exception.hint)
        with self.assertRaises(NotJoined):
            self.open_as("ghost").add_comment(key, "hi")
        with self.assertRaises(NotJoined):
            self.open_as("Bad Handle!").claim(key)
        self.assertEqual(self.open_as("ghost").get_issue(key)["key"], key)


class Triage(BoardCase):
    def test_sections(self):
        b = self.board
        w1, idle = self.agent("w1"), self.agent("idle-1")
        self.agent("pm-1", "pm")
        backlog = self.issue("idea", status="backlog")["key"]
        unassigned = self.issue("free")["key"]
        stale = self.issue("stale")["key"]
        w1.claim(stale)
        review = self.issue("review")["key"]
        b.move(review, "in_progress")
        b.submit_review(review, note="x")
        blocked = self.issue("blocked")["key"]
        b.link(unassigned, "blocks", blocked)
        overdue = self.issue("late", due="2026-11-01")["key"]
        self.clock.advance(hours=3)
        report = b.triage()
        keys = lambda name: [i["key"] for i in report[name]]  # noqa: E731
        self.assertEqual(keys("backlog"), [backlog])
        self.assertIn(unassigned, keys("unassigned"))
        self.assertEqual(keys("stale"), [stale])
        self.assertEqual(keys("review_queue"), [review])
        self.assertEqual(report["review_since"], {review: "2026-11-03T09:00:00Z"})
        self.assertEqual(keys("blocked"), [blocked])
        self.assertEqual(keys("overdue"), [overdue])
        self.assertEqual([a["handle"] for a in report["idle_agents"]], ["idle-1"])
        self.assertEqual(report["counts"]["unassigned"], 3)
        self.assertNotIn("owner", [a["handle"] for a in report["idle_agents"]])
        del idle


class WaitCheck(BoardCase):
    def test_modes(self):
        w1, r1 = self.agent("w1"), self.agent("r1", "reviewer")
        self.assertIsNone(w1.wait_check("any"))
        key = self.issue("x")["key"]
        self.assertEqual(w1.wait_check("work"), {"reason": "work", "key": key, "title": "x"})
        self.assertIsNone(w1.wait_check("review"))
        self.assertIsNone(w1.wait_check("inbox"))
        w1.claim(key)
        w1.submit_review(key, note="please @r1")
        self.assertEqual(r1.wait_check("review")["key"], key)
        self.assertEqual(r1.wait_check("any"), {"reason": "inbox", "count": 1})
        self.assertIsNone(w1.wait_check("any"))
        with self.assertRaises(InvalidRequest):
            w1.wait_check("forever")
        with self.assertRaises(IdentityRequired):
            self.open_as(None).wait_check("any")


if __name__ == "__main__":
    unittest.main()
