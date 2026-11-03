"""`need` (a blocker for another specialist, the issue parked), auto-unblock, effective priority
and blocker-first `next`, work of agents that left, and one timestamp per claiming transaction."""

from __future__ import annotations

import io
import unittest
from unittest import mock

from tests.support import BoardCase

from earmark import timeutil
from earmark.errors import Conflict, Empty, InvalidRequest, NotFound, PayloadTooLarge


class NeedCase(BoardCase):
    def setUp(self):
        super().setUp()
        b = self.board
        for path in ("ui-ux/hud", "artwork/icons", "audio"):
            b.add_topic(path)
        b.add_profile("ui-lead", name="UI lead", role="worker", topics=["ui-ux"])
        b.add_profile("art", name="Art", role="worker", topics=["artwork"])
        self.ui = self.agent("claude-ui", "ui-lead")
        self.art = self.agent("codex-art", "art")
        self.rev = self.agent("codex-qa", "reviewer")

    def events(self, key: str) -> list[tuple]:
        return [(e["action"], e["actor"], e["old"], e["new"]) for e in self.board.issue_events(key)]


class Need(NeedCase):
    def test_holder_need_parks_the_issue(self):
        key = self.issue("Ammo counter overlaps minimap", topic="ui-ux/hud", priority="high")["key"]
        self.ui.claim(key)
        self.clock.advance(minutes=5)
        out = self.ui.need(key, "Ammo icon at 24 px", topic="artwork/icons", body="Two variants.",
                           labels=["hud"], attach=[("ref.png", io.BytesIO(b"\x89PNG ref"))])
        new, parked = out["created"], out["blocked"]
        self.assertEqual((new["key"], new["status"], new["topic"], new["reporter"], new["priority"],
                          new["type"], new["labels"], new["assignee"], new["body"]),
                         ("GAME-2", "todo", "artwork/icons", "claude-ui", "high", "task", ["hud"], None,
                          "Two variants."))
        self.assertEqual(new["links"]["blocks"], [key])
        self.assertEqual([a["name"] for a in new["attachments"]], ["ref.png"])
        self.assertEqual((parked["status"], parked["assignee"], parked["lease_until"]),
                         ("blocked", "claude-ui", None))
        self.assertEqual(parked["links"]["blocked_by"], ["GAME-2"])
        self.assertEqual(parked["link_status"], {"GAME-2": "todo"})
        self.assertTrue(parked["blocked"])
        note = self.board.list_comments(key)["items"][-1]
        self.assertEqual((note["author"], note["kind"], note["body"]),
                         ("claude-ui", "note", "waiting on GAME-2: Ammo icon at 24 px"))
        self.assertIn(("status", "claude-ui", "in_progress", "blocked"), self.events(key))
        self.assertEqual(self.events("GAME-2")[0], ("created", "claude-ui", None, "Ammo icon at 24 px"))

    def test_assign_priority_type_and_non_holder(self):
        key = self.issue("Screen", topic="ui-ux/hud")["key"]
        out = self.ui.need(key, "Splash art", topic="artwork/icons", assignee="codex-art",
                           priority="urgent", type="feature")
        new = out["created"]
        self.assertEqual((new["assignee"], new["priority"], new["type"]), ("codex-art", "urgent", "feature"))
        self.assertEqual(out["blocked"]["status"], "todo")  # not held by the actor: only linked
        self.assertIn(("assignee", "claude-ui", None, "codex-art"), self.events(new["key"]))
        self.assertEqual(self.board.get_issue(key)["blocked"], True)

    def test_refusals_change_nothing(self):
        key = self.issue("Screen", topic="ui-ux/hud")["key"]
        self.ui.claim(key)
        cases = [(NotFound, dict(topic="nope")), (InvalidRequest, dict(topic="")),
                 (NotFound, dict(topic="audio", assignee="ghost")),
                 (InvalidRequest, dict(topic="audio", priority="whenever"))]
        for exc, kw in cases:
            with self.subTest(kw=kw), self.assertRaises(exc):
                self.ui.need(key, "Sound", **kw)
        self.board.config("max_attachment_mb", "1")
        with self.assertRaises(PayloadTooLarge):
            self.ui.need(key, "Sound", topic="audio", attach=[("big", io.BytesIO(b"\0" * (1024 * 1024 + 1)))])
        with self.assertRaises(NotFound):
            self.ui.need("GAME-9", "Sound", topic="audio")
        self.assertEqual(self.board.list_issues(status="all")["total"], 1)
        self.assertEqual(self.board.get_issue(key)["status"], "in_progress")
        self.board.move(key, "canceled", note="dropped")
        with self.assertRaises(InvalidRequest):
            self.board.need(key, "Sound", topic="audio")


class AutoUnblock(NeedCase):
    def test_round_trip_back_to_the_first_worker(self):
        key = self.issue("HUD ammo", topic="ui-ux/hud", priority="medium")["key"]
        self.issue("Urgent but unassigned", topic="ui-ux/hud", priority="urgent")
        self.assertEqual(self.ui.next()["key"], "GAME-2")  # urgent first
        self.ui.release("GAME-2")
        self.ui.claim(key)
        blocker = self.ui.need(key, "Ammo icon", topic="artwork/icons")["created"]["key"]
        picked = self.art.next()
        self.assertEqual(picked["key"], blocker)
        self.art.add_comment(blocker, "icon attached", attach=[("ammo.png", io.BytesIO(b"png"))])
        self.art.submit_review(blocker, note="24 px and 48 px")
        self.assertEqual(self.rev.next(review=True)["key"], blocker)
        self.clock.advance(minutes=1)
        self.rev.approve(blocker, note="ok")
        back = self.board.get_issue(key)
        self.assertEqual((back["status"], back["assignee"], back["blocked"]), ("todo", "claude-ui", False))
        self.assertEqual(back["link_status"], {blocker: "done"})
        self.assertIn(("unblocked", "codex-qa", f"{blocker} done", "todo"), self.events(key))
        self.assertEqual(self.ui.next()["key"], key)  # assigned to the worker: before the urgent one

    def test_every_open_blocker_must_close(self):
        key = self.issue("Waits on two", topic="ui-ux/hud")["key"]
        self.ui.claim(key)
        one = self.ui.need(key, "Icon", topic="artwork/icons")["created"]["key"]
        two = self.ui.need(key, "Sound", topic="audio")["created"]["key"]
        self.board.done(one, note="made")
        self.assertEqual(self.board.get_issue(key)["status"], "blocked")
        self.board.cancel(two, note="not needed")
        self.assertEqual(self.board.get_issue(key)["status"], "todo")
        self.assertIn(("unblocked", "owner", f"{two} canceled", "todo"), self.events(key))

    def test_only_blocked_dependents_move(self):
        waiting = self.issue("Waiting", topic="ui-ux/hud")["key"]
        planned = self.issue("Planned", topic="ui-ux/hud", status="backlog")["key"]
        manual = self.issue("Manual block", topic="ui-ux/hud")["key"]
        blocker = self.issue("Blocker", topic="ui-ux/hud")["key"]
        for k in (waiting, planned, manual):
            self.board.link(blocker, "blocks", k)
        self.board.move(manual, "blocked", note="also waits on a person")
        self.board.move(blocker, "done")
        statuses = {k: self.board.get_issue(k)["status"] for k in (waiting, planned, manual)}
        self.assertEqual(statuses, {waiting: "todo", planned: "backlog", manual: "todo"})
        alone = self.issue("Blocked by hand", topic="ui-ux/hud")["key"]
        self.board.move(alone, "blocked", note="waiting on a vendor")
        self.board.move(self.issue("Unrelated")["key"], "done")
        self.assertEqual(self.board.get_issue(alone)["status"], "blocked")


class EffectivePriority(NeedCase):
    def test_blockers_take_the_priority_they_hold_up(self):
        low = self.issue("Low asset", topic="artwork/icons", priority="low")["key"]
        high = self.issue("High feature", topic="ui-ux/hud", priority="high")["key"]
        urgent = self.issue("Urgent", topic="ui-ux/hud", priority="urgent")["key"]
        self.board.link(low, "blocks", high)
        self.board.link(high, "blocks", urgent)
        summary = {i["key"]: i for i in self.board.list_issues()["items"]}
        self.assertEqual((summary[low]["priority"], summary[low]["effective_priority"],
                          summary[low]["blocks_open"]), ("low", "high", 1))  # one level, not urgent
        self.assertEqual(summary[high]["effective_priority"], "urgent")
        self.assertEqual((summary[urgent]["effective_priority"], summary[urgent]["blocks_open"]), ("urgent", 0))
        self.board.move(high, "canceled", note="cut")
        after = {i["key"]: i for i in self.board.list_issues(status="all")["items"]}
        self.assertEqual((after[low]["effective_priority"], after[low]["blocks_open"]), ("low", 0))
        self.assertEqual(after[high]["blocks_open"], 0)  # a closed issue holds nothing up

    def test_next_works_blockers_at_their_effective_priority(self):
        self.board.edit_profile("art", if_version=self.board.get_profile("art")["version"], topics=[])
        self.issue("Medium, free", topic="artwork/icons", priority="medium")
        low = self.issue("Low, holds up high", topic="artwork/icons", priority="low")["key"]
        self.board.link(low, "blocks", self.issue("High", topic="ui-ux/hud", priority="high")["key"])
        self.issue("High, free", topic="artwork/icons", priority="high")
        first = self.art.next()
        self.assertEqual(first["title"], "Low, holds up high")  # high tie: the blocker first
        self.assertEqual(self.art.next()["title"], "High, free")

    def test_review_queue_orders_by_effective_priority(self):
        plain = self.issue("Plain review", topic="ui-ux/hud", priority="medium")["key"]
        blocker = self.issue("Blocker review", topic="ui-ux/hud", priority="low")["key"]
        self.board.link(blocker, "blocks", self.issue("Urgent", topic="ui-ux/hud", priority="urgent")["key"])
        for k in (plain, blocker):
            self.ui.claim(k)
            self.ui.submit_review(k, note="done")
            self.clock.advance(minutes=1)
        self.assertEqual(self.rev.next(review=True)["key"], blocker)


class OfflineAssignees(NeedCase):
    def test_work_of_an_agent_that_left_is_claimable(self):
        w1, w2 = self.agent("w1"), self.agent("w2")
        key = self.issue("x")["key"]
        w1.next()
        w1.submit_review(key, note="done, run tests")
        w1.leave()
        self.rev.next(review=True)
        rejected = self.rev.reject(key, note="fails on reload")
        self.assertEqual((rejected["status"], rejected["assignee"]), ("todo", "w1"))
        report = self.board.triage()
        self.assertEqual([i["key"] for i in report["offline"]], [key])
        self.assertEqual(report["counts"]["offline"], 1)
        taken = w2.next()
        self.assertEqual((taken["key"], taken["status"], taken["assignee"]), (key, "in_progress", "w2"))
        self.assertIn(("takeover", "w2", "w1", "w2"), self.events(key))
        self.assertNotIn("claim", [e[0] for e in self.events(key)][-2:])
        self.assertEqual(self.board.triage()["offline"], [])

    def test_claim_and_move_take_over_too(self):
        w1, w2 = self.agent("w1"), self.agent("w2")
        a, b = self.issue("a")["key"], self.issue("b")["key"]
        self.board.assign(a, "w1")
        self.board.assign(b, "w1")
        with self.assertRaises(Conflict):
            w2.claim(a)  # w1 is still here
        w1.leave()
        self.assertEqual(w2.claim(a)["assignee"], "w2")
        self.assertEqual(w2.move(b, "in_progress")["assignee"], "w2")

    def test_any_command_brings_an_agent_back(self):
        w1 = self.agent("w1")
        w1.leave()
        self.assertFalse(self.agent_row("w1")["online"])
        w1.touch()
        self.assertTrue(self.agent_row("w1")["online"])
        w1.leave()
        w1.add_comment(self.issue("x")["key"], "back")
        self.assertTrue(self.agent_row("w1")["online"])

    def agent_row(self, handle: str) -> dict:
        return {a["handle"]: a for a in self.board.team()["agents"]}[handle]


class OneTimestamp(NeedCase):
    def test_pick_and_claim_agree_at_the_lease_boundary(self):
        """A second later than the transaction's time, the held issue would look stale to the
        pick while the claim still sees a live lease (exit 3). The pick must use tx.ts."""
        key = self.issue("held", topic="ui-ux/hud")["key"]
        self.ui.claim(key)
        w2 = self.agent("w2")
        later = timeutil.iso(self.clock() + timeutil_minutes(500))
        with mock.patch.object(timeutil, "now_iso", lambda: later):
            with self.assertRaises(Empty):
                w2.next()
        self.assertEqual(self.board.get_issue(key)["assignee"], "claude-ui")


def timeutil_minutes(n: int):
    from datetime import timedelta

    return timedelta(minutes=n)


if __name__ == "__main__":
    unittest.main()
