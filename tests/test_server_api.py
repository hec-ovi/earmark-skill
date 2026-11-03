"""Every API route against a recording fake board: argument mapping, shapes, error mapping."""

from __future__ import annotations

import unittest

from tests.test_server_fixtures import FakeServerCase, errors, issue


class EnvelopeTests(FakeServerCase):
    def test_envelope_and_meta(self):
        resp = self.call("GET", "/api/board")
        data = self.assertEnvelopeOk(resp)
        env = resp.json
        self.assertRegex(env["contract_version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(env["meta"], {"command": "GET /api/board", "board": "GAME", "actor": "owner"})
        self.assertEqual(resp.headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(data["prefix"], "GAME")
        self.assertEqual(data["owner"], "owner")
        self.assertEqual(data["settings"], {"require_review": True, "lease_minutes": 120})
        self.assertEqual(data["counts"], {"by_status": {"todo": 1}, "open": 1, "total": 1})
        for name in ("statuses", "priorities", "types", "comment_kinds", "roles", "link_kinds"):
            self.assertIsInstance(data["enums"][name], list)
        self.assertIn("in_review", data["enums"]["statuses"])
        self.assertTrue(data["version"])

    def test_board_settings_from_flat_info(self):
        self.store.results["info"] = {"prefix": "GAME", "owner": "owner", "require_review": False,
                                      "lease_minutes": 30, "counts": {"open": 2, "total": 3}}
        data = self.assertEnvelopeOk(self.call("GET", "/api/board"))
        self.assertEqual(data["settings"], {"require_review": False, "lease_minutes": 30})
        self.assertEqual(data["counts"], {"by_status": {}, "open": 2, "total": 3})

    def test_requests_act_as_owner_and_close_boards(self):
        self.call("GET", "/api/issues")
        self.call("POST", "/api/issues", {"title": "x"})
        self.assertIn("owner", self.store.actors)
        # every board the request path opened was closed (the watcher keeps one open)
        self.assertEqual(self.store.opened - self.store.closed, 1)

    def test_unknown_route_is_404(self):
        err = self.assertEnvelopeError(self.call("GET", "/api/nope"), 404, "not_found")
        self.assertIn("/api/nope", err["message"])
        self.assertEnvelopeError(self.call("GET", "/api"), 404, "not_found")
        self.assertEnvelopeError(self.call("PATCH", "/api/comments/abc", {"body": "x"}), 404, "not_found")

    def test_wrong_method_is_405_with_allow(self):
        resp = self.call("PUT", "/api/issues/GAME-1", {"title": "x"})
        self.assertEnvelopeError(resp, 405, "invalid_request")
        self.assertEqual(resp.headers["Allow"], "DELETE, GET, PATCH")
        resp = self.call("POST", "/api/stream", {})
        self.assertEnvelopeError(resp, 405, "invalid_request")

    def test_head_on_api_has_no_body(self):
        resp = self.call("HEAD", "/api/board")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.body, b"")
        self.assertGreater(int(resp.headers["Content-Length"]), 0)


class ErrorMappingTests(FakeServerCase):
    CASES = [
        (errors.InvalidRequest, 400, "invalid_request", False),
        (errors.IdentityRequired, 400, "identity_required", False),
        (errors.NotJoined, 400, "not_joined", False),
        (errors.Forbidden, 403, "forbidden", False),
        (errors.NotFound, 404, "not_found", False),
        (errors.NoBoard, 404, "no_board", False),
        (errors.Empty, 404, "empty", False),
        (errors.Busy, 503, "busy", True),
        (errors.InternalError, 500, "internal_error", False),
    ]

    def test_each_error_maps_to_its_status(self):
        for cls, status, code, retriable in self.CASES:
            with self.subTest(code=code):
                self.store.fail["get_issue"] = cls(f"{code} happened", hint="try this", details={"x": 1})
                err = self.assertEnvelopeError(self.call("GET", "/api/issues/GAME-1"), status, code)
                self.assertEqual(err["message"], f"{code} happened")
                self.assertEqual(err["hint"], "try this")
                self.assertEqual(err["details"], {"x": 1})
                self.assertIs(err["retriable"], retriable)

    def test_conflict_carries_current_issue(self):
        self.store.fail["update_issue"] = errors.Conflict("version 3 is stale", details={"version": 5})
        self.store.results["get_issue"] = lambda a, k: issue(a[0], version=5, title="Current")
        resp = self.call("PATCH", "/api/issues/GAME-4", {"version": 3, "title": "Mine"})
        err = self.assertEnvelopeError(resp, 409, "conflict")
        self.assertEqual(err["details"]["key"], "GAME-4")
        self.assertEqual(err["details"]["version"], 5)
        self.assertEqual(err["details"]["title"], "Current")

    def test_conflict_on_status_move_carries_current_issue(self):
        self.store.fail["move"] = errors.Conflict("held by claude-ui until 16:05")
        err = self.assertEnvelopeError(self.call("POST", "/api/issues/GAME-2/status", {"status": "done"}),
                                       409, "conflict")
        self.assertEqual(err["details"]["key"], "GAME-2")
        self.assertIn("links", err["details"])

    def test_conflict_on_profile_carries_current_profile(self):
        self.store.fail["edit_profile"] = errors.Conflict("stale version")
        err = self.assertEnvelopeError(self.call("PATCH", "/api/profiles/ui-lead", {"version": 1, "name": "X"}),
                                       409, "conflict")
        self.assertEqual(err["details"]["slug"], "ui-lead")

    def test_unexpected_exception_is_internal_error(self):
        self.store.fail["team"] = RuntimeError("boom")
        import io
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            err = self.assertEnvelopeError(self.call("GET", "/api/team"), 500, "internal_error")
        self.assertNotIn("boom", err["message"])  # no internals on the wire
        self.assertIn("RuntimeError", stderr.getvalue())


class TopicRouteTests(FakeServerCase):
    def test_list(self):
        data = self.assertEnvelopeOk(self.call("GET", "/api/topics"))
        self.assertEqual(data[0]["path"], "engine")
        self.assertEqual(self.store.last("topics")[1], {"include_archived": False})
        self.call("GET", "/api/topics?all=1")
        self.assertEqual(self.store.last("topics")[1], {"include_archived": True})

    def test_add(self):
        body = {"path": "ui-ux/hud", "name": "HUD", "icon": "gauge", "description": "Heads-up display"}
        data = self.assertEnvelopeOk(self.call("POST", "/api/topics", body))
        self.assertEqual(data["path"], "ui-ux/hud")
        args, kw = self.store.last("add_topic")
        self.assertEqual(args, ("ui-ux/hud",))
        self.assertEqual(kw, {"name": "HUD", "icon": "gauge", "description": "Heads-up display"})

    def test_add_requires_path(self):
        err = self.assertEnvelopeError(self.call("POST", "/api/topics", {"name": "x"}), 400, "invalid_request")
        self.assertIn("path", err["message"])

    def test_edit_fields(self):
        self.assertEnvelopeOk(self.call("PATCH", "/api/topics/3", {"name": "Rendering", "position": 2,
                                                                    "archived": False}))
        args, kw = self.store.last("edit_topic")
        self.assertEqual(args, (3,))
        self.assertEqual(kw, {"name": "Rendering", "position": 2, "archived": False})
        self.assertNotIn("move_topic", self.store.names())

    def test_edit_path_moves(self):
        data = self.assertEnvelopeOk(self.call("PATCH", "/api/topics/3", {"path": "engine/gfx"}))
        self.assertEqual(self.store.last("move_topic")[0], (3, "engine/gfx"))
        self.assertEqual(data["path"], "engine/gfx")
        self.assertNotIn("edit_topic", self.store.names())
        self.call("PATCH", "/api/topics/3", {"path": "engine/gfx2", "name": "Graphics"})
        self.assertEqual(self.store.names()[-2:], ["move_topic", "edit_topic"])

    def test_edit_null_clears_icon_and_description(self):
        self.call("PATCH", "/api/topics/3", {"icon": None, "description": None})
        self.assertEqual(self.store.last("edit_topic"), ((3,), {"icon": "", "description": ""}))

    def test_edit_by_encoded_path(self):
        self.call("PATCH", "/api/topics/ui-ux%2Fhud", {"name": "HUD"})
        self.assertEqual(self.store.last("edit_topic")[0], ("ui-ux/hud",))

    def test_edit_validation(self):
        self.assertEnvelopeError(self.call("PATCH", "/api/topics/3", {}), 400, "invalid_request")
        self.assertEnvelopeError(self.call("PATCH", "/api/topics/3", {"position": "2"}), 400, "invalid_request")
        self.assertEnvelopeError(self.call("PATCH", "/api/topics/3", {"archived": 1}), 400, "invalid_request")
        err = self.assertEnvelopeError(self.call("PATCH", "/api/topics/3", {"colour": "red"}), 400,
                                       "invalid_request")
        self.assertIn("colour", err["message"])

    def test_delete(self):
        data = self.assertEnvelopeOk(self.call("DELETE", "/api/topics/4"))
        self.assertEqual(data, {"deleted": 4})
        self.assertEqual(self.store.last("remove_topic"), ((4,), {}))
        self.call("DELETE", "/api/topics/4?archive=1")
        self.assertEqual(self.store.last("remove_topic"), ((4,), {"archive": True}))


class IssueReadRouteTests(FakeServerCase):
    def test_list_defaults(self):
        data = self.assertEnvelopeOk(self.call("GET", "/api/issues"))
        self.assertEqual(set(data), {"items", "total", "limit", "offset"})
        self.assertEqual(self.store.last("list_issues")[1], {})

    def test_list_query_mirrors_ls(self):
        q = ("topic=ui-ux&topic=engine/physics,audio&exact=0&status=todo,in_progress&priority=high"
             "&priority=urgent&type=bug&assignee=me&reviewer=codex-review&reporter=pm&label=regression"
             "&label=ux,perf&q=eye%20color&due_before=2026-11-10&overdue=true&updated_since=2h"
             "&created_since=2026-11-01&ready=1&blocked=0&stale=false&parent=GAME-30&sort=due"
             "&desc=1&limit=20&offset=40&min_priority=high&_=123")
        self.assertEnvelopeOk(self.call("GET", "/api/issues?" + q))
        _, kw = self.store.last("list_issues")
        self.assertEqual(kw, {
            "topic": ["ui-ux", "engine/physics", "audio"], "exact": False,
            "status": ["todo", "in_progress"], "priority": ["high", "urgent"], "type": ["bug"],
            "assignee": "me", "reviewer": "codex-review", "reporter": "pm",
            "label": ["regression", "ux", "perf"], "q": "eye color", "due_before": "2026-11-10",
            "overdue": True, "updated_since": "2h", "created_since": "2026-11-01", "ready": True,
            "blocked": False, "stale": False, "parent": "GAME-30", "sort": "due", "desc": True,
            "limit": 20, "offset": 40, "min_priority": "high",
        })

    def test_list_single_status_is_a_string(self):
        self.call("GET", "/api/issues?status=all")
        self.assertEqual(self.store.last("list_issues")[1], {"status": "all"})

    def test_list_bad_query_values(self):
        self.assertEnvelopeError(self.call("GET", "/api/issues?limit=ten"), 400, "invalid_request")
        self.assertEnvelopeError(self.call("GET", "/api/issues?offset=-1"), 400, "invalid_request")
        err = self.assertEnvelopeError(self.call("GET", "/api/issues?overdue=maybe"), 400, "invalid_request")
        self.assertIn("overdue", err["message"])

    def test_get(self):
        data = self.assertEnvelopeOk(self.call("GET", "/api/issues/GAME-42"))
        self.assertEqual(data["key"], "GAME-42")
        self.assertEqual(self.store.last("get_issue")[0], ("GAME-42",))

    def test_events(self):
        data = self.assertEnvelopeOk(self.call("GET", "/api/issues/GAME-42/events"))
        self.assertEqual(data[0]["action"], "created")
        self.assertEqual(self.store.last("issue_events")[0], ("GAME-42",))

    def test_comments(self):
        data = self.assertEnvelopeOk(self.call("GET", "/api/issues/GAME-42/comments"))
        self.assertEqual(set(data), {"items", "last_id", "has_more"})
        self.assertEqual(self.store.last("list_comments"), (("GAME-42",), {}))
        self.call("GET", "/api/issues/GAME-42/comments?since=1187&limit=20")
        self.assertEqual(self.store.last("list_comments"), (("GAME-42",), {"since": 1187, "limit": 20}))
        self.assertEnvelopeError(self.call("GET", "/api/issues/GAME-42/comments?limit=0"), 400, "invalid_request")

    def test_activity(self):
        data = self.assertEnvelopeOk(self.call("GET", "/api/activity?since=12&limit=5"))
        self.assertEqual(set(data), {"items", "last_id"})
        self.assertEqual(self.store.last("activity"), ((), {"since": 12, "limit": 5}))

    def test_reads_never_touch(self):
        for path in ("/api/board", "/api/topics", "/api/issues", "/api/issues/GAME-1",
                     "/api/issues/GAME-1/comments", "/api/issues/GAME-1/events", "/api/activity",
                     "/api/team", "/api/profiles"):
            self.assertEnvelopeOk(self.call("GET", path))
        self.assertNotIn("touch", self.store.names())


class IssueWriteRouteTests(FakeServerCase):
    def test_create(self):
        body = {"title": "Eye color widget resets on reload", "body": "Steps...", "topic": "ui-ux/hud",
                "priority": "high", "type": "bug", "status": "backlog", "labels": ["regression"],
                "due": "2026-11-07", "parent": "GAME-30", "assignee": "claude-ui"}
        data = self.assertEnvelopeOk(self.call("POST", "/api/issues", body))
        self.assertEqual(data["key"], "GAME-7")
        self.assertIn("links", data)
        args, kw = self.store.last("create_issue")
        self.assertEqual(args, ("Eye color widget resets on reload",))
        expected = dict(body)
        del expected["title"]
        self.assertEqual(kw, expected)

    def test_create_drops_nulls_and_validates(self):
        self.call("POST", "/api/issues", {"title": "x", "topic": None, "due": None})
        self.assertEqual(self.store.last("create_issue"), (("x",), {}))
        self.assertEnvelopeError(self.call("POST", "/api/issues", {}), 400, "invalid_request")
        self.assertEnvelopeError(self.call("POST", "/api/issues", {"title": 5}), 400, "invalid_request")
        self.assertEnvelopeError(self.call("POST", "/api/issues", {"title": "x", "labels": "a"}), 400,
                                 "invalid_request")
        self.assertEnvelopeError(self.call("POST", "/api/issues", ["x"]), 400, "invalid_request")

    def test_create_fetches_issue_when_board_returns_less(self):
        self.store.results["create_issue"] = {"key": "GAME-8"}
        data = self.assertEnvelopeOk(self.call("POST", "/api/issues", {"title": "x"}))
        self.assertEqual(data["key"], "GAME-8")
        self.assertEqual(self.store.last("get_issue")[0], ("GAME-8",))

    def test_patch(self):
        body = {"version": 7, "title": "New", "topic": None, "due": None, "parent": None,
                "labels": ["a", "b"], "priority": "low", "type": "chore", "body": "text"}
        data = self.assertEnvelopeOk(self.call("PATCH", "/api/issues/GAME-42", body))
        self.assertEqual(data["version"], 8)
        args, kw = self.store.last("update_issue")
        self.assertEqual(args, ("GAME-42",))
        self.assertEqual(kw, {"if_version": 7, "title": "New", "topic": None, "due": None, "parent": None,
                              "labels": ["a", "b"], "priority": "low", "type": "chore", "body": "text"})

    def test_patch_requires_version_and_a_change(self):
        err = self.assertEnvelopeError(self.call("PATCH", "/api/issues/GAME-1", {"title": "x"}), 400,
                                       "invalid_request")
        self.assertIn("version", err["message"])
        self.assertEnvelopeError(self.call("PATCH", "/api/issues/GAME-1", {"version": 1}), 400, "invalid_request")
        self.assertEnvelopeError(self.call("PATCH", "/api/issues/GAME-1", {"version": True, "title": "x"}),
                                 400, "invalid_request")
        self.assertEnvelopeError(self.call("PATCH", "/api/issues/GAME-1", {"version": 1, "status": "done"}),
                                 400, "invalid_request")

    def test_status(self):
        data = self.assertEnvelopeOk(self.call("POST", "/api/issues/GAME-42/status",
                                               {"status": "in_review", "note": "ready"}))
        self.assertEqual(data["status"], "in_review")
        self.assertEqual(self.store.last("move"), (("GAME-42", "in_review"), {"note": "ready"}))
        self.call("POST", "/api/issues/GAME-42/status", {"status": "todo"})
        self.assertEqual(self.store.last("move"), (("GAME-42", "todo"), {"note": None}))
        self.assertEnvelopeError(self.call("POST", "/api/issues/GAME-42/status", {}), 400, "invalid_request")

    def test_assign(self):
        data = self.assertEnvelopeOk(self.call("POST", "/api/issues/GAME-42/assign", {"assignee": "claude-ui"}))
        self.assertEqual(data["assignee"], "claude-ui")
        self.assertEqual(self.store.last("assign")[0], ("GAME-42", "claude-ui"))
        self.call("POST", "/api/issues/GAME-42/assign", {"assignee": None})
        self.assertEqual(self.store.last("assign")[0], ("GAME-42", None))
        self.assertEnvelopeError(self.call("POST", "/api/issues/GAME-42/assign", {}), 400, "invalid_request")

    def test_rank(self):
        self.assertEnvelopeOk(self.call("POST", "/api/issues/GAME-42/rank", {"before": "GAME-3"}))
        self.assertEqual(self.store.last("rank"), (("GAME-42",), {"before": "GAME-3"}))
        self.call("POST", "/api/issues/GAME-42/rank", {"after": "GAME-3", "before": "GAME-9"})
        self.assertEqual(self.store.last("rank"), (("GAME-42",), {"before": "GAME-9", "after": "GAME-3"}))
        self.call("POST", "/api/issues/GAME-42/rank", {"top": True})
        self.assertEqual(self.store.last("rank"), (("GAME-42",), {"top": True}))
        self.call("POST", "/api/issues/GAME-42/rank", {"bottom": True})
        self.assertEqual(self.store.last("rank"), (("GAME-42",), {"bottom": True}))
        self.assertEnvelopeError(self.call("POST", "/api/issues/GAME-42/rank", {}), 400, "invalid_request")

    def test_links(self):
        data = self.assertEnvelopeOk(self.call("POST", "/api/issues/GAME-42/links",
                                               {"kind": "blocks", "target": "GAME-43"}))
        self.assertEqual(data["key"], "GAME-42")
        self.assertEqual(self.store.last("link")[0], ("GAME-42", "blocks", "GAME-43"))
        self.assertEnvelopeOk(self.call("DELETE", "/api/issues/GAME-42/links",
                                        {"kind": "blocks", "target": 43}))
        self.assertEqual(self.store.last("unlink")[0], ("GAME-42", "blocks", "43"))
        self.assertEnvelopeError(self.call("POST", "/api/issues/GAME-42/links", {"kind": "blocks"}), 400,
                                 "invalid_request")

    def test_delete(self):
        data = self.assertEnvelopeOk(self.call("DELETE", "/api/issues/GAME-42"))
        self.assertEqual(data, {"deleted": "GAME-42"})
        self.assertEqual(self.store.last("delete_issue")[0], ("GAME-42",))

    def test_key_is_url_decoded(self):
        self.call("GET", "/api/issues/game%2D42")
        self.assertEqual(self.store.last("get_issue")[0], ("game-42",))


class CommentRouteTests(FakeServerCase):
    def test_add(self):
        data = self.assertEnvelopeOk(self.call("POST", "/api/issues/GAME-42/comments",
                                               {"body": "Looks good @claude-ui", "kind": "question",
                                                "reply_to": 1187}))
        self.assertEqual(data["id"], 9)
        self.assertEqual(self.store.last("add_comment"),
                         (("GAME-42", "Looks good @claude-ui"), {"kind": "question", "reply_to": 1187}))
        self.call("POST", "/api/issues/GAME-42/comments", {"body": "plain"})
        self.assertEqual(self.store.last("add_comment"), (("GAME-42", "plain"), {}))
        self.assertEnvelopeError(self.call("POST", "/api/issues/GAME-42/comments", {"kind": "note"}), 400,
                                 "invalid_request")
        self.assertEnvelopeError(self.call("POST", "/api/issues/GAME-42/comments",
                                           {"body": "x", "reply_to": "1"}), 400, "invalid_request")

    def test_edit(self):
        data = self.assertEnvelopeOk(self.call("PATCH", "/api/comments/1190", {"body": "fixed typo"}))
        self.assertEqual(data["body"], "fixed typo")
        self.assertEqual(self.store.last("edit_comment")[0], (1190, "fixed typo"))
        self.store.fail["edit_comment"] = errors.Forbidden("only the author may edit")
        self.assertEnvelopeError(self.call("PATCH", "/api/comments/1190", {"body": "x"}), 403, "forbidden")


class TeamRouteTests(FakeServerCase):
    def test_team(self):
        data = self.assertEnvelopeOk(self.call("GET", "/api/team"))
        self.assertEqual(data, {"agents": []})

    def test_profiles(self):
        data = self.assertEnvelopeOk(self.call("GET", "/api/profiles"))
        self.assertEqual(data[0]["slug"], "ui-lead")

    def test_add_profile(self):
        body = {"slug": "qa", "name": "QA", "role": "reviewer", "icon": "bug", "prompt": "Test it.",
                "topics": ["ui-ux"]}
        data = self.assertEnvelopeOk(self.call("POST", "/api/profiles", body))
        self.assertEqual(data["slug"], "qa")
        self.assertEqual(self.store.last("add_profile"),
                         (("qa",), {"name": "QA", "role": "reviewer", "icon": "bug", "prompt": "Test it.",
                                    "topics": ["ui-ux"]}))
        self.assertEnvelopeError(self.call("POST", "/api/profiles", {"slug": "qa", "name": "QA"}), 400,
                                 "invalid_request")

    def test_edit_profile(self):
        self.assertEnvelopeOk(self.call("PATCH", "/api/profiles/qa", {"version": 2, "prompt": "New",
                                                                      "topics": []}))
        self.assertEqual(self.store.last("edit_profile"), (("qa",), {"if_version": 2, "prompt": "New",
                                                                     "topics": []}))
        self.assertEnvelopeError(self.call("PATCH", "/api/profiles/qa", {"prompt": "x"}), 400, "invalid_request")
        self.assertEnvelopeError(self.call("PATCH", "/api/profiles/qa", {"version": 2, "slug": "x"}), 400,
                                 "invalid_request")

    def test_delete_profile(self):
        data = self.assertEnvelopeOk(self.call("DELETE", "/api/profiles/qa"))
        self.assertEqual(data, {"deleted": "qa"})
        self.assertEqual(self.store.last("remove_profile")[0], ("qa",))


class LoggingTests(unittest.TestCase):
    def _run(self, verbose: bool) -> str:
        import contextlib
        import io

        from tests.test_server_fixtures import FakeStore, ServerHarness

        store = FakeStore()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            h = ServerHarness(board_factory=store.factory, verbose=verbose)
            try:
                h.request("GET", "/api/board")
                h.request("GET", "/api/nope")
                h.request("POST", "/api/issues", {"title": "x"})
            finally:
                h.stop()
        return err.getvalue()

    def test_quiet_by_default(self):
        self.assertEqual(self._run(False), "")

    def test_verbose_logs_requests(self):
        out = self._run(True)
        self.assertIn("GET /api/board", out)
        self.assertIn("POST /api/issues", out)


if __name__ == "__main__":
    unittest.main()
