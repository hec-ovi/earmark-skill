"""Comments, mentions, edits, `comments --since/--new/--last`, cursors and the inbox."""

from __future__ import annotations

import unittest

from tests.support import BoardCase

from earmark.errors import Forbidden, IdentityRequired, InvalidRequest, NotFound

COMMENT_KEYS = {"id", "issue", "author", "kind", "body", "reply_to", "mentions", "created_at",
                "edited_at"}


def bodies(result) -> list[str]:
    return [c["body"] for c in result["items"]]


class Writing(BoardCase):
    def test_shape_kinds_and_ids(self):
        a, b = self.issue("a")["key"], self.issue("b")["key"]
        first = self.board.add_comment(a, "hello")
        self.assertEqual(set(first), COMMENT_KEYS)
        self.assertEqual((first["id"], first["issue"], first["author"], first["kind"]),
                         (1, "GAME-1", "owner", "note"))
        ids = [self.board.add_comment(k, "x", kind=kind)["id"]
               for k, kind in ((b, "progress"), (a, "question"), (b, "note"))]
        self.assertEqual(ids, [2, 3, 4])
        for kind in ("handoff", "approval", "changes", "shout"):
            with self.assertRaises(InvalidRequest, msg=kind):
                self.board.add_comment(a, "x", kind=kind)
        with self.assertRaises(InvalidRequest):
            self.board.add_comment(a, "   ")
        with self.assertRaises(NotFound):
            self.board.add_comment("GAME-9", "x")

    def test_reply_to(self):
        a, b = self.issue("a")["key"], self.issue("b")["key"]
        root = self.board.add_comment(a, "root")
        self.assertEqual(self.board.add_comment(a, "re", reply_to=root["id"])["reply_to"], root["id"])
        self.assertEqual(self.board.add_comment(a, "re", reply_to=f"#{root['id']}")["reply_to"], root["id"])
        with self.assertRaises(InvalidRequest):
            self.board.add_comment(b, "wrong issue", reply_to=root["id"])
        with self.assertRaises(NotFound):
            self.board.add_comment(a, "missing", reply_to=999)

    def test_mentions_only_existing_handles(self):
        self.agent("claude-ui")
        self.agent("codex-qa", "reviewer")
        key = self.issue("a")["key"]
        comment = self.board.add_comment(key, "@codex-qa and @claude-ui, also @ghost, mail x@codex-qa "
                                              "again @claude-ui")
        self.assertEqual(comment["mentions"], ["codex-qa", "claude-ui"])
        edited = self.board.edit_comment(comment["id"], "only @codex-qa now")
        self.assertEqual(edited["mentions"], ["codex-qa"])

    def test_updated_at_moves_version_does_not(self):
        key = self.issue("a")["key"]
        self.clock.advance(minutes=5)
        self.board.add_comment(key, "later")
        issue = self.board.get_issue(key)
        self.assertEqual((issue["updated_at"], issue["version"]), ("2026-11-03T09:05:00Z", 1))

    def test_edit_own_comments_only(self):
        w1 = self.agent("w1")
        key = self.issue("a")["key"]
        mine = w1.add_comment(key, "typo")
        self.clock.advance(minutes=1)
        edited = w1.edit_comment(mine["id"], "fixed")
        self.assertEqual((edited["id"], edited["body"], edited["edited_at"]),
                         (mine["id"], "fixed", "2026-11-03T09:01:00Z"))
        with self.assertRaises(Forbidden):
            self.board.edit_comment(mine["id"], "owner rewrite")
        with self.assertRaises(NotFound):
            w1.edit_comment(42, "x")


class Reading(BoardCase):
    def setUp(self):
        super().setUp()
        self.w1 = self.agent("w1")
        self.key = self.issue("a")["key"]
        self.other = self.issue("b")["key"]
        for n in range(5):
            self.board.add_comment(self.key, f"c{n}")
            self.board.add_comment(self.other, f"other{n}")

    def test_since_last_limit(self):
        b = self.board
        everything = b.list_comments(self.key)
        self.assertEqual(bodies(everything), ["c0", "c1", "c2", "c3", "c4"])
        self.assertEqual((everything["last_id"], everything["has_more"]), (9, False))
        after = b.list_comments(self.key, since=3)
        self.assertEqual(bodies(after), ["c2", "c3", "c4"])
        page = b.list_comments(self.key, limit=2)
        self.assertEqual((bodies(page), page["last_id"], page["has_more"]), (["c0", "c1"], 3, True))
        self.assertEqual(bodies(b.list_comments(self.key, last=2)), ["c3", "c4"])
        self.assertEqual(b.list_comments(self.key, since=9), {"items": [], "last_id": 9, "has_more": False})
        with self.assertRaises(InvalidRequest):
            b.list_comments(self.key, since=1, new=True)

    def test_new_advances_the_issue_cursor_and_skips_own(self):
        w1 = self.w1
        first = w1.list_comments(self.key, new=True, limit=3)
        self.assertEqual((bodies(first), first["has_more"], first["last_id"]), (["c0", "c1", "c2"], True, 5))
        second = w1.list_comments(self.key, new=True)
        self.assertEqual((bodies(second), second["last_id"]), (["c3", "c4"], 9))
        self.assertEqual(w1.list_comments(self.key, new=True),
                         {"items": [], "last_id": 9, "has_more": False})
        w1.add_comment(self.key, "my own")
        self.board.add_comment(self.key, "reply")
        w1.add_comment(self.key, "my own again")
        third = w1.list_comments(self.key, new=True)
        self.assertEqual((bodies(third), third["last_id"]), (["reply"], 13))
        self.assertEqual(w1.list_comments(self.key, new=True)["items"], [])
        self.assertEqual(w1.get_issue(self.key)["new_comments"], 0)
        with self.assertRaises(IdentityRequired):
            self.open_as(None).list_comments(self.key, new=True)

    def test_reads_never_write(self):
        watcher = self.open_as(None)
        version = watcher.data_version()
        self.w1.get_issue(self.key, comments=3)
        self.w1.list_comments(self.key, since=2)
        self.w1.list_issues(status="all")
        self.w1.inbox(peek=True)
        self.w1.topics()
        self.w1.team()
        self.w1.whoami()
        self.w1.triage()
        self.w1.wait_check("any")
        self.w1.next(dry_run=True)
        self.assertEqual(watcher.data_version(), version)


class Inbox(BoardCase):
    def setUp(self):
        super().setUp()
        self.me = self.agent("me-1")
        self.pal = self.agent("pal")
        b = self.board
        self.reported = self.me.create_issue("I reported this")["key"]
        self.assigned = self.issue("assigned to me")["key"]
        b.assign(self.assigned, "me-1")
        self.reviewed = self.issue("I review this")["key"]
        self.pal.claim(self.reviewed)
        self.pal.submit_review(self.reviewed, note="please review")
        self.me.claim(self.reviewed)
        self.talked = self.issue("I commented here")["key"]
        self.me.add_comment(self.talked, "my thoughts")
        self.mentioned = self.issue("mentions me")["key"]
        self.unrelated = self.issue("not mine")["key"]

    def test_membership(self):
        b = self.board
        for key in (self.reported, self.assigned, self.reviewed, self.talked, self.unrelated):
            b.add_comment(key, f"on {key}")
        b.add_comment(self.mentioned, "hey @me-1 look")
        b.add_comment(self.mentioned, "follow-up without mention")
        self.me.add_comment(self.assigned, "my own comment")
        inbox = self.me.inbox(peek=True)
        expected = [f"on {k}" for k in (self.reported, self.assigned, self.reviewed, self.talked)]
        self.assertEqual(bodies(inbox)[0], "please review")
        self.assertEqual(bodies(inbox)[1:], expected + ["hey @me-1 look", "follow-up without mention"])
        self.assertNotIn(self.unrelated, {c["issue"] for c in inbox["items"]})
        self.assertEqual(inbox["cursor"], 0)

    def test_cursor_peek_and_limit(self):
        for n in range(4):
            self.board.add_comment(self.assigned, f"n{n}")
        peeked = self.me.inbox(peek=True, limit=2)
        self.assertEqual((len(peeked["items"]), peeked["has_more"], peeked["cursor"]), (2, True, 0))
        first = self.me.inbox(limit=2)
        self.assertEqual((first["has_more"], first["cursor"], first["last_id"]),
                         (True, first["items"][-1]["id"], first["items"][-1]["id"]))
        rest = self.me.inbox()
        self.assertEqual((bodies(rest), rest["has_more"]), (["n1", "n2", "n3"], False))
        empty = self.me.inbox()
        self.assertEqual((empty["items"], empty["last_id"], empty["cursor"]),
                         ([], rest["last_id"], rest["last_id"]))

    def test_skips_comments_read_through_the_issue_cursor(self):
        self.me.inbox()
        self.board.add_comment(self.assigned, "read in the thread")
        self.board.add_comment(self.talked, "unread elsewhere")
        self.me.list_comments(self.assigned, new=True)
        self.assertEqual(bodies(self.me.inbox(peek=True)), ["unread elsewhere"])
        self.board.add_comment(self.assigned, "newer in the thread")
        self.assertEqual(bodies(self.me.inbox()), ["unread elsewhere", "newer in the thread"])
        self.assertEqual(self.me.join("worker")["inbox_new"], 0)

    def test_requires_identity(self):
        with self.assertRaises(IdentityRequired):
            self.open_as(None).inbox()


if __name__ == "__main__":
    unittest.main()
