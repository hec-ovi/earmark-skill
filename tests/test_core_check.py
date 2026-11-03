"""check: the per-agent change feed (kinds, counts, order, cursor) and pending_changes, the
read-only count behind the dirty hint."""

from __future__ import annotations

import unittest

from tests.support import BoardCase

from earmark import db
from earmark.errors import IdentityRequired, NotJoined

CHECK_KEYS = {"agent", "role", "since", "holding", "ready_for_you", "waiting_on_blockers",
              "reviews_waiting", "todo_for_you", "changed", "more", "cursor"}
CHANGED_KEYS = {"key", "priority", "effective_priority", "blocks_open", "action", "changes",
                "comments", "questions", "mentions", "files"}


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
        self.assertEqual((feed["role"], feed["ready_for_you"], feed["todo_for_you"]), ("worker", 1, 1))
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
        self.assertEqual((w["comments"], w["questions"], w["mentions"], w["files"], w["action"]),
                         (2, 1, 0, 1, True))
        a = items[assets]
        self.assertEqual((a["priority"], a["effective_priority"], a["blocks_open"]), ("low", "urgent", 1))
        self.assertIn({"kind": "assigned", "by": "grok-pm"}, a["changes"])
        self.assertTrue(a["action"])
        e = items[elsewhere]
        self.assertEqual((e["changes"], e["comments"], e["questions"], e["mentions"], e["action"]),
                         ([], 1, 0, 1, True))  # a note that @mentions it: not a question
        self.assertPending(feed)

    def test_links_say_which_link_changed(self):
        """Live-test repro: `SHOP-1  links changed` left backend-dev guessing what changed."""
        b, ui, pm = self.board, self.ui, self.pm
        mine = self.issue("endpoint")["key"]
        b.assign(mine, "claude-ui")
        five, eight, nine = (self.issue(t)["key"] for t in ("ui", "schema", "old endpoint"))
        b.link(mine, "relates", nine)
        ui.check()
        pm.link(mine, "blocks", five)
        pm.link(eight, "blocks", mine)
        pm.link(mine, "duplicates", nine)
        pm.unlink(mine, "relates", nine)
        pm.link(mine, "blocks", five)  # already there: no event, no repeat
        item = by_key(ui.check())[mine]
        self.assertEqual(item["changes"], [{"kind": "field", "field": "links", "by": "grok-pm", "links": [
            {"change": "added", "link": "blocks", "key": five},
            {"change": "added", "link": "blocked_by", "key": eight},
            {"change": "added", "link": "duplicates", "key": nine},
            {"change": "removed", "link": "relates", "key": nine}]}])

    def test_a_question_is_for_whom_it_names(self):
        """Live-test repros: a note @mentioning the agent printed `question for you`, and pm-1's
        question to @owner on the artist's SHOP-8 reached the artist as a question for it."""
        b, ui, pm, qa = self.board, self.ui, self.pm, self.qa
        mine = self.issue("hero art")["key"]
        b.assign(mine, "claude-ui")
        elsewhere = self.issue("not mine")["key"]
        ui.check()
        pm.add_comment(mine, "@owner no art-profile agent on the board yet?", kind="question")
        pm.add_comment(mine, "fyi @claude-ui the palette moved", kind="note")
        qa.add_comment(mine, "which size?", kind="question")  # names nobody: for its assignee
        qa.add_comment(elsewhere, "@claude-ui @owner thoughts?", kind="question")
        qa.add_comment(elsewhere, "cc @claude-ui", kind="progress")
        items = by_key(ui.check(peek=True))
        self.assertEqual({k: (v["comments"], v["questions"], v["mentions"]) for k, v in items.items()},
                         {mine: (3, 1, 1), elsewhere: (2, 1, 1)})
        only = self.issue("for the owner")["key"]
        b.assign(only, "claude-ui")
        ui.check()
        pm.add_comment(only, "@owner can we cut this?", kind="question")
        item = by_key(ui.check())[only]
        self.assertEqual((item["comments"], item["questions"], item["mentions"], item["action"]),
                         (1, 0, 0, False))

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

    def test_holding_and_counts(self):
        ui = self.ui
        first = self.issue("first")["key"]
        self.issue("second")
        ui.claim(first)
        self.clock.advance(minutes=100)
        feed = ui.check()
        self.assertEqual(feed["holding"], [{"key": first, "lease_until": "2026-11-03T11:00:00Z",
                                            "lease_left_min": 20}])
        self.assertEqual((feed["ready_for_you"], feed["todo_for_you"], feed["reviews_waiting"]),
                         (1, 1, 0))
        self.clock.advance(minutes=30)
        feed = ui.check()
        self.assertEqual(feed["holding"][0]["lease_left_min"], 0)
        self.assertEqual(feed["ready_for_you"], 2)  # its own lapsed claim is claimable again
        qa = self.qa.check()
        self.assertEqual((qa["role"], qa["reviews_waiting"]), ("reviewer", 0))
        ui.submit_review(first, note="ok")
        self.assertEqual(self.qa.check()["reviews_waiting"], 1)
        self.assertEqual(ui.check()["reviews_waiting"], 0)  # its own handoff is not for it

    def test_ready_counts_what_next_claims_and_blocked_work_waits(self):
        """Live-test repros: a reviewer with nothing assigned read the worker queue as its todo,
        and a writer's issue parked on an open blocker counted as ready though next skipped it."""
        b, ui = self.board, self.ui
        b.add_topic("copy")
        b.add_topic("art")
        b.add_profile("writer", name="Writer", role="worker", topics=["copy"])
        writer = self.agent("writer-1", "writer")
        first = self.issue("tagline", topic="copy")["key"]
        second = self.issue("blurb", topic="copy")["key"]
        b.assign(first, "writer-1")
        b.assign(second, "writer-1")
        art = self.issue("hero image", topic="art")["key"]
        b.link(art, "blocks", second)
        self.issue("someone else's", topic="copy", assignee="claude-ui")
        self.issue("api work")  # outside the writer's scope
        feed = writer.check()
        self.assertEqual((feed["ready_for_you"], feed["waiting_on_blockers"], feed["reviews_waiting"]),
                         (1, 1, 0))
        self.assertEqual(writer.next()["key"], first)
        self.assertEqual((writer.check()["ready_for_you"], writer.check()["waiting_on_blockers"]),
                         (0, 1))
        b.move(art, "done")
        self.assertEqual((writer.check()["ready_for_you"], writer.check()["waiting_on_blockers"]),
                         (1, 0))
        qa = self.qa.check()
        self.assertEqual((qa["reviews_waiting"], qa["waiting_on_blockers"]), (0, 0))

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

    def test_what_next_printed_is_not_reported_again(self):
        """`next` prints the issue and its unread comments (advancing issue:<id>), and its claim
        is newer than the reject: neither the reject nor its comment is news afterwards."""
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
        self.assertEqual(ui.check(peek=True)["changed"], [])
        self.assertEqual(ui.pending_changes(), 0)
        self.step()
        b.add_comment(key, "also check the hover state")  # after the read: news again
        b.update_issue(key, priority="high")  # and an event after its claim
        item = by_key(ui.check(peek=True))[key]
        self.assertEqual((kinds(item), item["comments"]), (["field"], 1))
        self.assertEqual(ui.pending_changes(), 1)

    def test_a_link_before_the_claim_is_not_reported(self):
        """Live-test repro: pm linked SHOP-8 before backend-dev's `next` claimed it (show printed
        the link); check and the hint still said `SHOP-8  links changed`."""
        b, ui, pm = self.board, self.ui, self.pm
        key = self.issue("endpoint", priority="high")["key"]
        other = self.issue("schema")["key"]
        self.step()
        pm.link(other, "blocks", key)
        pm.move(other, "done")
        self.assertEqual(ui.pending_changes(), 0)  # not its issue yet
        self.assertEqual(ui.next()["key"], key)
        self.assertEqual(ui.check(peek=True)["changed"], [])
        self.assertEqual(ui.pending_changes(), 0)
        self.step()
        pm.link(key, "relates", other)  # after the claim: news
        self.assertEqual(set(by_key(ui.check(peek=True))), {key})
        self.assertEqual(ui.pending_changes(), 1)

    def test_a_review_claim_hides_the_handoff(self):
        """Live-test repro: reviewer's `next --review` printed SHOP-4 in review, yet check still
        reported `SHOP-4  status in_progress→in_review`."""
        ui, qa = self.ui, self.qa
        key = self.issue("feature")["key"]
        qa.check()
        ui.claim(key)
        ui.submit_review(key, note="ready")
        self.assertEqual(qa.next(review=True)["key"], key)
        qa.list_comments(key, new=True)
        self.assertEqual(qa.check(peek=True)["changed"], [])
        self.assertEqual(qa.pending_changes(), 0)
        self.step()
        ui.update_issue(key, body="more detail")
        self.assertEqual(kinds(by_key(qa.check())[key]), ["field"])

    def test_an_own_comment_from_a_later_second_hides_older_events(self):
        b, ui = self.board, self.ui
        key = self.issue("x")["key"]
        b.assign(key, "claude-ui")
        ui.check()
        b.update_issue(key, title="renamed")
        ui.add_comment(key, "same second: ordering unknown, still reported")
        self.assertEqual(kinds(by_key(ui.check(peek=True))[key]), ["field"])
        self.step()
        ui.add_comment(key, "a second later: it had seen the rename")
        self.assertEqual(ui.check(peek=True)["changed"], [])
        self.assertEqual(ui.pending_changes(), 0)
        b.update_issue(key, priority="low")
        self.assertEqual(kinds(by_key(ui.check())[key]), ["field"])

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


class PmFeed(BoardCase):
    """A pm watches its scope: the live test's pm-1 read `nothing new` while SHOP-1 closed and
    SHOP-2..4 moved, and its `wait --for any` slept through the last two closes."""

    def setUp(self):
        super().setUp()
        b = self.board
        b.add_topic("api")
        b.add_topic("art")
        b.config("max_review_rounds", "1")
        self.w1 = self.agent("w1", cli="claude-code")
        self.w2 = self.agent("w2", cli="codex")
        self.qa = self.agent("qa", "reviewer", cli="codex")
        self.pm = self.agent("pm-1", "pm", cli="grok")
        self.keys = [self.issue(f"i{n}", topic="api")["key"] for n in range(6)]

    def test_the_whole_board_in_one_line_per_issue(self):
        b, w1, qa, pm = self.board, self.w1, self.qa, self.pm
        closed, handed, assigned, canceled, capped, lapsed = self.keys
        for key in (closed, capped):
            w1.claim(key)
            w1.submit_review(key, note="ready")
        pm.check()
        qa.claim(closed)
        qa.approve(closed)
        w1.claim(handed)
        w1.submit_review(handed, note="ready")
        b.assign(assigned, "w2")
        b.cancel(canceled, note="not needed")
        qa.claim(capped)
        qa.reject(capped, note="still broken")
        self.w2.claim(lapsed)
        new = self.w2.create_issue("found a bug", topic="art", priority="high")["key"]
        self.clock.advance(minutes=121)
        self.assertEqual(pm.pending_changes(), 7)
        feed = pm.check(peek=True)
        items = by_key(feed)
        self.assertEqual(set(items), {closed, handed, assigned, canceled, capped, lapsed, new})
        self.assertEqual(items[closed]["changes"], [{"kind": "approved", "by": "qa"}])
        self.assertEqual(items[handed]["changes"], [{"kind": "handoff", "by": "w1"}])  # one second
        self.assertEqual(items[assigned]["changes"],
                         [{"kind": "field", "field": "assignee", "new": "w2", "by": "owner"}])
        self.assertEqual(items[canceled]["changes"],
                         [{"kind": "status", "old": "todo", "new": "canceled", "by": "owner"}])
        self.assertEqual(kinds(items[capped]), ["rejected", "review_cap"])
        self.assertEqual(kinds(items[lapsed]), ["status", "stale"])
        self.assertEqual(items[lapsed]["changes"][-1], {"kind": "stale", "by": "w2"})
        self.assertEqual(items[new]["changes"], [{"kind": "created", "by": "w2"}])
        # only the exhausted review needs the pm; the rest is the board's flow
        self.assertEqual([c["key"] for c in feed["changed"] if c["action"]], [capped])
        self.assertEqual(feed["changed"][0]["key"], capped)
        self.assertEqual(pm.check()["changed"], feed["changed"])
        again = pm.check()  # nothing moved: the lapsed lease is not news twice
        self.assertEqual((again["changed"], pm.pending_changes()), ([], 0))
        self.assertEqual(again["since"], "2026-11-03T11:01:00Z")

    def test_workers_and_reviewers_keep_their_own_feed(self):
        b, w1, qa = self.board, self.w1, self.qa
        w1.check()
        qa.check()
        b.move(self.keys[0], "canceled")
        self.w2.claim(self.keys[1])
        self.w2.create_issue("new", topic="api")
        self.assertEqual((w1.pending_changes(), qa.pending_changes()), (0, 0))
        self.assertEqual((w1.check()["changed"], qa.check()["changed"]), ([], []))

    def test_a_scoped_pm_watches_its_subtrees_only(self):
        b = self.board
        b.add_profile("art-pm", name="Art PM", role="pm", topics=["art"])
        art_pm = self.agent("pm-art", "art-pm")
        self.pm.check()
        art = self.issue("sprite", topic="art")["key"]
        loose = self.issue("no topic")["key"]
        self.w1.claim(self.keys[0])
        self.w1.claim(art)
        self.assertEqual(set(by_key(art_pm.check(peek=True))), {art})
        self.assertEqual(art_pm.pending_changes(), 1)
        self.assertEqual(set(by_key(self.pm.check(peek=True))), {art, loose, self.keys[0]})
        self.assertEqual(self.pm.pending_changes(), 3)  # unscoped: the whole board

    def test_the_10_line_cap(self):
        self.pm.check()
        for n in range(12):
            self.w1.create_issue(f"new {n}", topic="api")
        feed = self.pm.check()
        self.assertEqual((len(feed["changed"]), feed["more"]), (10, 2))

    def test_wait_wakes_on_scope_changes_and_lapsing_leases(self):
        b, pm = self.board, self.pm
        pm.check()
        self.assertIsNone(pm.wait_check("changes"))
        self.assertIsNone(pm.wait_wake_in("changes"))  # no lease running
        b.move(self.keys[0], "done")
        self.assertEqual(pm.wait_check("changes"), {"reason": "changes", "count": 1})
        self.assertEqual(pm.wait_check("any")["reason"], "changes")
        pm.check()
        self.w1.claim(self.keys[1])
        pm.check()
        self.assertEqual(pm.wait_wake_in("changes"), 120 * 60 + 1)
        self.assertEqual(self.w2.wait_wake_in("work"), 120 * 60 + 1)  # next could take it over
        self.assertIsNone(self.w2.wait_wake_in("changes"))  # a worker's feed has no lapses
        self.assertIsNone(self.w1.wait_wake_in("work"))  # its own lease
        self.clock.advance(minutes=100)
        self.assertEqual(pm.wait_wake_in("any"), 20 * 60 + 1)
        self.clock.advance(minutes=20, seconds=1)
        self.assertEqual(pm.wait_check("changes"), {"reason": "changes", "count": 1})


if __name__ == "__main__":
    unittest.main()
