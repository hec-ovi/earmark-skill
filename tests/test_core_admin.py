"""Board utilities: info, config, doctor, export, backup, heads, data_version."""

from __future__ import annotations

import json
import os
import sqlite3
import unittest

from tests.support import BoardCase

from earmark.errors import Forbidden, InvalidRequest


class Info(BoardCase):
    def test_info_is_a_superset_of_api_board(self):
        self.issue("a")
        self.board.cancel(self.issue("b")["key"], note="no")
        info = self.board.info()
        for key in ("prefix", "owner", "settings", "counts", "enums", "version"):
            self.assertIn(key, info)
        self.assertEqual(info["settings"], {"require_review": True, "lease_minutes": 120})
        self.assertEqual((info["counts"]["open"], info["counts"]["total"]), (1, 2))
        self.assertEqual(info["counts"]["by_status"]["canceled"], 1)
        self.assertEqual(info["enums"]["priorities"], ["urgent", "high", "medium", "low", "none"])
        self.assertEqual(info["enums"]["link_kinds"], ["blocks", "duplicates", "relates"])
        self.assertEqual(info["agents"], {"online": 1, "total": 1})
        self.assertEqual(info["path"], self.board.path)
        self.assertGreater(info["files"]["db"], 0)
        self.assertEqual(info["heads"], self.board.heads())


class Config(BoardCase):
    def test_get_and_set(self):
        b = self.board
        self.assertEqual(b.config(), {"require_review": True, "lease_minutes": 120, "prefix": "GAME"})
        self.assertEqual(b.config("lease-minutes"), {"lease_minutes": 120})
        self.assertEqual(b.config("lease_minutes", "45"), {"lease_minutes": 45})
        self.assertEqual(b.config("lease_minutes", "2h"), {"lease_minutes": 120})
        self.assertEqual(b.config("require_review", "no"), {"require_review": False})
        self.assertEqual(b.config("prefix", "quest"), {"prefix": "QUEST"})
        self.assertEqual(self.issue("x")["key"], "QUEST-1")
        self.assertEqual(self.open_as(None).get_issue("quest-1")["key"], "QUEST-1")
        for key, value in (("lease_minutes", "0"), ("require_review", "maybe"), ("prefix", "9"),
                           ("colour", "red")):
            with self.assertRaises(InvalidRequest, msg=key):
                b.config(key, value)
        with self.assertRaises(Forbidden):
            self.agent("pm-1", "pm").config("lease_minutes", "5")
        self.assertIn("config", [e["action"] for e in b.activity()["items"]])

    def test_other_connections_see_settings(self):
        other = self.open_as(None)
        self.board.config("require_review", "0")
        self.assertEqual(other.config("require_review"), {"require_review": False})
        self.assertFalse(other.info()["settings"]["require_review"])


class Doctor(BoardCase):
    def test_checks_pass_on_a_fresh_board(self):
        checks = self.board.doctor()
        self.assertTrue(all(set(c) == {"name", "ok", "detail"} for c in checks))
        failing = [c for c in checks if not c["ok"]]
        self.assertEqual(failing, [])
        names = {c["name"] for c in checks}
        self.assertTrue({"python", "sqlite", "wal", "schema", "integrity", "gitignore"} <= names)
        os.remove(os.path.join(os.path.dirname(self.board.path), ".gitignore"))
        self.assertFalse({c["name"]: c["ok"] for c in self.board.doctor()}["gitignore"])


class ExportBackup(BoardCase):
    def test_export_is_json_ready_and_complete(self):
        b = self.board
        b.add_topic("a")
        one = self.issue("one", topic="a", labels=["x"])["key"]
        two = self.issue("two")["key"]
        b.link(one, "blocks", two)
        b.add_comment(one, "hello @owner")
        dump = json.loads(json.dumps(b.export()))
        self.assertEqual([i["key"] for i in dump["issues"]], [one, two])
        self.assertEqual(dump["issues"][0]["labels"], ["x"])
        self.assertEqual(dump["issues"][1]["blocked"], True)
        self.assertEqual(dump["links"][0]["src"], one)
        self.assertEqual(dump["comments"][0]["mentions"], ["owner"])
        self.assertEqual(dump["board"]["prefix"], "GAME")
        self.assertEqual(len(dump["events"]), len(b.activity(limit=1000)["items"]))
        self.assertTrue({"topics", "profiles", "agents", "cursors"} <= set(dump))

    def test_backup_is_a_consistent_single_file(self):
        self.issue("kept")
        target = os.path.join(self.tmp, "copy.db")
        result = self.board.backup(target)
        self.assertEqual(result["path"], target)
        self.assertGreater(result["bytes"], 0)
        copy = sqlite3.connect(target)
        self.addCleanup(copy.close)
        self.assertEqual(copy.execute("SELECT title FROM issues").fetchone()[0], "kept")
        self.assertEqual(copy.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        with self.assertRaises(InvalidRequest):
            self.board.backup(target)
        in_dir = self.board.backup(self.tmp)
        self.assertTrue(os.path.basename(in_dir["path"]).startswith("earmark-"))
        with self.assertRaises(InvalidRequest):
            self.board.backup(os.path.join(self.tmp, "missing", "x.db"))


class ChangeDetection(BoardCase):
    def test_heads_and_data_version(self):
        watcher = self.open_as(None)
        self.assertEqual(watcher.heads(), {"event_id": 0, "comment_id": 0})
        version = watcher.data_version()
        self.assertEqual(watcher.data_version(), version)
        key = self.issue("x")["key"]
        self.assertNotEqual(watcher.data_version(), version)
        self.board.add_comment(key, "c")
        self.assertEqual(watcher.heads(), {"event_id": 1, "comment_id": 1})
        own = self.board.data_version()
        self.board.add_comment(key, "own write")
        self.assertEqual(self.board.data_version(), own)


if __name__ == "__main__":
    unittest.main()
