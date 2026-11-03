"""Pipes speak UTF-8 whatever the locale (a Windows pipe is simulated with PYTHONIOENCODING=cp1252):
non-ASCII text survives stdout, stderr and stdin; stdin that is not UTF-8 fails clearly."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import LAUNCHER, clean_env  # noqa: E402

TITLE = "日本語 título ✓"
WINDOWS_PIPE = {"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}


class WindowsPipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="earmark-stdio-")
        cls.run_cli("init", "--prefix", "T", "--owner", "owner")
        cls.run_cli("join", "worker", "--as", "w1")
        cls.run_cli("new", TITLE, "--as", "w1")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def run_cli(cls, *argv, stdin: bytes = b"", env: dict | None = None):
        proc = subprocess.run([sys.executable, LAUNCHER, *argv], cwd=cls.tmp, input=stdin,
                              capture_output=True, env=clean_env(**(env or {})), timeout=60)
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def test_json_and_text_keep_non_ascii(self):
        code, out, _ = self.run_cli("show", "T-1", "-f", "title", "--json", "--as", "w1", env=WINDOWS_PIPE)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["data"], {"title": TITLE})
        code, out, _ = self.run_cli("show", "T-1", "-f", "title", env=WINDOWS_PIPE)
        self.assertEqual((code, out), (0, f"title: {TITLE}\n"))

    def test_errors_keep_non_ascii(self):
        code, _, err = self.run_cli("new", "x", "-t", "ünknown", "--as", "w1", env=WINDOWS_PIPE)
        self.assertEqual(code, 1)
        self.assertIn("ünknown", err)

    def test_piped_utf8_body_is_stored_as_sent(self):
        body = "naïve café ✓ 日本"
        code, out, err = self.run_cli("comment", "T-1", "-", "--as", "w1", stdin=body.encode("utf-8"),
                                      env=WINDOWS_PIPE)
        self.assertEqual(code, 0, err)
        cid = int(out.strip().lstrip("#"))
        _, out, _ = self.run_cli("comments", "T-1", "--since", str(cid - 1), "--json")
        self.assertEqual(json.loads(out)["data"]["items"][0]["body"], body)

    def test_bom_is_dropped(self):
        code, out, _ = self.run_cli("comment", "T-1", "-", "--as", "w1",
                                    stdin="﻿with bom".encode("utf-8"), env=WINDOWS_PIPE)
        self.assertEqual(code, 0)
        _, out2, _ = self.run_cli("comments", "T-1", "--last", "1", "--json")
        self.assertEqual(json.loads(out2)["data"]["items"][0]["body"], "with bom")

    def test_stdin_that_is_not_utf8_fails_clearly(self):
        code, out, err = self.run_cli("comment", "T-1", "-", "--as", "w1",
                                      stdin="café".encode("cp1252"), env=WINDOWS_PIPE)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("error: comment: stdin is not UTF-8 text (byte 0xe9)", err)
        self.assertIn("hint: send UTF-8", err)

    def test_body_file_that_is_not_utf8_fails_clearly(self):
        path = os.path.join(self.tmp, "latin1.txt")
        with open(path, "wb") as f:
            f.write("café".encode("latin-1"))
        code, _, err = self.run_cli("comment", "T-1", "--body-file", path, "--as", "w1")
        self.assertEqual(code, 1)
        self.assertIn("is not UTF-8 text (byte 0xe9)", err)


if __name__ == "__main__":
    unittest.main()
