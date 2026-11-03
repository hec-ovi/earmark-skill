"""model.py and timeutil.py: parsing and validation."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tests.support import FakeClock  # noqa: F401  (puts src/ on sys.path)

from earmark import model, timeutil
from earmark.errors import InvalidRequest, NotFound


class KeysAndEnums(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(model.parse_key("GAME-42", "GAME"), 42)
        self.assertEqual(model.parse_key("game-42", "GAME"), 42)
        self.assertEqual(model.parse_key("42", "GAME"), 42)
        self.assertEqual(model.parse_key(" #7 ", "GAME"), 7)
        self.assertEqual(model.parse_key(9, "GAME"), 9)
        self.assertEqual(model.format_key("GAME", 3), "GAME-3")
        self.assertIsNone(model.format_key("GAME", None))
        with self.assertRaises(NotFound):
            model.parse_key("FOO-1", "GAME")
        for bad in ("", "GAME-", "x", "GAME-1a", "0x1", -1, 0):
            with self.assertRaises(InvalidRequest, msg=bad):
                model.parse_key(bad, "GAME")

    def test_priorities(self):
        self.assertEqual([model.parse_priority(p) for p in model.PRIORITIES], [0, 1, 2, 3, 4])
        self.assertEqual([model.parse_priority(f"p{i}") for i in range(5)], [0, 1, 2, 3, 4])
        self.assertEqual(model.parse_priority("HIGH"), 1)
        self.assertEqual(model.parse_priorities("urgent,low"), [0, 3])
        self.assertEqual(model.priority_name(4), "none")
        for bad in ("p5", "critical", 7):
            with self.assertRaises(InvalidRequest):
                model.parse_priority(bad)

    def test_status_groups(self):
        self.assertEqual(model.parse_statuses(None), list(model.OPEN))
        self.assertEqual(model.parse_statuses("open"), list(model.OPEN))
        self.assertEqual(model.parse_statuses("active"), ["todo", "in_progress", "in_review"])
        self.assertEqual(model.parse_statuses("closed"), ["done", "canceled"])
        self.assertEqual(model.parse_statuses("all"), list(model.STATUSES))
        self.assertEqual(model.parse_statuses(["todo,done", "todo"]), ["todo", "done"])
        self.assertEqual(model.parse_status("in-progress"), "in_progress")
        self.assertEqual(model.parse_status("cancelled"), "canceled")
        with self.assertRaises(InvalidRequest):
            model.parse_status("finished")

    def test_types_roles_kinds(self):
        self.assertEqual(model.parse_types("bug,task"), ["bug", "task"])
        self.assertEqual(model.parse_role("PM"), "pm")
        self.assertEqual(model.parse_link_kind("blocks"), "blocks")
        self.assertEqual(model.parse_comment_kind("progress"), "progress")
        with self.assertRaises(InvalidRequest):
            model.parse_comment_kind("approval")
        with self.assertRaises(InvalidRequest):
            model.parse_type("epic")

    def test_transition_table_matches_architecture(self):
        self.assertEqual(model.TRANSITIONS["in_review"], ("done", "todo", "in_progress"))
        self.assertEqual(model.TRANSITIONS["done"], ("todo",))
        self.assertNotIn("in_progress", model.TRANSITIONS["backlog"])


class SlugsAndLabels(unittest.TestCase):
    def test_handles(self):
        self.assertEqual(model.parse_handle("Claude-UI"), "claude-ui")
        self.assertEqual(model.parse_handle("@codex"), "codex")
        self.assertEqual(model.parse_handle("a" * 32), "a" * 32)
        for bad in ("-x", "a" * 33, "a_b", "", "a b"):
            with self.assertRaises(InvalidRequest, msg=bad):
                model.parse_handle(bad)

    def test_topic_paths(self):
        self.assertEqual(model.parse_topic_path("/UI-UX/menu/"), "ui-ux/menu")
        self.assertEqual(model.topic_ancestors("a/b/c"), ["a", "a/b"])
        self.assertEqual(model.parse_topic_path("x" * 48), "x" * 48)
        with self.assertRaises(InvalidRequest):
            model.parse_topic_path("x" * 49)
        with self.assertRaises(InvalidRequest):
            model.parse_topic_path("/".join(["a"] * 17))
        with self.assertRaises(InvalidRequest):
            model.parse_topic_path("a//b")

    def test_labels(self):
        self.assertEqual(model.parse_labels(["Regression", "ui:menu,v1.2", "regression"]),
                         ["regression", "ui:menu", "v1.2"])
        for bad in ("-x", "a" * 33, "a b", "ü"):
            with self.assertRaises(InvalidRequest, msg=bad):
                model.parse_label(bad)

    def test_prefixes(self):
        self.assertEqual(model.parse_prefix("game"), "GAME")
        self.assertEqual(model.default_prefix("earmark-skill"), "EARMAR")
        self.assertEqual(model.default_prefix("my-game"), "MYGAME")
        self.assertEqual(model.default_prefix("2048-clone"), "EM")
        self.assertEqual(model.default_prefix("---"), "EM")
        with self.assertRaises(InvalidRequest):
            model.parse_prefix("1ABC")
        with self.assertRaises(InvalidRequest):
            model.parse_prefix("ABCDEFGHIJK")

    def test_slugify_and_mentions(self):
        self.assertEqual(model.slugify("Ada Lovelace"), "ada-lovelace")
        self.assertEqual(model.slugify("  "), "")
        self.assertEqual(model.mentions("hi @claude-ui and @codex, again @claude-ui"),
                         ["claude-ui", "codex"])
        self.assertEqual(model.mentions("mail a@b.com or @@x or w@y"), [])
        self.assertEqual(model.mentions("(@pm) @QA"), ["pm"])


class TimeUtil(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(datetime(2026, 11, 3, 23, 30, 5, 999, tzinfo=timezone.utc))
        timeutil.set_clock(self.clock)

    def tearDown(self):
        timeutil.reset_clock()

    def test_now_is_injectable_and_second_precision(self):
        self.assertEqual(timeutil.now_iso(), "2026-11-03T23:30:05Z")
        self.clock.advance(minutes=1)
        self.assertEqual(timeutil.now_iso(), "2026-11-03T23:31:05Z")
        timeutil.reset_clock()
        self.assertRegex(timeutil.now_iso(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_iso_round_trip(self):
        moment = timeutil.parse_iso("2026-01-02T03:04:05Z")
        self.assertEqual(timeutil.iso(moment), "2026-01-02T03:04:05Z")
        with self.assertRaises(InvalidRequest):
            timeutil.parse_iso("2026-01-02 03:04:05")

    def test_durations(self):
        cases = {"30m": 1800, "2h": 7200, "3d": 259200, "1w": 604800, "45s": 45, "1h30m": 5400,
                 "90": 90}
        for text, seconds in cases.items():
            self.assertEqual(timeutil.parse_duration(text), timedelta(seconds=seconds), text)
        for bad in ("", "3x", "m", "1.5h", "-2h"):
            with self.assertRaises(InvalidRequest, msg=bad):
                timeutil.parse_duration(bad)

    def test_dates(self):
        self.assertEqual(timeutil.parse_date("today"), "2026-11-03")
        self.assertEqual(timeutil.parse_date("tomorrow"), "2026-11-04")
        self.assertEqual(timeutil.parse_date("+3d"), "2026-11-06")
        self.assertEqual(timeutil.parse_date("+2w"), "2026-11-17")
        self.assertEqual(timeutil.parse_date("2026-12-31"), "2026-12-31")
        for bad in ("2026-13-01", "next week", "+3m", "26-01-01"):
            with self.assertRaises(InvalidRequest, msg=bad):
                timeutil.parse_date(bad)

    def test_when(self):
        self.assertEqual(timeutil.parse_when("2h"), "2026-11-03T21:30:05Z")
        self.assertEqual(timeutil.parse_when("1w"), "2026-10-27T23:30:05Z")
        self.assertEqual(timeutil.parse_when("2026-11-01"), "2026-11-01T00:00:00Z")
        self.assertEqual(timeutil.parse_when("2026-11-01T10:00:00Z"), "2026-11-01T10:00:00Z")
        with self.assertRaises(InvalidRequest):
            timeutil.parse_when("soon")


if __name__ == "__main__":
    unittest.main()
