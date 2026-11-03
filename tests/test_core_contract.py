"""Live board output validated against contracts/board.schema.json (stdlib mini-validator)."""

from __future__ import annotations

import json
import os
import re
import unittest

from tests.support import BoardCase

from earmark import CONTRACT_VERSION
from earmark.errors import Conflict, Empty

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "contracts", "board.schema.json"), encoding="utf-8") as _fh:
    SCHEMA = json.load(_fh)

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _type_ok(value, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def errors(value, schema: dict, path: str = "$") -> list[str]:
    """The subset of JSON Schema the contract uses: $ref, type, enum, pattern, min/max,
    required, properties, additionalProperties, items, oneOf."""
    if "$ref" in schema:
        return errors(value, SCHEMA["$defs"][schema["$ref"].rsplit("/", 1)[-1]], path)
    if "oneOf" in schema:
        passing = [s for s in schema["oneOf"] if not errors(value, s, path)]
        return [] if len(passing) == 1 else [f"{path}: matches {len(passing)} of oneOf"]
    out: list[str] = []
    types = schema.get("type")
    if types and not any(_type_ok(value, t) for t in ([types] if isinstance(types, str) else types)):
        return [f"{path}: {value!r} is not {types}"]
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} not in enum")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path}: {value!r} does not match {schema['pattern']}")
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 1 << 30):
            out.append(f"{path}: length {len(value)} out of range")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
        if value < schema["minimum"]:
            out.append(f"{path}: {value} < {schema['minimum']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{path}: missing {key}")
        props = schema.get("properties", {})
        for key, item in value.items():
            if key in props:
                out += errors(item, props[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}: unexpected {key}")
    if isinstance(value, list) and "items" in schema:
        for n, item in enumerate(value):
            out += errors(item, schema["items"], f"{path}[{n}]")
    return out


class Contract(BoardCase):
    def check(self, name: str, value) -> None:
        problems = errors(json.loads(json.dumps(value)), {"$ref": f"#/$defs/{name}"})
        self.assertEqual(problems, [], name)

    def test_versions_agree(self):
        self.assertEqual(SCHEMA["x-contract-version"], CONTRACT_VERSION)
        with open(os.path.join(ROOT, "contracts", "envelope.schema.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["x-contract-version"], CONTRACT_VERSION)

    def test_every_shape_on_a_busy_board(self):
        b = self.board
        b.add_topic("ui-ux/menu", icon="layout")
        b.add_profile("ui-lead", name="UI lead", role="worker", topics=["ui-ux"])
        w1 = self.agent("claude-ui", "ui-lead", cli="claude-code")
        r1 = self.agent("codex-qa", "reviewer", cli="codex")
        brief = self.open_as(None).join("worker", cli="grok")
        parent = self.issue("Parent", topic="ui-ux/menu", labels=["epic"], due="+3d")
        child = self.issue("Child", parent=parent["key"], priority="urgent", type="bug",
                           topic="ui-ux/menu")
        b.link(child["key"], "blocks", parent["key"])
        b.link(parent["key"], "relates", self.issue("Other")["key"])
        work = w1.next()
        w1.add_comment(work["key"], "progress @codex-qa", kind="progress")
        w1.submit_review(work["key"], note="handoff")
        r1.next()
        r1.reject(work["key"], note="changes")
        b.config("lease_minutes", "60")
        self.check("Briefing", brief)
        self.check("Briefing", w1.join("ui-lead"))
        for issue in (parent, child, work, b.get_issue(work["key"], comments=5), w1.claim(work["key"])):
            self.check("Issue", issue)
        self.check("IssueList", b.list_issues(status="all"))
        self.check("CommentPage", b.list_comments(work["key"]))
        self.check("CommentPage", w1.list_comments(work["key"], new=True))
        self.check("InboxPage", r1.inbox(peek=True))
        self.check("InboxPage", r1.inbox())
        self.check("Activity", b.activity(limit=200))
        for event in b.issue_events(work["key"]):
            self.check("Event", event)
        self.check("Team", b.team())
        for topic in b.topics(include_archived=True):
            self.check("Topic", topic)
        self.check("Topic", b.edit_topic("ui-ux", name="UI"))
        for profile in b.profiles():
            self.check("Profile", profile)
        self.check("Comment", b.edit_comment(b.add_comment(parent["key"], "x")["id"], "y"))
        self.check("Agent", w1.whoami()["agent"])
        for name in ("backlog", "unassigned", "stale", "review_queue", "blocked", "overdue",
                     "review_cap"):
            for item in b.triage()[name]:
                self.check("IssueSummary", item)

    def test_check_feed_and_new_events(self):
        import io

        b = self.board
        b.add_topic("api")
        b.config("max_review_rounds", "1")
        ui, qa = self.agent("claude-ui"), self.agent("codex-qa", "reviewer")
        pm = self.agent("grok-pm", "pm")
        key = self.issue("feature", priority="low")["key"]
        ui.claim(key)
        self.check("Check", ui.check())
        ui.submit_review(key, note="ready")
        qa.claim(key)
        qa.reject(key, note="no")
        waiting = self.issue("waits on the api")["key"]
        ui.claim(waiting)
        dep = ui.need(waiting, "endpoint", topic="api")["created"]["key"]
        pm.update_issue(waiting, priority="high", title="Feature")
        b.attach(waiting, [("log.txt", io.BytesIO(b"trace"))])
        pm.add_comment(waiting, "@claude-ui status?", kind="question")
        b.move(dep, "done")
        b.assign(self.issue("assigned")["key"], "claude-ui")
        self.clock.advance(minutes=200)
        stale = self.issue("stale")["key"]
        b.assign(stale, "claude-ui")
        ui.claim(stale)
        self.clock.advance(minutes=200)
        pm.claim(stale)
        feed = ui.check(peek=True)
        self.check("Check", feed)
        kinds = {c["kind"] for item in feed["changed"] for c in item["changes"]}
        self.assertTrue({"assigned", "rejected", "unblocked", "review_cap", "takeover", "field"} <= kinds,
                        kinds)
        self.assertEqual(ui.pending_changes(), len(feed["changed"]) + feed["more"])
        self.check("Check", ui.check())
        lapsing = self.issue("lapsing")["key"]
        qa.claim(lapsing)
        self.clock.advance(minutes=200)
        board = pm.check(peek=True)  # a pm's feed: its scope's flow too
        self.check("Check", board)
        kinds = {c["kind"] for item in board["changed"] for c in item["changes"]}
        self.assertTrue({"created", "handoff", "stale", "field"} <= kinds, kinds)
        ui.leave(keep=True)
        self.open_as(None).join("worker", handle="claude-ui", cli="other")
        self.open_as(None).join("worker", handle="claude-ui")
        with self.assertRaises(Conflict) as taken:
            self.open_as(None).join("worker", handle="claude-ui", cli="third")
        self.check("HandleConflict", taken.exception.details)
        b.move(key, "done")
        b.reopen(key, note="again")
        events = b.activity(limit=500)["items"]
        for action in ("unblocked", "review_cap", "review_reset", "attach", "rejoin", "join"):
            self.assertIn(action, {e["action"] for e in events})
        for event in events:
            self.check("Event", event)

    def test_error_details(self):
        w1, w2 = self.agent("w1"), self.agent("w2")
        key = self.issue("x")["key"]
        w1.claim(key)
        with self.assertRaises(Conflict) as held:
            w2.claim(key)
        self.check("HolderConflict", held.exception.details)
        self.board.update_issue(key, title="y")
        with self.assertRaises(Conflict) as stale:
            self.board.update_issue(key, if_version=1, title="z")
        self.check("VersionConflict", stale.exception.details)
        self.check("Issue", {k: v for k, v in stale.exception.details.items()
                             if k not in ("current_version", "events_since")})
        w1.submit_review(key, note="done")
        with self.assertRaises(Empty) as empty:
            w2.next()
        self.check("NextEmpty", empty.exception.details)


if __name__ == "__main__":
    unittest.main()
