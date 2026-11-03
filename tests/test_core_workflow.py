"""Transitions and side effects, holders and leases, stale takeover, next (work and review)."""

from __future__ import annotations

import unittest

from tests.support import BoardCase

from earmark import model
from earmark.errors import Conflict, Empty, Forbidden, InvalidRequest

PATHS = {  # status -> moves (by the owner) that reach it from a fresh todo issue
    "backlog": ["backlog"], "todo": [], "in_progress": ["in_progress"],
    "in_review": ["in_progress", "in_review"], "blocked": ["blocked"],
    "done": ["in_progress", "done"], "canceled": ["canceled"],
}


def actions(board, key):
    return [e["action"] for e in board.issue_events(key)]


class Transitions(BoardCase):
    def at(self, status: str) -> str:
        key = self.issue(f"at {status}")["key"]
        for step in PATHS[status]:
            self.board.move(key, step)
        if status in ("in_progress", "in_review"):  # leave no live holder in the way
            self.board.assign(key, None)
        return key

    def test_table_for_regular_actors(self):
        worker = self.agent("w1")
        pairs = [(s, t) for s in model.STATUSES for t in model.STATUSES if s != t]
        for src, target in pairs:
            key = self.at(src)
            allowed = target in model.TRANSITIONS[src]
            if src == "in_progress" and target == "done":
                allowed = False  # require_review
            if src == "in_review" and target in ("done", "todo"):
                allowed = True
            with self.subTest(src=src, target=target):
                if allowed:
                    self.assertEqual(worker.move(key, target)["status"], target)
                else:
                    with self.assertRaises((InvalidRequest, Forbidden)):
                        worker.move(key, target)
                    self.assertEqual(self.board.get_issue(key)["status"], src)

    def test_owner_and_pm_make_any_move(self):
        pm = self.agent("pm-1", "pm")
        key = self.issue("x", status="backlog")["key"]
        self.assertEqual(pm.move(key, "done")["status"], "done")
        self.assertEqual(self.board.move(key, "in_review")["status"], "in_review")
        self.assertEqual(self.board.move(key, "backlog")["status"], "backlog")
        with self.assertRaises(InvalidRequest):
            self.board.move(key, "backlog")
        with self.assertRaises(InvalidRequest):
            self.board.move(key, "finished")

    def test_require_review_gate(self):
        worker = self.agent("w1")
        key = self.issue("x")["key"]
        worker.claim(key)
        with self.assertRaises(Forbidden) as ctx:
            worker.done(key)
        self.assertIn("earmark review", ctx.exception.hint)
        self.board.config("require_review", "0")
        self.assertEqual(worker.done(key, note="trivial")["status"], "done")

    def test_side_effects(self):
        w1, r1 = self.agent("w1"), self.agent("r1", "reviewer")
        key = self.issue("x")["key"]
        claimed = w1.move(key, "in_progress")
        self.assertEqual((claimed["assignee"], claimed["lease_until"], claimed["started_at"]),
                         ("w1", "2026-11-03T11:00:00Z", "2026-11-03T09:00:00Z"))
        self.clock.advance(minutes=10)
        w1.move(key, "blocked", note="waiting on API")
        blocked = self.board.get_issue(key)
        self.assertEqual((blocked["assignee"], blocked["lease_until"]), ("w1", None))
        again = w1.move(key, "in_progress")
        self.assertEqual(again["started_at"], "2026-11-03T09:00:00Z")
        review = w1.submit_review(key, note="changed x; run tests")
        self.assertEqual((review["status"], review["reviewer"], review["lease_until"],
                          review["review_requested_at"]), ("in_review", None, None, "2026-11-03T09:10:00Z"))
        r1.claim(key)
        rejected = r1.reject(key, note="fails on reload")
        self.assertEqual((rejected["status"], rejected["assignee"], rejected["reviewer"]), ("todo", "w1", None))
        w1.claim(key)
        w1.submit_review(key, note="fixed")
        done = r1.approve(key)
        self.assertEqual((done["status"], done["reviewer"], done["lease_until"], done["resolved_at"]),
                         ("done", "r1", None, "2026-11-03T09:10:00Z"))
        reopened = self.board.reopen(key, note="regressed")
        self.assertEqual((reopened["status"], reopened["assignee"], reopened["reviewer"],
                          reopened["resolved_at"]), ("todo", "w1", None, None))
        kinds = [c["kind"] for c in self.board.list_comments(key)["items"]]
        self.assertEqual(kinds, ["note", "handoff", "changes", "handoff", "approval", "note"])
        log = actions(self.board, key)
        for action in ("claim", "status", "review", "reject", "approve", "reopen"):
            self.assertIn(action, log)

    def test_release_and_cancel(self):
        w1 = self.agent("w1")
        key = self.issue("x")["key"]
        w1.claim(key)
        released = w1.release(key, note="not my area")
        self.assertEqual((released["status"], released["assignee"], released["lease_until"]),
                         ("todo", None, None))
        self.assertIn("release", actions(self.board, key))
        with self.assertRaises(InvalidRequest):
            w1.release(key)
        with self.assertRaises(InvalidRequest):
            w1.cancel(key, note="")
        self.assertEqual(w1.cancel(key, note="obsolete")["status"], "canceled")
        with self.assertRaises(InvalidRequest):
            w1.reopen(self.issue("open one")["key"], note="why")
        self.assertEqual(w1.reopen(key, note="needed after all")["status"], "todo")

    def test_review_rules(self):
        w1, w2 = self.agent("w1"), self.agent("w2")
        key = self.issue("x")["key"]
        w1.claim(key)
        with self.assertRaises(InvalidRequest):
            w1.submit_review(key, note="  ")
        with self.assertRaises(InvalidRequest):
            w1.approve(key)
        w1.submit_review(key, note="done")
        with self.assertRaises(Forbidden):
            w1.approve(key)
        with self.assertRaises(Forbidden):
            w1.claim(key)
        with self.assertRaises(InvalidRequest):
            w2.reject(key, note="")
        approved = w2.approve(key, note="verified")
        self.assertEqual(approved["status"], "done")
        last = self.board.list_comments(key)["items"][-1]
        self.assertEqual((last["kind"], last["body"], last["author"]), ("approval", "verified", "w2"))

    def test_moves_bump_version_comments_do_not(self):
        w1 = self.agent("w1")
        key = self.issue("x")["key"]
        self.assertEqual(w1.claim(key)["version"], 2)
        w1.add_comment(key, "progress", kind="progress")
        self.assertEqual(self.board.get_issue(key)["version"], 2)
        self.assertEqual(w1.submit_review(key, note="ok")["version"], 3)


class Leases(BoardCase):
    def setUp(self):
        super().setUp()
        self.w1, self.w2 = self.agent("w1"), self.agent("w2")
        self.key = self.issue("x")["key"]
        self.w1.claim(self.key)

    def lease(self):
        return self.board.get_issue(self.key)["lease_until"]

    def test_holder_writes_renew(self):
        self.assertEqual(self.lease(), "2026-11-03T11:00:00Z")
        self.clock.advance(minutes=30)
        self.w1.add_comment(self.key, "progress", kind="progress")
        self.assertEqual(self.lease(), "2026-11-03T11:30:00Z")
        self.clock.advance(minutes=30)
        self.w1.update_issue(self.key, priority="high")
        self.assertEqual(self.lease(), "2026-11-03T12:00:00Z")
        self.clock.advance(minutes=30)
        self.w1.claim(self.key)
        self.assertEqual(self.lease(), "2026-11-03T12:30:00Z")
        self.clock.advance(minutes=30)
        self.w2.add_comment(self.key, "not the holder")
        self.w2.update_issue(self.key, labels=["x"])
        self.assertEqual(self.lease(), "2026-11-03T12:30:00Z")

    def test_live_lease_blocks_others_but_not_comments(self):
        with self.assertRaises(Conflict) as ctx:
            self.w2.claim(self.key)
        self.assertEqual(ctx.exception.details["holder"], "w1")
        self.assertEqual(ctx.exception.details["lease_until"], "2026-11-03T11:00:00Z")
        self.assertIn("w1", ctx.exception.message)
        with self.assertRaises(Conflict):
            self.w2.move(self.key, "blocked")
        with self.assertRaises(Conflict):
            self.w2.release(self.key)
        self.assertEqual(self.w2.add_comment(self.key, "@w1 ping")["mentions"], ["w1"])
        self.assertEqual(self.w2.update_issue(self.key, title="better title")["title"], "better title")
        self.assertEqual(self.board.move(self.key, "blocked")["status"], "blocked")

    def test_stale_takeover(self):
        self.clock.advance(minutes=121)
        summary = self.board.list_issues(stale=True)["items"]
        self.assertEqual([i["key"] for i in summary], [self.key])
        self.assertTrue(summary[0]["stale"])
        taken = self.w2.claim(self.key)
        self.assertEqual((taken["assignee"], taken["stale"], taken["lease_until"]),
                         ("w2", False, "2026-11-03T13:01:00Z"))
        event = self.board.issue_events(self.key)[-1]
        self.assertEqual((event["action"], event["old"], event["new"], event["actor"]),
                         ("takeover", "w1", "w2", "w2"))
        with self.assertRaises(Conflict):
            self.w1.claim(self.key)

    def test_stale_issue_goes_back_through_next(self):
        self.clock.advance(hours=3)
        picked = self.w2.next()
        self.assertEqual((picked["key"], picked["assignee"]), (self.key, "w2"))
        self.assertEqual(actions(self.board, self.key)[-1], "takeover")

    def test_lease_minutes_setting(self):
        self.board.config("lease_minutes", "30")
        key = self.issue("y")["key"]
        self.assertEqual(self.w2.claim(key)["lease_until"], "2026-11-03T09:30:00Z")

    def test_stale_review_can_be_taken(self):
        r1, r2 = self.agent("r1", "reviewer"), self.agent("r2", "reviewer")
        self.w1.submit_review(self.key, note="done")
        r1.next()
        with self.assertRaises(Conflict):
            r2.claim(self.key)
        self.clock.advance(minutes=121)
        self.assertEqual(r2.next()["reviewer"], "r2")
        self.assertEqual(actions(self.board, self.key)[-1], "takeover")
        released = r2.release(self.key)
        self.assertEqual((released["status"], released["reviewer"]), ("in_review", None))


class Assigned(BoardCase):
    def test_assigned_todo_is_reserved_for_the_assignee(self):
        w1, w2 = self.agent("w1"), self.agent("w2")
        key = self.issue("x")["key"]
        self.board.assign(key, "w1")
        with self.assertRaises(Conflict) as ctx:
            w2.claim(key)
        self.assertEqual(ctx.exception.details["assignee"], "w1")
        with self.assertRaises(Empty):
            w2.next()
        self.assertEqual(w1.next()["key"], key)


class Next(BoardCase):
    def test_work_order(self):
        w1 = self.agent("w1")
        self.agent("w2")
        b = self.board
        low = self.issue("low", priority="low")["key"]
        high_late = self.issue("high late", priority="high", due="2026-11-20")["key"]
        high_none = self.issue("high no due", priority="high")["key"]
        high_soon = self.issue("high soon", priority="high", due="2026-11-05")["key"]
        high_ranked = self.issue("high soon ranked", priority="high", due="2026-11-05")["key"]
        b.rank(high_ranked, top=True)
        mine = self.issue("mine but low", priority="none")["key"]
        b.assign(mine, "w1")
        theirs = self.issue("theirs urgent", priority="urgent")["key"]
        b.assign(theirs, "w2")
        order = []
        while True:
            try:
                picked = w1.next()
            except Empty:
                break
            order.append(picked["key"])
            w1.submit_review(picked["key"], note="done")
        self.assertEqual(order, [mine, high_ranked, high_soon, high_late, high_none, low])

    def test_exclusions_blockers_and_statuses(self):
        w1 = self.agent("w1")
        blocker = self.issue("blocker", priority="low")["key"]
        blocked = self.issue("blocked", priority="urgent")["key"]
        self.board.link(blocker, "blocks", blocked)
        self.issue("backlog", status="backlog", priority="urgent")
        self.assertEqual(w1.next()["key"], blocker)
        w1.submit_review(blocker, note="done")
        with self.assertRaises(Empty):
            w1.next()
        self.agent("r1", "reviewer").next()
        self.board.approve(blocker)
        self.assertEqual(w1.next()["key"], blocked)

    def test_scope_and_topic_narrowing(self):
        b = self.board
        for path in ("ui-ux/menu", "ui-ux/calls", "backend"):
            b.add_topic(path)
        b.add_profile("ui-lead", name="UI lead", role="worker", topics=["ui-ux"])
        ui = self.agent("ui-1", "ui-lead")
        anyone = self.agent("w1")
        api = self.issue("api", topic="backend", priority="urgent")["key"]
        calls = self.issue("calls", topic="ui-ux/calls", priority="high")["key"]
        menu = self.issue("menu", topic="ui-ux/menu")["key"]
        loose = self.issue("loose", priority="urgent")["key"]
        self.assertEqual(ui.next(topics=["ui-ux/menu"], dry_run=True)["key"], menu)
        with self.assertRaises(Empty) as ctx:
            ui.next(topics=["backend"])
        self.assertEqual(ctx.exception.details["scope"], ["backend"])
        self.assertEqual(ui.next()["key"], calls)
        self.assertEqual(ui.next()["key"], menu)
        with self.assertRaises(Empty) as ctx:
            ui.next()
        self.assertEqual(ctx.exception.details["scope"], ["ui-ux"])
        self.assertEqual(anyone.next()["key"], api)
        self.assertEqual(anyone.next()["key"], loose)

    def test_review_mode(self):
        w1, w2 = self.agent("w1"), self.agent("w2")
        r1 = self.agent("r1", "reviewer")
        first = self.issue("first", priority="low")["key"]
        second = self.issue("second", priority="low")["key"]
        urgent = self.issue("urgent", priority="urgent")["key"]
        for key in (first, second, urgent):
            w1.claim(key)
        w1.submit_review(second, note="x")
        self.clock.advance(minutes=1)
        w1.submit_review(first, note="x")
        self.clock.advance(minutes=1)
        w1.submit_review(urgent, note="x")
        with self.assertRaises(Empty):
            w1.next(review=True)
        own = self.issue("own", priority="low")["key"]
        w2.claim(own)
        w2.submit_review(own, note="mine")
        self.assertEqual(w2.next(review=True)["key"], urgent)
        picked = r1.next()
        self.assertEqual((picked["key"], picked["reviewer"]), (second, "r1"))
        self.assertEqual(r1.next()["key"], first)
        self.assertEqual(r1.next()["key"], own)
        with self.assertRaises(Empty) as ctx:
            r1.next()
        self.assertEqual(ctx.exception.details["mode"], "review")

    def test_empty_details_and_dry_run(self):
        w1, w2 = self.agent("w1"), self.agent("w2")
        key = self.issue("x")["key"]
        watcher = self.open_as(None)
        before = watcher.data_version()
        self.assertEqual(w1.next(dry_run=True)["status"], "todo")
        self.assertEqual(watcher.data_version(), before)
        w1.next()
        w1.submit_review(key, note="please check @w2")
        stale = self.issue("stale one")["key"]
        self.board.move(stale, "in_progress")
        self.board.link(self.issue("blocker")["key"], "blocks", self.issue("blocked")["key"])
        self.board.move("GAME-3", "blocked")
        self.clock.advance(hours=3)
        w2.next()  # takes the stale one
        self.board.assign("GAME-3", "w2")
        with self.assertRaises(Empty) as ctx:
            w2.next()
        details = ctx.exception.details
        self.assertEqual(details, {"review_queue": 1, "inbox": 1, "stale": 0, "scope": [],
                                   "mode": "work"})
        self.assertEqual(ctx.exception.hint, "earmark next --review")
        self.assertEqual(ctx.exception.exit_code, 4)

    def test_next_requires_a_joined_actor(self):
        from earmark.errors import IdentityRequired, NotJoined

        self.issue("x")
        with self.assertRaises(IdentityRequired):
            self.open_as(None).next()
        with self.assertRaises(NotJoined):
            self.open_as("ghost").next()


if __name__ == "__main__":
    unittest.main()
