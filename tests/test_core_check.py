"""check: the per-agent change feed (kinds, counts, order, cursor) and pending_changes, the
read-only count behind the dirty hint."""

from __future__ import annotations

import unittest

from tests.support import BoardCase

from earmark import db
from earmark.errors import IdentityRequired, NotJoined

CHECK_KEYS = {"agent", "since", "holding", "todo_for_you", "changed", "more", "cursor"}
CHANGED_KEYS = {"key", "priority", "effective_priority", "blocks_open", "action", "changes",
                "comments", "questions", "files"}


def by_key(feed: dict) -> dict:
    return {c["key"]: c for c in feed["changed"]}


def kinds(item: dict) -> list[str]:
    return [c["kind"] for c in item["changes"]]


class Feed(BoardCase):
    def setUp(self):
        super().setUp()
        self.ui = self.agent("claude-ui", cli="claude-code")
        self.pm = self.agent("grok-pm", "pm", cli="grok")
        self.qa = self.agent("codex-qa", "reviewer", cli="codex")

    def step(self) -> None:
        """Separate writes by one actor land in separate seconds (as they would for real)."""
        self.clock.advance(seconds=1)

    def assertPending(self, feed: dict) -> None:
        self.assertEqual(self.ui.pending_changes(), len(feed["changed"]) + feed["more"])

    def test_shape_since_join_and_nothing_new(self):
        before = self.issue("before the join")["key"]
        self.board.assign(before, "claude-ui")
        self.assertEqual(self.ui.pending_changes(), 1)
        self.clock.advance(minutes=5)
        self.ui.join("worker")  # a join (re)starts the feed at the current heads
        feed = self.ui.check()
        self.assertEqual(set(feed), CHECK_KEYS)
        self.assertEqual((feed["agent"], feed["since"], feed["changed"], feed["more"]),
                         ("claude-ui", "2026-11-03T09:05:00Z", [], 0))
        self.assertEqual(feed["cursor"], self.board.heads())
        self.assertEqual(feed["todo_for_you"], 1)
        self.assertEqual(self.ui.pending_changes(), 0)

    def test_kinds_counts_and_own_changes(self):
        b, ui, pm, qa = self.board, self.ui, self.pm, self.qa
        work = self.issue("work")["key"]
        ui.claim(work)
        ui.add_comment(work, "my own progress", kind="progress")  # own writes never count
        ui.update_issue(work, title="work, renamed by me")
        self.assertEqual(ui.check(peek=True)["changed"], [])
        self.step()
        pm.update_issue(work, priority="high", title="Work", body="details")
        pm.add_comment(work, "is the API ready?", kind="question")
        b.add_comment(work, "fyi")
        b.attach(work, [("shot.png", __import__("io").BytesIO(b"png"))])
        assets = pm.create_issue("assets", priority="low", assignee="none")["key"]
        blocked = self.issue("waits on assets", priority="urgent")["key"]
        b.link(assets, "blocks", blocked)
        self.step()
        pm.assign(assets, "claude-ui")
        elsewhere = self.issue("not mine")["key"]
        qa.add_comment(elsewhere, "@claude-ui can you look?")
        feed = ui.check(peek=True)
        items = by_key(feed)
        self.assertEqual(set(items), {work, assets, elsewhere})
        for item in feed["changed"]:
            self.assertEqual(set(item), CHANGED_KEYS)
        w = items[work]
        self.assertEqual(w["changes"], [
            {"kind": "field", "field": "title", "by": "grok-pm"},
            {"kind": "field", "field": "body", "by": "grok-pm"},
            {"kind": "field", "field": "priority", "old": "medium", "new": "high", "by": "grok-pm"}])
        self.assertEqual((w["comments"], w["questions"], w["files"], w["action"]), (2, 1, 1, True))
        a = items[assets]
        self.assertEqual((a["priority"], a["effective_priority"], a["blocks_open"]), ("low", "urgent", 1))
        self.assertIn({"kind": "assigned", "by": "grok-pm"}, a["changes"])
        self.assertTrue(a["action"])
        e = items[elsewhere]
        self.assertEqual((e["changes"], e["comments"], e["questions"], e["action"]), ([], 1, 1, True))
        self.assertPending(feed)

    def test_review_round_trip_unblock_and_cap(self):
        b, ui, qa = self.board, self.ui, self.qa
        b.config("max_review_rounds", "2")
        key = self.issue("feature")["key"]
        ui.claim(key)
        ui.submit_review(key, note="ready")
        ui.check()
        qa.claim(key)  # a reviewer taking the handoff is no change for the worker
        self.assertEqual(ui.check(peek=True)["changed"], [])
        qa.reject(key, note="fails on reload")
        item = by_key(ui.check())[key]
        self.assertEqual((item["changes"], item["comments"], item["action"]),
                         ([{"kind": "rejected", "by": "codex-qa"}], 1, True))
        ui.claim(key)
        ui.submit_review(key, note="fixed")
        qa.claim(key)
        qa.reject(key, note="still fails")
        item = by_key(ui.check())[key]
        self.assertEqual(kinds(item), ["rejected", "review_cap"])
        self.assertEqual(item["comments"], 2)  # the changes note and the cap note
        self.pm.move(key, "todo")
        item = by_key(ui.check())[key]
        self.assertEqual(item["changes"], [{"kind": "status", "old": "blocked", "new": "todo",
                                            "by": "grok-pm"}])
        self.assertFalse(item["action"])
        ui.claim(key)
        dep = ui.need(key, "API endpoint", topic=self._topic())["created"]["key"]
        self.assertEqual(ui.check()["changed"], [])  # all of it was the worker's own doing
        other = self.agent("codex-api")
        other.claim(dep)
        self.step()
        b.move(dep, "done")
        item = by_key(ui.check())[key]
        self.assertEqual(item["changes"], [{"kind": "unblocked", "by_issue": dep, "by": "owner"}])
        self.assertTrue(item["action"])
        ui.claim(key)
        ui.submit_review(key, note="third time")
        qa.claim(key)
        qa.approve(key)
        self.assertEqual(kinds(by_key(ui.check())[key]), ["approved"])

    def _topic(self) -> str:
        self.board.add_topic("api")
        return "api"

    def test_unassigned_takeover_and_status(self):
        b, ui = self.board, self.ui
        held = self.issue("held")["key"]
        ui.claim(held)
        moved = self.issue("moved")["key"]
        b.assign(moved, "claude-ui")
        gone = self.issue("gone")["key"]
        b.assign(gone, "claude-ui")
        other = self.agent("w2")
        ui.check()
        self.clock.advance(minutes=121)  # the lease runs out
        other.claim(held)
        b.move(moved, "blocked")
        self.step()
        b.assign(gone, "w2")
        feed = ui.check(peek=True)
        items = by_key(feed)
        self.assertEqual(items[held]["changes"], [{"kind": "takeover", "by": "w2"}])
        self.assertTrue(items[held]["action"])
        self.assertEqual(items[moved]["changes"],
                         [{"kind": "status", "old": "todo", "new": "blocked", "by": "owner"}])
        self.assertEqual(items[gone]["changes"], [{"kind": "unassigned", "by": "owner"}])
        self.assertFalse(items[gone]["action"])
        self.assertPending(feed)

    def test_order_limit_more_and_cursor(self):
        b, ui = self.board, self.ui
        keys = []
        for n, prio in enumerate(["low", "urgent", "medium", "high", "none", "low",
                                  "medium", "high", "urgent", "low", "medium", "none"]):
            key = self.issue(f"i{n}", priority=prio)["key"]
            b.assign(key, "claude-ui")
            keys.append(key)
        ui.check()
        self.step()
        for key in keys:
            b.update_issue(key, body="edited")
        b.add_comment(keys[-1], "question @claude-ui")  # needs action: first despite priority none
        feed = ui.check(peek=True)
        self.assertEqual((len(feed["changed"]), feed["more"]), (10, 2))
        self.assertEqual(feed["changed"][0]["key"], keys[-1])
        rest = [(c["effective_priority"], int(c["key"].split("-")[1])) for c in feed["changed"][1:]]
        order = ["urgent", "high", "medium", "low", "none"]
        self.assertEqual(rest, sorted(rest, key=lambda p: (order.index(p[0]), p[1])))
        self.assertPending(feed)
        heads = b.heads()
        cursor = ui.conn.execute("SELECT last_event_id, last_comment_id FROM cursors "
                                 "WHERE agent = 'claude-ui' AND scope = 'check'").fetchone()
        self.assertNotEqual(tuple(cursor), (heads["event_id"], heads["comment_id"]))  # peek kept it
        self.assertEqual(feed["cursor"], {"event_id": cursor[0], "comment_id": cursor[1]})
        self.clock.advance(minutes=3)
        done = ui.check()
        self.assertEqual(done["cursor"], heads)
        again = ui.check()
        self.assertEqual((again["changed"], again["since"]), ([], "2026-11-03T09:03:01Z"))
        self.assertEqual(ui.pending_changes(), 0)

    def test_holding_and_todo_for_you(self):
        ui = self.ui
        first = self.issue("first")["key"]
        self.issue("second")
        ui.claim(first)
        self.clock.advance(minutes=100)
        feed = ui.check()
        self.assertEqual(feed["holding"], [{"key": first, "lease_until": "2026-11-03T11:00:00Z",
                                            "lease_left_min": 20}])
        self.assertEqual(feed["todo_for_you"], 1)
        self.clock.advance(minutes=30)
        self.assertEqual(ui.check()["holding"][0]["lease_left_min"], 0)
        self.assertEqual(self.qa.check()["todo_for_you"], 0)  # reviewers count the review queue
        ui.submit_review(first, note="ok")
        self.assertEqual(self.qa.check()["todo_for_you"], 1)

    def test_reads_do_not_write_and_identity(self):
        ui = self.ui
        key = self.issue("x")["key"]
        self.board.assign(key, "claude-ui")
        probe = db.connect(self.board.path)
        self.addCleanup(probe.close)
        version = probe.execute("PRAGMA data_version").fetchone()[0]
        self.assertEqual(ui.pending_changes(), 1)
        ui.check(peek=True)
        self.assertEqual(probe.execute("PRAGMA data_version").fetchone()[0], version)
        ui.check()
        self.assertNotEqual(probe.execute("PRAGMA data_version").fetchone()[0], version)
        version = probe.execute("PRAGMA data_version").fetchone()[0]
        ui.check()  # nothing moved: no write either
        self.assertEqual(probe.execute("PRAGMA data_version").fetchone()[0], version)
        self.assertEqual(self.open_as(None).pending_changes(), 0)
        self.assertEqual(self.open_as("ghost").pending_changes(), 0)
        with self.assertRaises(IdentityRequired):
            self.open_as(None).check()
        with self.assertRaises(NotJoined):
            self.open_as("ghost").check()

    def test_comments_read_through_next_are_not_reported_again(self):
        """`next` prints the unread comments (advancing issue:<id>); check keeps the events."""
        b, ui, qa = self.board, self.ui, self.qa
        key = self.issue("feature")["key"]
        ui.claim(key)
        ui.submit_review(key, note="ready")
        ui.check()
        qa.claim(key)
        qa.reject(key, note="fails on reload")
        self.assertEqual(ui.pending_changes(), 1)
        self.assertEqual(ui.next()["key"], key)  # assigned to it after the reject
        read = ui.list_comments(key, new=True)["items"]  # what the CLI's `next` prints
        self.assertEqual([c["body"] for c in read], ["fails on reload"])
        feed = ui.check(peek=True)
        item = by_key(feed)[key]
        self.assertEqual((item["changes"], item["comments"], item["questions"], item["action"]),
                         ([{"kind": "rejected", "by": "codex-qa"}], 0, 0, True))
        self.assertPending(feed)
        ui.check()
        self.assertEqual(ui.pending_changes(), 0)
        self.step()
        b.add_comment(key, "also check the hover state")  # after the read: news again
        self.assertEqual(by_key(ui.check(peek=True))[key]["comments"], 1)
        self.assertEqual(ui.pending_changes(), 1)

    def test_comments_read_through_show_new_are_not_reported_again(self):
        b, ui, qa = self.board, self.ui, self.qa
        mine = self.issue("mine")["key"]
        ui.claim(mine)
        elsewhere = self.issue("not mine")["key"]
        ui.check()
        b.add_comment(mine, "use the blue palette")
        qa.add_comment(elsewhere, "@claude-ui which palette?", kind="question")
        self.assertEqual(ui.pending_changes(), 2)
        self.assertEqual(len(ui.list_comments(mine, new=True)["items"]), 1)  # show --new
        self.assertEqual(set(by_key(ui.check(peek=True))), {elsewhere})
        self.assertEqual(ui.pending_changes(), 1)
        ui.list_comments(elsewhere, new=True)  # a mention read on its issue is read too
        self.assertEqual(ui.check(peek=True)["changed"], [])
        self.assertEqual(ui.pending_changes(), 0)
        b.update_issue(mine, priority="high")  # events still count after the read
        feed = ui.check()
        self.assertEqual((kinds(by_key(feed)[mine]), by_key(feed)[mine]["comments"]), (["field"], 0))

    def test_an_agent_without_a_cursor_starts_now(self):
        legacy = self.agent("legacy")
        with self.board.conn:
            self.board.conn.execute("DELETE FROM cursors WHERE agent = 'legacy'")
        key = self.issue("x")["key"]
        self.board.assign(key, "legacy")
        self.assertEqual(legacy.pending_changes(), 0)
        self.assertEqual(legacy.check(peek=True)["changed"], [])
        legacy.check()
        self.board.update_issue(key, title="y")
        self.assertEqual(kinds(by_key(legacy.check())[key]), ["field"])


if __name__ == "__main__":
    unittest.main()
