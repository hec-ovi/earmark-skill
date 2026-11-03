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
        ui1 = self.agent("ui-1", "ui-lead")
        self.assertEqual(b.get_profile("ui-lead")["agents"], ["ui-1"])
        ui1.leave()
        with self.assertRaises(InvalidRequest) as used:  # offline agents still use it
            b.remove_profile("ui-lead")
        self.assertEqual(used.exception.details, {"agents": ["ui-1"]})
        self.assertIn("ui-1", used.exception.message)
        self.assertIn("earmark join worker --as ui-1", used.exception.hint)
        self.assertEqual(b.get_profile("ui-lead")["agents"], ["ui-1"])
        ui1.join("worker")
        self.assertEqual(b.remove_profile("ui-lead"), {"deleted": "ui-lead"})
        self.assertEqual(self.open_as("ui-1").whoami()["profile"]["slug"], "worker")
        actions = [e["action"] for e in b.activity()["items"]]
        for action in ("profile_add", "profile_edit", "profile_remove"):
            self.assertIn(action, actions)


class JoinLeave(BoardCase):
    def test_default_handles_never_reuse_an_existing_one(self):
        first = self.open_as(None)
        self.assertEqual(first.join("worker")["agent"]["handle"], "worker-1")
        self.assertEqual(first.actor, "worker-1")
        second = self.open_as(None)
        self.assertEqual(second.join("worker")["agent"]["handle"], "worker-2")
        first.leave()
        third = self.open_as(None)
        self.assertEqual(third.join("worker")["agent"]["handle"], "worker-3")
        self.assertEqual(self.open_as(None).join("reviewer")["agent"]["handle"], "reviewer-1")

    def test_new_agent_never_inherits_the_work_of_one_that_left(self):  # r04
        self.issue("a")
        first = self.open_as(None)
        self.assertEqual(first.join("worker", cli="claude-code")["agent"]["handle"], "worker-1")
        first.next()
        first.leave(keep=True)
        second = self.open_as(None).join("worker", cli="codex")["agent"]
        self.assertEqual((second["handle"], second["cli"], second["working"]),
                         ("worker-2", "codex", []))

    def test_cross_cli_join_as_an_existing_handle(self):
        b = self.board
        codex = self.agent("worker-2", cli="codex")
        key = self.issue("handoff")["key"]
        codex.claim(key)
        codex.submit_review(key, note="ready")
        grok = self.open_as(None)
        with self.assertRaises(Conflict) as online:  # someone else's live identity
            grok.join("worker", handle="worker-2", cli="grok")
        self.assertEqual(online.exception.details,
                         {"handle": "worker-2", "cli": "codex", "online": True, "profile": "worker"})
        self.assertIn("earmark join worker --cli grok", online.exception.hint)
        self.assertEqual(grok.actor, None)
        codex.leave(keep=True)
        with self.assertRaises(Conflict) as other_profile:
            grok.join("reviewer", handle="worker-2", cli="grok")
        self.assertIn("earmark join worker --as worker-2 --cli grok", other_profile.exception.hint)
        agent = b.team()["agents"]
        self.assertEqual([(a["handle"], a["cli"], a["online"]) for a in agent if a["handle"] == "worker-2"],
                         [("worker-2", "codex", False)])
        resumed = grok.join("worker", handle="worker-2", cli="grok")  # deliberate resume
        self.assertEqual((resumed["agent"]["cli"], resumed["agent"]["online"]), ("grok", True))
        log = [(e["action"], e["new"]) for e in b.activity()["items"] if e["action"] in ("join", "rejoin")]
        self.assertEqual(log[0], ("rejoin", "worker cli=grok"))
        self.assertIn(("join", "worker cli=codex"), log)
        same = self.agent("a1", cli="codex")
        self.assertEqual(same.join("worker", cli="codex")["agent"]["handle"], "a1")
        self.assertEqual(same.join("worker")["agent"]["cli"], "codex")  # no cli given: kept

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


class Resume(BoardCase):
    """`join PROFILE --cli C` without a handle resumes the seat a session of C left."""

    def fresh(self, profile: str, cli: str | None = None) -> str:
        """A new session (no handle, no actor) joins; returns the handle it got."""
        return self.open_as(None).join(profile, cli=cli)["agent"]["handle"]

    def test_resume_after_leave_on_the_same_cli(self):
        b = self.board
        first = self.open_as(None)
        self.assertEqual(first.join("reviewer", cli="claude-code")["agent"]["handle"], "reviewer-1")
        self.assertEqual(first.join_action, "join")
        w1 = self.agent("w1")
        key = self.issue("feature")["key"]
        w1.claim(key)
        w1.submit_review(key, note="ready")
        self.assertEqual(first.next()["key"], key)
        first.reject(key, note="fails on reload")
        first.list_comments(key, new=True)
        parked = self.issue("parked")["key"]
        b.assign(parked, "reviewer-1")
        b.move(parked, "blocked")  # leave keeps blocked work on its handle
        first.leave()
        self.clock.advance(minutes=30)
        b.add_comment(parked, "unblocks tomorrow")  # while it was away
        later = self.open_as(None)
        brief = later.join("reviewer", cli="claude-code")
        self.assertEqual((brief["agent"]["handle"], brief["agent"]["online"], later.actor,
                          later.join_action), ("reviewer-1", True, "reviewer-1", "rejoin"))
        self.assertEqual(([i["key"] for i in brief["assigned"]], brief["inbox_new"]), ([parked], 1))
        log = [(e["actor"], e["action"], e["new"]) for e in b.activity()["items"]
               if e["action"] in ("join", "rejoin")]
        self.assertEqual(log[0], ("reviewer-1", "rejoin", "reviewer cli=claude-code"))
        self.assertEqual(later.list_comments(key, new=True)["items"], [])  # issue cursor kept
        feed = later.check()  # check cursor kept: what changed while it was away
        self.assertEqual([c["key"] for c in feed["changed"]], [parked])
        self.assertEqual(self.fresh("reviewer", "claude-code"), "reviewer-2")  # it is online again

    def test_no_resume_across_clis_or_profiles(self):
        codex = self.open_as(None)
        self.assertEqual(codex.join("worker", cli="codex")["agent"]["handle"], "worker-1")
        codex.leave()
        self.assertEqual(self.fresh("worker", "grok"), "worker-2")
        self.assertEqual(self.fresh("reviewer", "codex"), "reviewer-1")
        self.assertEqual(self.fresh("worker"), "worker-3")  # no --cli: a new handle, as before
        gone = {a["handle"]: (a["online"], a["cli"]) for a in self.board.team()["agents"]}
        self.assertEqual(gone["worker-1"], (False, "codex"))
        self.assertEqual(self.fresh("worker", "codex"), "worker-1")

    def test_no_resume_of_an_online_handle(self):
        self.assertEqual(self.fresh("worker", "codex"), "worker-1")
        second = self.open_as(None)
        self.assertEqual(second.join("worker", cli="codex")["agent"]["handle"], "worker-2")
        self.assertEqual(second.join_action, "join")
        online = {a["handle"]: a["online"] for a in self.board.team()["agents"]}
        self.assertEqual((online["worker-1"], online["worker-2"]), (True, True))

    def test_two_offline_matches_resume_the_most_recently_seen(self):
        seats = {}
        for _ in range(3):
            agent = self.open_as(None)
            seats[agent.join("worker", cli="codex")["agent"]["handle"]] = agent
        for handle in ("worker-1", "worker-3", "worker-2"):  # left at 09:00, 09:05, 09:10
            seats[handle].leave()
            self.clock.advance(minutes=5)
        self.assertEqual([self.fresh("worker", "codex") for _ in range(4)],
                         ["worker-2", "worker-3", "worker-1", "worker-4"])


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

    def test_reads_never_bring_back_an_agent_that_left(self):
        w1 = self.agent("w1")
        self.clock.advance(minutes=5)
        w1.leave()
        watcher = self.open_as(None)
        version = watcher.data_version()
        self.clock.advance(minutes=10)
        w1.touch()  # every read command with --as
        w1.whoami()
        self.assertEqual(watcher.data_version(), version)  # no write at all
        w1.check()  # its cursor moves, the agent stays where it was
        w1.touch()
        state = {a["handle"]: a for a in self.board.team()["agents"]}["w1"]
        self.assertEqual((state["online"], state["presence"], state["last_seen"]),
                         (False, "offline", "2026-11-03T09:05:00Z"))
        self.clock.advance(minutes=1)
        w1.create_issue("back")  # a write does
        state = {a["handle"]: a for a in self.board.team()["agents"]}["w1"]
        self.assertEqual((state["online"], state["presence"], state["last_seen"]),
                         (True, "active", "2026-11-03T09:16:00Z"))


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
