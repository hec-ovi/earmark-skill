"""The server over real boards: every route end to end, conflicts, SSE and read-only idling."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest

from tests.test_server_fixtures import ServerHarness, SSEReader, core_ready  # first: src/ on sys.path

CORE = core_ready()
if CORE:
    from tests.support import join_agent, make_board, open_as


def _files(db_path: str) -> dict:
    out = {}
    for suffix in ("", "-wal", "-shm"):
        path = db_path + suffix
        if os.path.exists(path):
            st = os.stat(path)
            out[suffix or "db"] = (st.st_size, st.st_mtime_ns)
    return out


@unittest.skipUnless(CORE, "earmark.board is not implemented yet")
class RealServerCase(unittest.TestCase):
    poll_interval = 0.2

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="earmark-server-real-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        board = make_board(self.tmp)
        self.db = board.path
        board.add_topic("ui-ux", name="UI & UX", icon="layout-dashboard")
        board.add_topic("ui-ux/hud", name="HUD")
        board.add_topic("engine", name="Engine", icon="cpu")
        board.close()
        self.worker = join_agent(self.db, "claude-ui", "worker", cli="claude-code")
        self.addCleanup(self.worker.close)
        self.h = ServerHarness(self.db, poll_interval=self.poll_interval)
        self.addCleanup(self.h.stop)

    def ok(self, method: str, path: str, body=None, status: int = 200):
        resp = self.h.request(method, path, body)
        self.assertEqual(resp.status, status, resp.body)
        env = resp.json
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["meta"]["board"], "GAME")
        self.assertEqual(env["meta"]["actor"], "owner")
        return env["data"]

    def refused(self, method: str, path: str, body=None, *, status: int, code: str) -> dict:
        resp = self.h.request(method, path, body)
        self.assertEqual(resp.status, status, resp.body)
        env = resp.json
        self.assertFalse(env["ok"])
        self.assertIsNone(env["data"])
        self.assertEqual(env["error"]["code"], code, env["error"])
        return env["error"]


class RealRouteTests(RealServerCase):
    def test_board(self):
        data = self.ok("GET", "/api/board")
        self.assertEqual(data["prefix"], "GAME")
        self.assertEqual(data["owner"], "owner")
        self.assertEqual(data["settings"], {"require_review": True, "lease_minutes": 120,
                                            "max_attachment_mb": 50, "max_review_rounds": 3})
        self.assertEqual(set(data["counts"]), {"by_status", "open", "total"})
        self.assertEqual(set(data["enums"]) >= {"statuses", "priorities", "types", "comment_kinds",
                                                 "roles", "link_kinds"}, True)
        self.assertIn("in_review", data["enums"]["statuses"])
        self.assertTrue(data["version"])

    def test_topics_crud(self):
        tree = self.ok("GET", "/api/topics")
        self.assertEqual([t["path"] for t in tree], ["ui-ux", "engine"])
        self.assertEqual(tree[0]["children"][0]["path"], "ui-ux/hud")
        made = self.ok("POST", "/api/topics", {"path": "engine/physics", "name": "Physics", "icon": "zap",
                                                "description": "Collision and cloth"})
        self.assertEqual((made["path"], made["name"], made["icon"]), ("engine/physics", "Physics", "zap"))
        tid = made["id"]
        edited = self.ok("PATCH", f"/api/topics/{tid}", {"name": "Physics & cloth", "position": 1})
        self.assertEqual(edited["name"], "Physics & cloth")
        moved = self.ok("PATCH", f"/api/topics/{tid}", {"path": "engine/sim"})
        self.assertEqual((moved["id"], moved["path"]), (tid, "engine/sim"))
        # path and fields together: a bad field leaves the move undone
        self.refused("PATCH", f"/api/topics/{tid}", {"path": "engine/sim2", "name": "   "},
                     status=400, code="invalid_request")
        self.assertIn("engine/sim", [c["path"] for c in self.ok("GET", "/api/topics")[1]["children"]])
        both = self.ok("PATCH", "/api/topics/engine%2Fsim", {"path": "engine/sim2", "name": "Sim"})
        self.assertEqual((both["id"], both["path"], both["name"]), (tid, "engine/sim2", "Sim"))
        self.ok("PATCH", f"/api/topics/{tid}", {"path": "engine/sim"})
        self.ok("PATCH", f"/api/topics/{tid}", {"archived": True})
        self.assertNotIn("engine/sim", [c["path"] for c in self.ok("GET", "/api/topics")[1]["children"]])
        self.assertIn("engine/sim", [c["path"] for c in self.ok("GET", "/api/topics?all=1")[1]["children"]])
        self.assertEqual(self.ok("DELETE", f"/api/topics/{tid}"), {"deleted": tid})
        by_path = self.ok("POST", "/api/topics", {"path": "engine/tmp"})["id"]
        self.assertEqual(self.ok("DELETE", "/api/topics/engine%2Ftmp"), {"deleted": by_path})
        self.refused("DELETE", f"/api/topics/{tid}", status=404, code="not_found")
        self.refused("POST", "/api/topics", {"path": "Bad Path!"}, status=400, code="invalid_request")

    def test_status_route_unblocks_the_waiting_issue(self):
        waiting = self.worker.create_issue("HUD ammo", topic="ui-ux/hud", priority="high")["key"]
        self.worker.claim(waiting)
        need = self.worker.need(waiting, "Ammo icon", topic="engine")
        blocker = need["created"]["key"]
        self.assertEqual(need["blocked"]["status"], "blocked")
        listed = {i["key"]: i for i in self.ok("GET", "/api/issues")["items"]}
        self.assertEqual((listed[blocker]["blocks_open"], listed[blocker]["effective_priority"]), (1, "high"))
        self.ok("POST", f"/api/issues/{blocker}/status", {"status": "done", "note": "made"})
        back = self.ok("GET", f"/api/issues/{waiting}")
        self.assertEqual((back["status"], back["assignee"], back["link_status"]),
                         ("todo", "claude-ui", {blocker: "done"}))
        events = self.ok("GET", f"/api/issues/{waiting}/events")
        self.assertEqual((events[-1]["action"], events[-1]["actor"]), ("unblocked", "owner"))

    def test_issue_lifecycle(self):
        made = self.ok("POST", "/api/issues", {
            "title": "Ammo counter overlaps the minimap", "body": "## Steps\n1. 21:9",
            "topic": "ui-ux/hud", "priority": "high", "type": "bug", "labels": ["ultrawide"],
            "due": "2026-12-01"})
        key = made["key"]
        self.assertEqual(key, "GAME-1")
        for field in ("body", "started_at", "review_requested_at", "resolved_at", "links", "children"):
            self.assertIn(field, made)
        self.assertEqual(made["reporter"], "owner")
        other = self.ok("POST", "/api/issues", {"title": "Minimap icons", "topic": "ui-ux"})["key"]

        page = self.ok("GET", "/api/issues?topic=ui-ux&label=ultrawide")
        self.assertEqual([i["key"] for i in page["items"]], [key])
        self.assertEqual(page["total"], 1)
        page = self.ok("GET", "/api/issues?topic=ui-ux&exact=1")
        self.assertEqual([i["key"] for i in page["items"]], [other])
        page = self.ok("GET", "/api/issues?priority=high,urgent&limit=1")
        self.assertEqual((page["total"], page["limit"]), (1, 1))
        self.assertNotIn("body", page["items"][0])

        detail = self.ok("GET", f"/api/issues/{key.lower()}")
        self.assertEqual(detail["key"], key)

        v = detail["version"]
        patched = self.ok("PATCH", f"/api/issues/{key}", {"version": v, "title": "Ammo counter overlap",
                                                           "due": None})
        self.assertEqual((patched["title"], patched["due"]), ("Ammo counter overlap", None))
        self.assertEqual(patched["version"], v + 1)
        err = self.refused("PATCH", f"/api/issues/{key}", {"version": v, "title": "Stale"}, status=409,
                        code="conflict")
        self.assertEqual(err["details"]["key"], key)
        self.assertEqual(err["details"]["version"], v + 1)
        self.assertEqual(err["details"]["title"], "Ammo counter overlap")

        self.assertEqual(self.ok("POST", f"/api/issues/{key}/assign", {"assignee": "claude-ui"})["assignee"],
                         "claude-ui")
        self.assertIsNone(self.ok("POST", f"/api/issues/{key}/assign", {"assignee": None})["assignee"])
        self.refused("POST", f"/api/issues/{key}/assign", {"assignee": "nobody"}, status=404, code="not_found")

        moved = self.ok("POST", f"/api/issues/{key}/status", {"status": "in_progress", "note": "on it"})
        self.assertEqual(moved["status"], "in_progress")
        self.refused("POST", f"/api/issues/{key}/status", {"status": "sideways"}, status=400,
                  code="invalid_request")

        ranked = self.ok("POST", f"/api/issues/{other}/rank", {"top": True})
        self.assertLess(ranked["rank"], self.ok("GET", f"/api/issues/{key}")["rank"])

        linked = self.ok("POST", f"/api/issues/{key}/links", {"kind": "blocks", "target": other})
        self.assertEqual(linked["links"]["blocks"], [other])
        self.assertEqual(self.ok("GET", f"/api/issues/{other}")["links"]["blocked_by"], [key])
        unlinked = self.ok("DELETE", f"/api/issues/{key}/links", {"kind": "blocks", "target": other})
        self.assertEqual(unlinked["links"]["blocks"], [])

        events = self.ok("GET", f"/api/issues/{key}/events")
        self.assertIn("created", [e["action"] for e in events])
        self.assertEqual(set(events[0]), {"id", "issue", "actor", "action", "old", "new", "at"})
        activity = self.ok("GET", "/api/activity?limit=3")
        self.assertEqual(len(activity["items"]), 3)
        ids = [e["id"] for e in activity["items"]]
        self.assertEqual(ids, sorted(ids, reverse=True))  # newest first
        self.assertEqual(activity["last_id"], ids[0])
        older = self.ok("GET", f"/api/activity?since={ids[-1]}")
        self.assertTrue(all(e["id"] > ids[-1] for e in older["items"]))

        self.assertEqual(self.ok("DELETE", f"/api/issues/{other}"), {"deleted": other})
        self.refused("GET", f"/api/issues/{other}", status=404, code="not_found")

    def test_comments(self):
        key = self.ok("POST", "/api/issues", {"title": "Eye color resets"})["key"]
        first = self.ok("POST", f"/api/issues/{key}/comments", {"body": "Repro on HDD builds @claude-ui"})
        self.assertEqual(first["mentions"], ["claude-ui"])
        self.assertEqual((first["author"], first["kind"], first["issue"]), ("owner", "note", key))
        reply = self.ok("POST", f"/api/issues/{key}/comments",
                        {"body": "Which build?", "kind": "question", "reply_to": first["id"]})
        self.assertEqual(reply["reply_to"], first["id"])
        page = self.ok("GET", f"/api/issues/{key}/comments")
        self.assertEqual([c["id"] for c in page["items"]], [first["id"], reply["id"]])
        self.assertEqual((page["last_id"], page["has_more"]), (reply["id"], False))
        page = self.ok("GET", f"/api/issues/{key}/comments?since={first['id']}")
        self.assertEqual([c["id"] for c in page["items"]], [reply["id"]])
        page = self.ok("GET", f"/api/issues/{key}/comments?limit=1")
        self.assertTrue(page["has_more"])

        edited = self.ok("PATCH", f"/api/comments/{first['id']}", {"body": "Repro on HDD builds only"})
        self.assertEqual(edited["body"], "Repro on HDD builds only")
        self.assertTrue(edited["edited_at"])
        theirs = self.worker.add_comment(key, "Looking now")
        self.refused("PATCH", f"/api/comments/{theirs['id']}", {"body": "hijack"}, status=403, code="forbidden")
        self.refused("PATCH", "/api/comments/99999", {"body": "x"}, status=404, code="not_found")
        self.refused("POST", f"/api/issues/{key}/comments", {"body": "x", "kind": "approval"}, status=400,
                  code="invalid_request")

    def test_held_issue_conflict_carries_issue(self):
        key = self.ok("POST", "/api/issues", {"title": "Held"})["key"]
        self.worker.claim(key)
        # the owner may make any move, so a held claim is only a conflict for regular actors;
        # a stale version is the owner's conflict
        current = self.ok("GET", f"/api/issues/{key}")
        self.assertEqual((current["status"], current["assignee"]), ("in_progress", "claude-ui"))
        err = self.refused("PATCH", f"/api/issues/{key}", {"version": current["version"] - 1, "title": "x"},
                        status=409, code="conflict")
        self.assertEqual(err["details"]["assignee"], "claude-ui")

    def test_team_and_profiles(self):
        team = self.ok("GET", "/api/team")
        handles = {a["handle"]: a for a in team["agents"]}
        self.assertIn("claude-ui", handles)
        self.assertEqual(handles["claude-ui"]["cli"], "claude-code")
        for field in ("presence", "working", "reviewing", "assigned"):
            self.assertIn(field, handles["claude-ui"])
        slugs = [p["slug"] for p in self.ok("GET", "/api/profiles")]
        self.assertEqual(set(slugs) >= {"worker", "reviewer", "pm"}, True)
        made = self.ok("POST", "/api/profiles", {"slug": "ui-lead", "name": "UI lead", "role": "worker",
                                                  "icon": "layout-dashboard", "prompt": "Own the UI.",
                                                  "topics": ["ui-ux"]})
        self.assertEqual((made["slug"], made["topics"]), ("ui-lead", ["ui-ux"]))
        edited = self.ok("PATCH", "/api/profiles/ui-lead", {"version": made["version"], "prompt": "Own it all."})
        self.assertEqual(edited["prompt"], "Own it all.")
        err = self.refused("PATCH", "/api/profiles/ui-lead", {"version": made["version"], "name": "Stale"},
                        status=409, code="conflict")
        self.assertEqual(err["details"]["slug"], "ui-lead")
        self.assertEqual(err["details"]["version"], edited["version"])
        self.refused("POST", "/api/profiles", {"slug": "ui-lead", "name": "Again", "role": "worker"},
                  status=409, code="conflict")
        self.assertEqual(self.ok("DELETE", "/api/profiles/ui-lead"), {"deleted": "ui-lead"})
        self.refused("DELETE", "/api/profiles/ui-lead", status=404, code="not_found")

    def test_errors(self):
        self.refused("GET", "/api/issues/GAME-404", status=404, code="not_found")
        self.refused("GET", "/api/issues/not-a-key", status=400, code="invalid_request")
        self.refused("POST", "/api/issues", {"title": "x", "priority": "p9"}, status=400, code="invalid_request")
        self.refused("POST", "/api/issues", {"title": "x", "topic": "nope"}, status=404, code="not_found")
        self.refused("GET", "/api/issues?status=sideways", status=400, code="invalid_request")


class RealStreamTests(RealServerCase):
    poll_interval = 1.0  # the production interval

    def test_write_from_another_connection_streams_a_change(self):
        self.assertTrue(self.h.server.watcher.ready.wait(5))
        reader = SSEReader(self.h.port)
        self.addCleanup(reader.close)
        self.assertEqual(reader.status, 200)
        start = reader.next_change()
        key = self.worker.create_issue("Raised by an agent")["key"]
        comment = self.worker.add_comment(key, "first note")
        t0 = time.monotonic()
        got = reader.next_change(timeout=2.5)
        while got["comment_id"] < comment["id"] and time.monotonic() - t0 < 2.5:
            got = reader.next_change(timeout=2.5)
        self.assertLess(time.monotonic() - t0, 2.5)
        self.assertGreater(got["event_id"], start["event_id"])
        self.assertEqual(got["comment_id"], comment["id"])

    def test_write_through_the_api_streams_a_change(self):
        self.assertTrue(self.h.server.watcher.ready.wait(5))
        reader = SSEReader(self.h.port)
        self.addCleanup(reader.close)
        start = reader.next_change()
        self.ok("POST", "/api/issues", {"title": "From the browser"})
        got = reader.next_change(timeout=2.5)
        self.assertGreater(got["event_id"], start["event_id"])

    def test_idle_server_writes_nothing(self):
        self.assertTrue(self.h.server.watcher.ready.wait(5))
        reader = SSEReader(self.h.port)
        self.addCleanup(reader.close)
        reader.next_change()
        for path in ("/api/board", "/api/topics?all=1", "/api/issues?status=all", "/api/team",
                     "/api/profiles", "/api/activity", "/", "/assets/js/api.js"):
            self.h.request("GET", path)
        before = _files(self.db)
        time.sleep(2.5)  # two watcher polls with a stream open
        for path in ("/api/board", "/api/issues", "/api/team"):
            self.h.request("GET", path)
        self.assertEqual(_files(self.db), before)
        self.assertEqual(reader.buf, b"")  # no spurious change events


@unittest.skipUnless(CORE, "earmark.board is not implemented yet")
class RealBoardFactoryTests(unittest.TestCase):
    def test_requests_open_and_close_their_own_board(self):
        tmp = tempfile.mkdtemp(prefix="earmark-server-real-")
        self.addCleanup(shutil.rmtree, tmp, True)
        board = make_board(tmp)
        db = board.path
        board.close()
        h = ServerHarness(db)
        try:
            self.assertEqual(h.server.owner, "owner")
            self.assertEqual(h.server.prefix, "GAME")
            for _ in range(20):
                self.assertEqual(h.request("GET", "/api/board").status, 200)
            other = open_as(db, "owner")
            try:
                self.assertEqual(other.info()["prefix"], "GAME")
            finally:
                other.close()
        finally:
            h.stop()

    def test_missing_board(self):
        from earmark import errors
        from earmark.server import make_server

        tmp = tempfile.mkdtemp(prefix="earmark-server-real-")
        self.addCleanup(shutil.rmtree, tmp, True)
        with self.assertRaises(errors.EarmarkError) as ctx:
            make_server(os.path.join(tmp, "nope", "earmark.db"), port=0)
        self.assertIn(ctx.exception.code, ("no_board", "not_found"))


if __name__ == "__main__":
    unittest.main()
