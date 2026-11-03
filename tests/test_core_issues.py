"""Issue reads and field writes: filters, shapes, versions, conflicts, labels, rank, links."""

from __future__ import annotations

import unittest

from tests.support import BoardCase

from earmark.errors import Conflict, Forbidden, IdentityRequired, InvalidRequest, NotFound

SUMMARY_KEYS = {"key", "id", "title", "status", "priority", "effective_priority", "type", "topic",
                "assignee", "reviewer", "reporter", "labels", "due", "parent", "children", "blocked",
                "blocks_open", "stale",
                "lease_until", "rank", "version", "comment_count", "last_comment_id",
                "attachment_count", "created_at", "updated_at"}
DETAIL_KEYS = SUMMARY_KEYS | {"body", "started_at", "review_requested_at", "resolved_at", "links",
                              "new_comments", "attachments", "link_status"}


def keys(result) -> list[str]:
    return [i["key"] for i in result["items"]]


class Create(BoardCase):
    def test_defaults_and_shape(self):
        issue = self.issue("First")
        self.assertEqual(set(issue), DETAIL_KEYS)
        self.assertEqual((issue["key"], issue["status"], issue["priority"], issue["type"]),
                         ("GAME-1", "todo", "medium", "task"))
        self.assertEqual((issue["reporter"], issue["version"], issue["rank"]), ("owner", 1, 1024.0))
        self.assertEqual(issue["links"], {"blocks": [], "blocked_by": [], "duplicates": [],
                                          "duplicated_by": [], "relates": []})
        self.assertEqual(issue["children"], [])
        self.assertEqual(issue["new_comments"], 0)
        self.assertEqual(self.issue("Second")["rank"], 2048.0)
        summary = self.board.list_issues()["items"][0]
        self.assertEqual(set(summary), SUMMARY_KEYS)

    def test_fields_are_parsed(self):
        self.board.add_topic("ui-ux/menu")
        parent = self.issue("Parent")
        issue = self.issue("  Child   title ", body="Body", topic="ui-ux/menu", priority="p0",
                           type="bug", labels=["Regression", "ui"], due="tomorrow",
                           parent=parent["key"].lower(), assignee="me")
        self.assertEqual(issue["title"], "Child title")
        self.assertEqual((issue["priority"], issue["type"], issue["topic"]), ("urgent", "bug", "ui-ux/menu"))
        self.assertEqual((issue["labels"], issue["due"]), (["regression", "ui"], "2026-11-04"))
        self.assertEqual((issue["parent"], issue["assignee"]), ("GAME-1", "owner"))
        self.assertEqual(self.board.get_issue("GAME-1")["children"][0]["key"], issue["key"])

    def test_validation(self):
        self.board.add_topic("ui-ux/character-menu/eye-color")
        with self.assertRaises(NotFound) as ctx:
            self.issue("x", topic="eye-color")
        self.assertIn("ui-ux/character-menu/eye-color", ctx.exception.details["matches"])
        self.assertIn("did you mean", ctx.exception.hint)
        with self.assertRaises(NotFound):
            self.issue("x", topic="ui-ux/character-men")
        self.assertEqual(self.board.list_issues(status="all")["total"], 0)
        with self.assertRaises(NotFound):
            self.issue("x", assignee="ghost")
        with self.assertRaises(NotFound):
            self.issue("x", parent="GAME-99")
        for bad in ({"priority": "p9"}, {"type": "epic"}, {"due": "someday"}, {"labels": ["a b"]}):
            with self.assertRaises(InvalidRequest, msg=bad):
                self.issue("x", **bad)
        with self.assertRaises(InvalidRequest):
            self.issue("")
        with self.assertRaises(InvalidRequest):
            self.issue("x" * 201)

    def test_status_on_create(self):
        worker, pm = self.agent("w1"), self.agent("pm-1", "pm")
        self.assertEqual(worker.create_issue("idea", status="backlog")["status"], "backlog")
        self.assertEqual(worker.create_issue("plain")["status"], "todo")
        for creator in (worker, pm, self.board):
            for status in ("in_progress", "in_review", "blocked", "done", "canceled"):
                with self.subTest(creator=creator.actor, status=status):
                    with self.assertRaises(InvalidRequest):
                        creator.create_issue("x", status=status)
        self.assertEqual(self.board.list_issues(status="all")["total"], 2)

    def test_assign_on_create(self):  # r05
        w1, w2, pm = self.agent("w1"), self.agent("w2"), self.agent("pm-1", "pm")
        with self.assertRaises(Forbidden) as ctx:
            w1.create_issue("y", assignee="w2")
        self.assertIn("--assign me", ctx.exception.hint)
        self.assertEqual(self.board.list_issues()["total"], 0)
        mine = w1.create_issue("mine", assignee="me")
        self.assertEqual(w1.create_issue("mine too", assignee="w1")["assignee"], "w1")
        self.assertEqual(w1.create_issue("nobody", assignee="none")["assignee"], None)
        self.assertEqual(pm.create_issue("for w2", assignee="w2")["assignee"], "w2")
        self.assertEqual(self.issue("for w1", assignee="w1")["assignee"], "w1")
        self.assertEqual(mine["assignee"], "w1")
        log = [(e["action"], e["old"], e["new"]) for e in self.board.issue_events(mine["key"])]
        self.assertEqual(log, [("created", None, "mine"), ("assignee", None, "w1")])
        self.assertEqual(w2.get_issue(mine["key"])["assignee"], "w1")

    def test_numbers_out_of_range_are_invalid_requests(self):  # never OverflowError
        b = self.board
        key = self.issue("x")["key"]
        big = 99999999999999999999
        calls = {
            "show": lambda: b.get_issue(str(big)),
            "updated_since": lambda: b.list_issues(updated_since="99999999999w"),
            "due": lambda: b.create_issue("y", due="+99999999d"),
            "limit": lambda: b.list_issues(limit=big),
            "offset": lambda: b.list_issues(offset=big),
            "since": lambda: b.list_comments(key, since=big),
            "activity": lambda: b.activity(since=big),
            "reply_to": lambda: b.add_comment(key, "x", reply_to=big),
            "edit_comment": lambda: b.edit_comment(big, "x"),
            "topic_id": lambda: b.edit_topic(str(big), name="x"),
            "triage": lambda: b.triage(limit=big),
            "parent": lambda: b.update_issue(key, parent=str(big)),
        }
        for name, call in calls.items():
            with self.subTest(name), self.assertRaises(InvalidRequest):
                call()
        b.add_topic("a")
        with self.assertRaises(InvalidRequest):
            b.edit_topic("a", position=big)
        self.assertEqual(b.topics()[0]["position"], 0)  # rolled back
        with self.assertRaises(Conflict) as ctx:
            b.update_issue(key, if_version=big, title="z")
        self.assertEqual((ctx.exception.details["current_version"],
                          ctx.exception.details["events_since"]), (1, []))
        self.assertEqual(len(b.list_issues()["items"]), 1)

    def test_identity(self):
        anon = self.open_as(None)
        with self.assertRaises(IdentityRequired):
            anon.create_issue("x")
        self.assertEqual(anon.list_issues()["total"], 0)


class ListFilters(BoardCase):
    def setUp(self):
        super().setUp()
        b = self.board
        b.add_topic("ui-ux/menu/face")
        b.add_topic("backend")
        self.w1 = self.agent("w1")
        self.i1 = self.issue("Eye color resets", topic="ui-ux/menu", priority="high", type="bug",
                             labels=["regression", "ui"], due="2026-11-01", body="100% broken")
        self.i2 = self.issue("Face widget", topic="ui-ux/menu/face", priority="low", labels=["ui"])
        self.i3 = self.issue("API", topic="backend", priority="urgent", type="feature", due="2026-11-10")
        self.i4 = self.w1.create_issue("No topic", priority="none", type="chore")
        self.i5 = self.issue("Closed one", topic="backend")
        b.cancel(self.i5["key"], note="dup")
        b.assign(self.i2["key"], "w1")

    def test_status_default_open_and_groups(self):
        self.assertEqual(self.board.list_issues()["total"], 4)
        self.assertEqual(keys(self.board.list_issues(status="closed")), ["GAME-5"])
        self.assertEqual(self.board.list_issues(status="all")["total"], 5)
        self.assertEqual(self.board.list_issues(status=["todo", "canceled"])["total"], 5)

    def test_topic_subtree_and_exact(self):
        self.assertEqual(sorted(keys(self.board.list_issues(topic=["ui-ux"]))), ["GAME-1", "GAME-2"])
        self.assertEqual(keys(self.board.list_issues(topic=["ui-ux/menu"], exact=True)), ["GAME-1"])
        self.assertEqual(keys(self.board.list_issues(topic="ui-ux/menu/face")), ["GAME-2"])
        self.assertEqual(sorted(keys(self.board.list_issues(topic=["ui-ux/menu/face", "backend"]))),
                         ["GAME-2", "GAME-3"])
        with self.assertRaises(NotFound):
            self.board.list_issues(topic=["nope"])
        self.assertEqual(self.board.list_issues(topic=["", None])["total"], 4)

    def test_priority_type_label_text(self):
        b = self.board
        self.assertEqual(keys(b.list_issues(priority=["urgent,high"])), ["GAME-3", "GAME-1"])
        self.assertEqual(keys(b.list_issues(min_priority="high")), ["GAME-3", "GAME-1"])
        self.assertEqual(keys(b.list_issues(type=["bug", "chore"])), ["GAME-1", "GAME-4"])
        self.assertEqual(keys(b.list_issues(label=["ui"])), ["GAME-1", "GAME-2"])
        self.assertEqual(keys(b.list_issues(label=["ui", "regression"])), ["GAME-1"])
        self.assertEqual(keys(b.list_issues(q="EYE")), ["GAME-1"])
        self.assertEqual(keys(b.list_issues(q="100%")), ["GAME-1"])
        self.assertEqual(keys(b.list_issues(q="%")), ["GAME-1"])

    def test_people(self):
        b = self.board
        self.assertEqual(keys(b.list_issues(assignee="w1")), ["GAME-2"])
        self.assertEqual(keys(self.w1.list_issues(assignee="me")), ["GAME-2"])
        self.assertEqual(len(keys(b.list_issues(assignee="none"))), 3)
        self.assertEqual(keys(b.list_issues(reporter="w1")), ["GAME-4"])
        self.assertEqual(b.list_issues(reviewer="none")["total"], 4)
        with self.assertRaises(IdentityRequired):
            self.open_as(None).list_issues(assignee="me")

    def test_dates(self):
        b = self.board
        self.assertEqual(keys(b.list_issues(overdue=True)), ["GAME-1"])
        self.assertEqual(keys(b.list_issues(due_before="2026-11-10")), ["GAME-1"])
        self.assertEqual(keys(b.list_issues(due_before="2026-11-11", sort="due")), ["GAME-1", "GAME-3"])
        self.clock.advance(hours=3)
        b.update_issue("GAME-3", title="API v2")
        self.assertEqual(keys(b.list_issues(updated_since="1h")), ["GAME-3"])
        self.assertEqual(b.list_issues(created_since="1h")["total"], 0)
        self.assertEqual(b.list_issues(created_since="2026-11-03")["total"], 4)

    def test_ready_blocked_parent(self):
        b = self.board
        b.link("GAME-3", "blocks", "GAME-1")
        self.assertEqual(keys(b.list_issues(blocked=True)), ["GAME-1"])
        self.assertNotIn("GAME-1", keys(b.list_issues(ready=True)))
        self.assertTrue(b.get_issue("GAME-1")["blocked"])
        b.cancel("GAME-3", note="not needed")
        self.assertEqual(b.list_issues(blocked=True)["total"], 0)
        self.assertFalse(b.get_issue("GAME-1")["blocked"])
        b.update_issue("GAME-2", parent="GAME-1")
        self.assertEqual(keys(b.list_issues(parent="GAME-1")), ["GAME-2"])
        self.assertEqual(b.list_issues()["items"][0]["children"], 1)

    def test_sort_and_paging(self):
        b = self.board
        self.assertEqual(keys(b.list_issues()), ["GAME-3", "GAME-1", "GAME-2", "GAME-4"])
        self.assertEqual(keys(b.list_issues(sort="key", desc=True)), ["GAME-4", "GAME-3", "GAME-2", "GAME-1"])
        self.assertEqual(keys(b.list_issues(sort="rank")), ["GAME-1", "GAME-2", "GAME-3", "GAME-4"])
        self.assertEqual(keys(b.list_issues(sort="due")), ["GAME-1", "GAME-3", "GAME-2", "GAME-4"])
        self.assertEqual(keys(b.list_issues(sort="due", desc=True))[:2], ["GAME-3", "GAME-1"])
        page = b.list_issues(sort="key", limit=2, offset=2)
        self.assertEqual((keys(page), page["total"], page["limit"], page["offset"]),
                         (["GAME-3", "GAME-4"], 4, 2, 2))
        self.assertEqual(b.list_issues(limit=0)["items"], [])
        with self.assertRaises(InvalidRequest):
            b.list_issues(sort="title")


class Read(BoardCase):
    def test_get_issue_counts_and_comments(self):
        issue = self.issue("x")
        w1 = self.agent("w1")
        for n in range(4):
            self.board.add_comment(issue["key"], f"note {n}")
        w1.add_comment(issue["key"], "mine")
        shown = w1.get_issue("5" if False else issue["key"], comments=2)
        self.assertEqual((shown["comment_count"], shown["last_comment_id"]), (5, 5))
        self.assertEqual([c["body"] for c in shown["comments"]], ["note 3", "mine"])
        self.assertEqual(shown["new_comments"], 4)
        self.assertIsNone(self.open_as(None).get_issue(issue["key"])["new_comments"])
        self.assertNotIn("comments", self.board.get_issue(issue["key"]))
        with self.assertRaises(NotFound):
            self.board.get_issue("GAME-42")

    def test_list_extra_fields_match_show(self):
        """`ls --fields` takes show's scalars: the list asks for the ones summaries lack."""
        b = self.board
        w1 = self.agent("w1")
        one, two, three = (self.issue(t)["key"] for t in ("one", "two", "three"))
        b.link(one, "blocks", two)
        b.link(three, "relates", one)
        b.link(three, "duplicates", two)
        w1.claim(one)
        self.clock.advance(minutes=5)
        w1.submit_review(one, note="ready")
        b.move(one, "done")
        b.add_comment(two, "for w1")
        extra = ["started_at", "review_requested_at", "resolved_at", "new_comments", "links"]
        for actor in (w1, self.open_as(None)):
            items = actor.list_issues(status="all", sort="key", extra=extra)["items"]
            for item in items:
                shown = actor.get_issue(item["key"])
                self.assertEqual({k: item[k] for k in extra}, {k: shown[k] for k in extra}, item["key"])
        first = w1.list_issues(status="all", sort="key", extra=["resolved_at"])["items"][0]
        self.assertEqual((first["resolved_at"], "started_at" in first, "links" in first),
                         ("2026-11-03T09:05:00Z", False, False))
        self.assertNotIn("resolved_at", b.list_issues(status="all")["items"][0])
        self.assertEqual(b.list_issues(status="all", extra=["links"], limit=0)["items"], [])
        with self.assertRaises(InvalidRequest):
            b.list_issues(extra=["body"])

    def test_events_and_activity(self):
        issue = self.issue("x")
        self.board.update_issue(issue["key"], title="y", priority="high")
        events = self.board.issue_events(issue["key"])
        self.assertEqual([e["action"] for e in events], ["created", "title", "priority"])
        self.assertEqual(events[1], {"id": 2, "issue": "GAME-1", "actor": "owner", "action": "title",
                                     "old": "x", "new": "y", "at": "2026-11-03T09:00:00Z"})
        self.assertEqual((events[2]["old"], events[2]["new"]), ("medium", "high"))
        feed = self.board.activity(limit=2)
        self.assertEqual([e["id"] for e in feed["items"]], [3, 2])
        self.assertEqual(feed["last_id"], 3)
        self.assertEqual(self.board.activity(since=3), {"items": [], "last_id": 3})
        self.assertEqual([e["id"] for e in self.board.activity(since=1)["items"]], [3, 2])


class Update(BoardCase):
    def test_partial_edits_from_two_writers_both_land(self):
        issue = self.issue("x", priority="low")
        a, b = self.agent("w1"), self.agent("w2")
        a.update_issue(issue["key"], title="new title")
        b.update_issue(issue["key"], priority="high")
        final = self.board.get_issue(issue["key"])
        self.assertEqual((final["title"], final["priority"], final["version"]), ("new title", "high", 3))

    def test_version_bumps_once_per_write_and_noop_does_not_write(self):
        issue = self.issue("x")
        edited = self.board.update_issue(issue["key"], title="y", priority="high", labels=["a"])
        self.assertEqual(edited["version"], 2)
        watcher = self.open_as(None)
        before = (watcher.data_version(), watcher.heads())
        same = self.board.update_issue(issue["key"], title="y", priority="high", labels=["a"])
        self.assertEqual(same["version"], 2)
        self.assertEqual((watcher.data_version(), watcher.heads()), before)

    def test_if_version_conflict_details(self):
        issue = self.issue("x")
        self.clock.advance(seconds=1)
        self.board.update_issue(issue["key"], title="y")
        self.clock.advance(seconds=1)
        self.board.update_issue(issue["key"], priority="high", type="bug")
        with self.assertRaises(Conflict) as ctx:
            self.board.update_issue(issue["key"], if_version=1, body="mine")
        details = ctx.exception.details
        self.assertEqual(details["current_version"], 3)
        self.assertEqual(details["version"], 3)
        self.assertEqual(details["key"], "GAME-1")
        self.assertEqual([e["action"] for e in details["events_since"]], ["title", "priority", "type"])
        self.assertEqual(self.board.get_issue(issue["key"])["body"], "")
        self.assertEqual(self.board.update_issue(issue["key"], if_version=3, body="ok")["version"], 4)

    def test_body_append_labels_and_clearing(self):
        self.board.add_topic("a")
        issue = self.issue("x", topic="a", due="2026-11-09", labels=["one", "two"])
        b = self.board
        self.assertEqual(b.update_issue(issue["key"], append="more")["body"], "more")
        self.assertEqual(b.update_issue(issue["key"], append="again")["body"], "more\n\nagain")
        with self.assertRaises(InvalidRequest):
            b.update_issue(issue["key"], body="x", append="y")
        self.assertEqual(b.update_issue(issue["key"], add_labels=["three"], remove_labels=["one"])["labels"],
                         ["three", "two"])
        self.assertEqual(b.update_issue(issue["key"], labels=["solo"])["labels"], ["solo"])
        cleared = b.update_issue(issue["key"], topic=None, due=None)
        self.assertEqual((cleared["topic"], cleared["due"]), (None, None))
        actions = [e["action"] for e in b.issue_events(issue["key"])]
        self.assertEqual(actions.count("labels"), 2)
        with self.assertRaises(InvalidRequest):
            b.update_issue(issue["key"], status="done")
        with self.assertRaises(InvalidRequest):
            b.update_issue(issue["key"], title="")

    def test_parent_rules(self):
        a, b, c = self.issue("a"), self.issue("b"), self.issue("c")
        self.board.update_issue(b["key"], parent=a["key"])
        self.board.update_issue(c["key"], parent=b["key"])
        with self.assertRaises(InvalidRequest):
            self.board.update_issue(a["key"], parent=c["key"])
        with self.assertRaises(InvalidRequest):
            self.board.update_issue(a["key"], parent=a["key"])
        self.assertIsNone(self.board.update_issue(c["key"], parent=None)["parent"])

    def test_assign(self):
        issue = self.issue("x")
        w1 = self.agent("w1")
        pm = self.agent("pm-1", "pm")
        with self.assertRaises(Forbidden):
            w1.assign(issue["key"], "w1")
        self.assertEqual(pm.assign(issue["key"], "w1")["assignee"], "w1")
        self.assertIsNone(self.board.assign(issue["key"], "none")["assignee"])
        with self.assertRaises(NotFound):
            self.board.assign(issue["key"], "ghost")
        w1.claim(issue["key"])
        self.clock.advance(minutes=30)
        moved = self.board.assign(issue["key"], "pm-1")
        self.assertEqual((moved["assignee"], moved["lease_until"]), ("pm-1", "2026-11-03T11:30:00Z"))
        unassigned = self.board.assign(issue["key"], None)
        self.assertEqual((unassigned["status"], unassigned["lease_until"]), ("todo", None))
        self.assertIn("assignee", [e["action"] for e in self.board.issue_events(issue["key"])])


class Rank(BoardCase):
    def ranks(self):
        return keys(self.board.list_issues(sort="rank"))

    def test_moves(self):
        for n in range(4):
            self.issue(f"i{n}")
        b = self.board
        self.assertEqual(b.rank("GAME-4", top=True)["rank"], 0.0)
        self.assertEqual(self.ranks(), ["GAME-4", "GAME-1", "GAME-2", "GAME-3"])
        self.assertEqual(b.rank("GAME-4", bottom=True)["rank"], 4096.0)
        self.assertEqual(b.rank("GAME-1", after="GAME-2")["rank"], 2560.0)
        self.assertEqual(self.ranks(), ["GAME-2", "GAME-1", "GAME-3", "GAME-4"])
        self.assertEqual(b.rank("GAME-4", before="GAME-2")["rank"], 1024.0)
        self.assertEqual(self.ranks(), ["GAME-4", "GAME-2", "GAME-1", "GAME-3"])
        b.rank("GAME-3", before="GAME-1")
        self.assertEqual(self.ranks(), ["GAME-4", "GAME-2", "GAME-3", "GAME-1"])
        with self.assertRaises(InvalidRequest):
            b.rank("GAME-1", top=True, bottom=True)
        with self.assertRaises(InvalidRequest):
            b.rank("GAME-1", before="GAME-1")
        self.assertIn("rank", [e["action"] for e in b.issue_events("GAME-1")])

    def test_renumber_when_the_gap_is_too_small(self):
        for n in range(3):
            self.issue(f"i{n}")
        with self.board.conn:
            self.board.conn.execute("UPDATE issues SET rank = 5.0 WHERE id = 1")
            self.board.conn.execute("UPDATE issues SET rank = 5.0000000001 WHERE id = 2")
        moved = self.board.rank("GAME-3", after="GAME-1")
        self.assertEqual(self.ranks(), ["GAME-1", "GAME-3", "GAME-2"])
        ranks = {i["key"]: i["rank"] for i in self.board.list_issues(sort="rank")["items"]}
        self.assertEqual(ranks, {"GAME-1": 1024.0, "GAME-3": 1536.0, "GAME-2": 2048.0})
        self.assertEqual(moved["rank"], 1536.0)


class Links(BoardCase):
    def test_kinds_both_directions_and_events(self):
        a, b, c = self.issue("a"), self.issue("b"), self.issue("c")
        board = self.board
        board.link(a["key"], "blocks", b["key"])
        board.link(a["key"], "duplicates", c["key"])
        board.link(c["key"], "relates", b["key"])
        self.assertEqual(board.get_issue("GAME-1")["links"],
                         {"blocks": ["GAME-2"], "blocked_by": [], "duplicates": ["GAME-3"],
                          "duplicated_by": [], "relates": []})
        self.assertEqual(board.get_issue("GAME-2")["links"],
                         {"blocks": [], "blocked_by": ["GAME-1"], "duplicates": [],
                          "duplicated_by": [], "relates": ["GAME-3"]})
        self.assertEqual(board.get_issue("GAME-3")["links"]["duplicated_by"], ["GAME-1"])
        again = board.link(b["key"], "relates", c["key"])
        self.assertEqual(again["links"]["relates"], ["GAME-3"])
        count = board.conn.execute("SELECT count(*) FROM links WHERE kind = 'relates'").fetchone()[0]
        self.assertEqual(count, 1)
        events = board.issue_events("GAME-2")
        self.assertIn(("link", "blocked_by GAME-1"), [(e["action"], e["new"]) for e in events])
        board.unlink(b["key"], "relates", c["key"])
        self.assertEqual(board.get_issue("GAME-3")["links"]["relates"], [])
        with self.assertRaises(NotFound):
            board.unlink(b["key"], "relates", c["key"])

    def test_cycles_and_self(self):
        a, b, c = self.issue("a"), self.issue("b"), self.issue("c")
        self.board.link(a["key"], "blocks", b["key"])
        self.board.link(b["key"], "blocks", c["key"])
        with self.assertRaises(InvalidRequest):
            self.board.link(c["key"], "blocks", a["key"])
        with self.assertRaises(InvalidRequest):
            self.board.link(a["key"], "relates", a["key"])
        with self.assertRaises(InvalidRequest):
            self.board.link(a["key"], "parent-of", b["key"])


class Delete(BoardCase):
    def test_owner_only_and_cascade(self):
        issue = self.issue("x", labels=["a"])
        other = self.issue("y")
        self.board.link(issue["key"], "blocks", other["key"])
        self.board.add_comment(issue["key"], "c")
        w1 = self.agent("w1")
        with self.assertRaises(Forbidden):
            w1.delete_issue(issue["key"])
        self.assertEqual(self.board.delete_issue(issue["key"]), {"deleted": "GAME-1"})
        with self.assertRaises(NotFound):
            self.board.get_issue("GAME-1")
        conn = self.board.conn
        for table in ("labels", "links", "comments"):
            self.assertEqual(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0, table)
        self.assertEqual(self.board.activity(limit=1)["items"][0]["action"], "delete")
        self.assertEqual(self.issue("z")["key"], "GAME-3")


if __name__ == "__main__":
    unittest.main()
