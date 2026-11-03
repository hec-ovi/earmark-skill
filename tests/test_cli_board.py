"""Board commands against the recording fake board: init paths, wait loop, ui, doctor, config."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import fake_board, run_main  # noqa: E402

from earmark import errors  # noqa: E402
from earmark.cli import cmd_board  # noqa: E402


class InitDirTests(unittest.TestCase):
    def test_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cmd_board.init_dir(tmp), tmp)
            self.assertEqual(cmd_board.init_dir(os.path.join(tmp, ".earmark", "earmark.db")), tmp)
            self.assertEqual(cmd_board.init_dir(os.path.join(tmp, "newproj")), os.path.join(tmp, "newproj"))
            with self.assertRaises(errors.InvalidRequest):
                cmd_board.init_dir(os.path.join(tmp, "other.db"))
        self.assertEqual(cmd_board.init_dir(None), os.getcwd())

    def test_durations(self):
        self.assertEqual(cmd_board.parse_duration("9m"), 540)
        self.assertEqual(cmd_board.parse_duration("90"), 90)
        self.assertEqual(cmd_board.parse_duration("1h30m"), 5400)
        self.assertEqual(cmd_board.parse_duration("0.5"), 0.5)
        for bad in ("", "9x", "m9", "1h 2m"):
            with self.assertRaises(errors.InvalidRequest, msg=bad):
                cmd_board.parse_duration(bad)


class WaitTests(unittest.TestCase):
    def test_immediate_hit_reads_version_first(self):
        hit = {"reason": "work", "key": "GAME-42", "title": "Eye color"}
        with fake_board(wait_check=hit) as boards:
            code, out, _ = run_main(["wait", "--for", "work", "--as", "w-1"])
        self.assertEqual((code, out), (0, "wait: work · GAME-42 Eye color · next: earmark next\n"))
        names = [c[0] for c in boards[0].calls]
        self.assertEqual(names, ["open", "touch", "data_version", "wait_check"])
        self.assertEqual(boards[0].called("wait_check"), [(("work",), {})])

    def test_checks_only_when_data_version_moves(self):
        versions = iter([1, 1, 1, 2, 2, 3])
        checks = iter([None, None, {"reason": "changes", "count": 2}])
        with fake_board(data_version=lambda: next(versions), wait_check=lambda mode: next(checks)) as boards:
            code, out, _ = run_main(["wait", "--interval", "0.01", "--timeout", "5s", "--as", "w-1"])
        self.assertEqual((code, out), (0, "wait: changes · 2 for you: earmark check\n"))
        self.assertEqual(len(boards[0].called("data_version")), 6)
        self.assertEqual(len(boards[0].called("wait_check")), 3)
        self.assertEqual(boards[0].called("wait_check")[0], (("any",), {}))

    def test_timeout_is_exit_4_on_stdout(self):
        with fake_board(wait_check=None) as boards:
            code, out, err = run_main(["wait", "--timeout", "0.2", "--interval", "0.05", "--as", "w-1"])
        self.assertEqual((code, err), (4, ""))
        self.assertEqual(out, "wait: nothing new in 0.2 · for: any\n")
        self.assertEqual(len(boards[0].called("wait_check")), 1)
        self.assertGreaterEqual(len(boards[0].called("data_version")), 3)
        self.assertEqual([c for c in boards[0].calls if c[0] not in
                          ("open", "touch", "data_version", "wait_check", "wait_wake_in")], [],
                         "read-only loop")
        with fake_board(wait_check=None):
            code, out, _ = run_main(["wait", "--timeout", "0", "--json", "--as", "w-1"])
        env = json.loads(out)
        self.assertEqual((code, env["error"]["code"], env["error"]["details"]["mode"]), (4, "empty", "any"))

    def test_checks_again_when_a_lease_lapses_without_a_write(self):
        """A lapsed claim writes nothing: wait asks when the next lease ends and checks then."""
        checks = iter([None, {"reason": "changes", "count": 1}])
        with fake_board(data_version=1, wait_check=lambda mode: next(checks),
                        wait_wake_in=lambda mode: 0.05) as boards:
            code, out, _ = run_main(["wait", "--for", "changes", "--interval", "0.01",
                                     "--timeout", "5s", "--as", "pm-1"])
        self.assertEqual((code, out), (0, "wait: changes · 1 for you: earmark check\n"))
        self.assertEqual(len(boards[0].called("wait_check")), 2)
        self.assertEqual(boards[0].called("wait_wake_in"), [(("changes",), {})])

    def test_inbox_is_the_old_name_of_changes(self):
        with fake_board(wait_check=None) as boards:
            code, out, _ = run_main(["wait", "--for", "inbox", "--timeout", "0", "--as", "w-1"])
        self.assertEqual((code, out), (4, "wait: nothing new in 0 · for: changes\n"))
        self.assertEqual(boards[0].called("wait_check"), [(("changes",), {})])
        code, _, err = run_main(["wait", "--for", "mail", "--as", "w-1"])
        self.assertNotEqual(code, 0)
        self.assertIn("changes", err)

    def test_default_timeout_fits_a_10_minute_tool_call(self):
        from earmark.cli import parser as P
        _, ns = P.parse(["wait"])
        self.assertEqual(cmd_board.parse_duration(ns.timeout), 540)
        self.assertEqual(ns.interval, 2.0)


class UiTests(unittest.TestCase):
    def test_serve_signature(self):
        serve = mock.Mock()
        fake_server = type(sys)("earmark.server")
        fake_server.serve = serve
        with fake_board() as boards, mock.patch.dict(sys.modules, {"earmark.server": fake_server}):
            code, _, _ = run_main(["ui", "--port", "7500", "--open"])
        self.assertEqual(code, 0)
        serve.assert_called_once_with("/tmp/board/.earmark/earmark.db", host="127.0.0.1", port=7500,
                                      open_browser=True)
        self.assertTrue(boards[0].closed, "board closed before serving")

    def test_ctrl_c_is_a_clean_exit(self):
        fake_server = type(sys)("earmark.server")
        fake_server.serve = mock.Mock(side_effect=KeyboardInterrupt)
        with fake_board(), mock.patch.dict(sys.modules, {"earmark.server": fake_server}):
            code, _, _ = run_main(["ui"])
        self.assertEqual(code, 0)
        fake_server.serve.assert_called_once()
        self.assertEqual(fake_server.serve.call_args.kwargs, {"host": "127.0.0.1", "port": 7477,
                                                              "open_browser": False})


class AdminTests(unittest.TestCase):
    def test_doctor_exit_code(self):
        checks = [{"name": "python", "ok": True, "detail": "3.12"}, {"name": "wal", "ok": False, "detail": "off"}]
        with fake_board(doctor=checks):
            code, out, _ = run_main(["doctor"])
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[-1], "doctor: 1 of 2 checks ok")
        with fake_board(doctor=checks):
            code, out, _ = run_main(["doctor", "--json"])
        env = json.loads(out)
        self.assertEqual((code, env["ok"], env["error"]["details"]), (1, False, checks))
        with fake_board(doctor=checks[:1]):
            self.assertEqual(run_main(["doctor"])[0], 0)

    def test_doctor_without_board(self):
        with mock.patch("earmark.board.Board.open", side_effect=errors.NoBoard("no board found",
                                                                           hint="earmark init")):
            code, out, _ = run_main(["doctor"])
        self.assertEqual(code, 1)
        self.assertIn("FAIL  board   no board found (earmark init)", out)

    def test_config_export_backup(self):
        with fake_board(config={"lease_minutes": 90}) as boards:
            _, out, _ = run_main(["config", "lease_minutes", "90", "--as", "hector"])
        self.assertEqual(boards[0].called("config"), [(("lease_minutes", "90"), {})])
        self.assertEqual(out, "lease_minutes: 90\n")
        with fake_board(export={"board": {"prefix": "GAME"}, "issues": []}):
            _, out, _ = run_main(["export"])
        self.assertEqual(json.loads(out), {"board": {"prefix": "GAME"}, "issues": []})
        with fake_board(backup={"path": "/b/copy.db", "bytes": 2048}) as boards:
            _, out, _ = run_main(["backup", "/b/copy.db"])
        self.assertEqual(boards[0].called("backup"), [(("/b/copy.db",), {})])
        self.assertEqual(out, "backup: /b/copy.db · 2.0 KB\n")


if __name__ == "__main__":
    unittest.main()
