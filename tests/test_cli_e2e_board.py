"""The checkout launcher in real subprocesses (non-TTY, like an agent shell): board discovery,
board commands, wait across processes, plain output and the lazy-import hot path."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import LAUNCHER, SRC, clean_env, run_launcher  # noqa: E402

ANSI = re.compile(r"\x1b\[")


class LauncherCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="earmark-cli-proc-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        proc = self.run_ok("init", "--prefix", "GAME", "--owner", "hector")
        self.init_out = proc.stdout
        self.db = os.path.join(self.tmp, ".earmark", "earmark.db")

    def run_cli(self, *argv, cwd=None, stdin="", env=None):
        proc = run_launcher(list(argv), cwd=cwd or self.tmp, stdin=stdin, env=env)
        self.assertIsNone(ANSI.search(proc.stdout + proc.stderr), "no color when not a terminal")
        return proc

    def run_ok(self, *argv, **kw):
        proc = self.run_cli(*argv, **kw)
        self.assertEqual(proc.returncode, 0, f"{argv}\n{proc.stdout}{proc.stderr}")
        return proc


class BoardCommandTests(LauncherCase):
    def test_init_and_discovery(self):
        lines = self.init_out.splitlines()
        self.assertTrue(lines[0].startswith("board: ") and
                        lines[0].endswith(os.path.join(".earmark", "earmark.db")), lines[0])
        self.assertEqual(lines[1], "prefix: GAME · owner: hector · review required: yes")
        self.assertTrue(lines[2].startswith("next: earmark topic add PATH"))
        with open(os.path.join(self.tmp, ".earmark", ".gitignore"), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "*")
        sub = os.path.join(self.tmp, "src", "deep")
        os.makedirs(sub)
        self.assertEqual(self.run_ok("ls", cwd=sub).stdout, "0 of 0 open issues\n")
        again = self.run_cli("init")
        self.assertEqual(again.returncode, 1)
        self.assertTrue(again.stderr.startswith("error: a board already exists"), again.stderr)

    def test_env_db_and_missing_board(self):
        elsewhere = tempfile.mkdtemp(prefix="earmark-cli-elsewhere-")
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        self.run_ok("new", "via env", cwd=elsewhere, env={"EARMARK_DB": self.db, "EARMARK_AS": "hector"})
        self.assertIn("via env", self.run_ok("ls", "--db", self.db, cwd=elsewhere).stdout)
        missing = os.path.join(elsewhere, "nope", "earmark.db")
        proc = self.run_cli("ls", "--db", missing)
        self.assertEqual((proc.returncode, proc.stdout), (1, ""))
        self.assertTrue(proc.stderr.startswith("error: "), proc.stderr)
        self.assertIn("\nhint: ", proc.stderr)
        proc = self.run_cli("--json", "ls", "--db", missing)
        env = json.loads(proc.stdout)
        self.assertEqual((proc.returncode, env["ok"], env["error"]["code"]), (1, False, "no_board"))

    def test_info_config_doctor_backup_export(self):
        self.run_ok("new", "One", "--as", "hector")
        info = self.run_ok("info").stdout.splitlines()
        self.assertTrue(info[0].startswith("board: ") and info[0].endswith("earmark.db"))
        self.assertTrue(info[1].startswith("prefix: GAME · owner: hector · schema: 1"))
        self.assertIn("issues: 1 total, 1 open · todo 1", info)
        self.assertEqual(self.run_ok("config").stdout,
                         "require_review: true\nlease_minutes: 120\nprefix: GAME\n")
        self.assertEqual(self.run_ok("config", "lease_minutes", "90", "--as", "hector").stdout,
                         "lease_minutes: 90\n")
        self.assertEqual(self.run_ok("config", "lease_minutes").stdout, "lease_minutes: 90\n")
        self.assertEqual(self.run_cli("config", "lease_minutes", "5").returncode, 1)
        doctor = self.run_ok("doctor").stdout.splitlines()
        self.assertRegex(doctor[-1], r"^doctor: (\d+) of \1 checks ok$")
        self.assertTrue(all(ln.startswith("ok  ") for ln in doctor[:-1]), doctor)
        dest = os.path.join(self.tmp, "copy.db")
        out = self.run_ok("backup", dest).stdout
        self.assertTrue(out.startswith("backup: ") and " KB" in out, out)
        self.assertTrue(os.path.getsize(dest) > 0)
        self.assertIn("One", self.run_ok("--db", dest, "ls").stdout)
        dump = json.loads(self.run_ok("export").stdout)
        self.assertEqual([i["title"] for i in dump["issues"]], ["One"])
        env = json.loads(self.run_ok("export", "--json").stdout)
        self.assertEqual((env["ok"], env["meta"]["command"]), (True, "export"))

    def test_exit_codes_from_the_process(self):
        self.run_ok("join", "worker", "--as", "w-1")
        self.assertEqual(self.run_cli("show", "99").returncode, 2)
        self.assertEqual(self.run_cli("ls", "--nope").returncode, 1)
        empty = self.run_cli("next", "--as", "w-1")
        self.assertEqual((empty.returncode, empty.stderr), (4, ""))
        self.assertTrue(empty.stdout.startswith("next: nothing ready in your scope · "))
        self.run_ok("new", "Held", "--as", "hector")
        self.run_ok("claim", "1", "--as", "w-1")
        self.run_ok("join", "worker", "--as", "w-2")
        self.assertEqual(self.run_cli("claim", "1", "--as", "w-2").returncode, 3)
        self.assertEqual(self.run_cli("new", "no identity").returncode, 1)

    def test_help(self):
        top = self.run_ok("--help").stdout
        self.assertIn("exit: 0 ok · 1 invalid · 2 not found · 3 conflict · 4 empty · 5 busy", top)
        self.assertEqual(self.run_ok().stdout, top)
        sub = self.run_ok("topic", "add", "-h").stdout
        self.assertTrue(sub.startswith("usage: earmark topic add"))
        self.assertNotIn("--json", sub.split("options:")[1], "global flags are hidden in nested help")


class WaitTests(LauncherCase):
    def setUp(self):
        super().setUp()
        self.run_ok("join", "worker", "--as", "w-1")

    def test_timeout_is_exit_4(self):
        started = time.monotonic()
        proc = self.run_cli("wait", "--timeout", "1s", "--interval", "0.2", "--as", "w-1")
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (4, "wait: nothing new in 1s · for: any\n", ""))
        self.assertLess(time.monotonic() - started, 15)

    def test_arrival_from_another_process(self):
        self.run_ok("new", "Talk", "--as", "hector")
        waiter = subprocess.Popen([sys.executable, LAUNCHER, "wait", "--for", "inbox", "--timeout", "60s",
                                   "--interval", "0.1", "--as", "w-1"], cwd=self.tmp,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=clean_env(), encoding="utf-8")
        self.addCleanup(lambda: waiter.poll() is None and waiter.kill())
        time.sleep(0.5)
        self.assertIsNone(waiter.poll(), "waits while nothing is new")
        self.run_ok("comment", "1", "ping @w-1", "--as", "hector")
        out, err = waiter.communicate(timeout=30)
        self.assertEqual((waiter.returncode, out, err), (0, "wait: inbox · 1 new · next: earmark inbox\n", ""))

    def test_work_ready_and_json(self):
        self.run_ok("new", "Ready now", "--as", "hector")
        out = self.run_ok("wait", "--for", "work", "--timeout", "5s", "--as", "w-1").stdout
        self.assertEqual(out, "wait: work · GAME-1 Ready now · next: earmark next\n")
        env = json.loads(self.run_ok("wait", "--json", "--timeout", "5s", "--as", "w-1").stdout)
        self.assertEqual(env["data"], {"reason": "work", "key": "GAME-1", "title": "Ready now"})


class HotPathTests(LauncherCase):
    def test_ls_loads_only_cli_board_and_db(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from earmark.cli import main; "
                "rc = main(['--db', sys.argv[2], 'ls']); "
                "print('MODULES', ' '.join(sorted(sys.modules)))")
        proc = subprocess.run([sys.executable, "-c", code, SRC, self.db], capture_output=True,
                              text=True, env=clean_env(), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        mods = set(proc.stdout.rsplit("MODULES ", 1)[1].split())
        for heavy in ("json", "earmark.server", "http.server", "socketserver", "difflib", "shutil",
                      "subprocess", "_colorize", "earmark.cli.cmd_board", "earmark.cli.cmd_team",
                      "earmark.cli.cmd_write", "earmark.cli.cmd_work"):
            self.assertNotIn(heavy, mods, f"{heavy} imported on the ls hot path")
        self.assertIn("earmark.cli.cmd_read", mods)


if __name__ == "__main__":
    unittest.main()
