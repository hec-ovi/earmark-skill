"""Envelope, exit codes, error streams and identity resolution, against a recording fake board."""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import ROOT, fake_board, run_main  # noqa: E402

from earmark import CONTRACT_VERSION, errors  # noqa: E402
from earmark.cli import app  # noqa: E402

with open(os.path.join(ROOT, "contracts", "envelope.schema.json"), encoding="utf-8") as _f:
    SCHEMA = json.load(_f)


def check_envelope(test: unittest.TestCase, text: str) -> dict:
    """Validate against contracts/envelope.schema.json by hand (stdlib only)."""
    env = json.loads(text)
    test.assertEqual(set(env), set(SCHEMA["required"]))
    test.assertEqual(env["contract_version"], CONTRACT_VERSION)
    test.assertIsInstance(env["ok"], bool)
    test.assertIn("command", env["meta"])
    if env["ok"]:
        test.assertIsNone(env["error"])
    else:
        test.assertIsNone(env["data"])
        err = env["error"]
        allowed = set(SCHEMA["$defs"]["Error"]["properties"])
        test.assertTrue(set(err) <= allowed, set(err) - allowed)
        test.assertIn(err["code"], SCHEMA["$defs"]["Error"]["properties"]["code"]["enum"])
        test.assertIsInstance(err["message"], str)
        test.assertIsInstance(err["retriable"], bool)
    return env


class EnvelopeTests(unittest.TestCase):
    def test_success_envelope(self):
        with fake_board():
            code, out, err = run_main(["ls", "--json", "--as", "w-1"])
        self.assertEqual((code, err), (0, ""))
        env = check_envelope(self, out)
        self.assertTrue(env["ok"])
        self.assertEqual(env["meta"], {"command": "ls", "board": "GAME", "actor": "w-1"})
        self.assertEqual(env["data"]["total"], 2)
        self.assertEqual(out.count("\n"), 1, "compact single line when not a terminal")

    def test_error_envelope_and_exit_codes(self):
        cases = [(errors.NotFound("GAME-9 not found", hint="earmark ls"), 2),
                 (errors.Conflict("held by codex-qa until 16:05", details={"holder": "codex-qa"}), 3),
                 (errors.Busy("board is busy"), 5), (errors.Forbidden("owner only"), 1),
                 (errors.InvalidRequest("bad"), 1), (errors.NotJoined("join first"), 1)]
        for exc, exit_code in cases:
            with fake_board(get_issue=exc_raiser(exc)):
                code, out, err = run_main(["show", "9", "--json"])
            self.assertEqual(code, exit_code, exc.code)
            self.assertEqual(err, "")
            env = check_envelope(self, out)
            self.assertEqual(env["error"]["code"], exc.code)
            self.assertEqual(env["error"]["message"], exc.message)
            self.assertEqual(env["error"]["retriable"], exc.code == "busy")
            self.assertEqual(env["meta"]["board"], "GAME")

    def test_parse_error_envelope(self):
        code, out, err = run_main(["ls", "--bogus", "--json"])
        self.assertEqual((code, err), (1, ""))
        env = check_envelope(self, out)
        self.assertEqual(env["error"]["code"], "invalid_request")
        self.assertEqual(env["meta"]["command"], "earmark")

    def test_no_board_envelope(self):
        with mock.patch("earmark.board.Board.open", side_effect=errors.NoBoard("no board here",
                                                                           hint="earmark init")):
            code, out, _ = run_main(["ls", "--json"])
        self.assertEqual(code, 1)
        env = check_envelope(self, out)
        self.assertEqual((env["error"]["code"], env["meta"]["board"]), ("no_board", None))


def exc_raiser(exc):
    def raise_(*a, **k):
        raise exc
    return raise_


class TextErrorTests(unittest.TestCase):
    def test_error_and_hint_on_stderr(self):
        exc = errors.NotFound("GAME-9 not found", hint="earmark ls -q eye")
        with fake_board(get_issue=exc_raiser(exc)):
            code, out, err = run_main(["show", "9"])
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "error: GAME-9 not found\nhint: earmark ls -q eye\n")

    def test_error_without_hint(self):
        with fake_board(get_issue=exc_raiser(errors.Conflict("version moved"))):
            code, out, err = run_main(["show", "9"])
        self.assertEqual((code, err), (3, "error: version moved\n"))

    def test_parse_error_is_exit_1_not_2(self):
        code, out, err = run_main(["show"])
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("error: earmark show: the following arguments are required: KEY"))
        self.assertIn("hint: earmark show -h", err)

    def test_not_implemented_and_bugs_are_internal_errors(self):
        with fake_board(get_issue=exc_raiser(NotImplementedError("get_issue"))):
            code, out, err = run_main(["show", "1"])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("error: not implemented yet: get_issue"))
        with fake_board(get_issue=exc_raiser(KeyError("boom"))):
            code, out, err = run_main(["show", "1", "--json"])
        env = check_envelope(self, out)
        self.assertEqual((code, env["error"]["code"]), (1, "internal_error"))
        self.assertIn("EARMARK_DEBUG", env["error"]["hint"])

    def test_help_exit_codes(self):
        code, out, err = run_main([])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Read", out)
        code, out, _ = run_main(["ls", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("usage: earmark ls", out)


class IdentityTests(unittest.TestCase):
    def opened(self, boards):
        self.assertEqual(len(boards), 1)
        return boards[0]

    def test_as_flag_wins_over_env(self):
        with fake_board() as boards:
            run_main(["ls", "--as", "flag-1"], env={"EARMARK_AS": "env-1"})
        b = self.opened(boards)
        self.assertEqual(b.calls[0], ("open", (None, "flag-1"), {}))
        self.assertEqual(len(b.called("touch")), 1)

    def test_env_identity_and_db(self):
        with fake_board() as boards:
            run_main(["ls"], env={"EARMARK_AS": "env-1", "EARMARK_DB": "/x/b.db"})
        self.assertEqual(self.opened(boards).calls[0], ("open", ("/x/b.db", "env-1"), {}))

    def test_db_flag_wins(self):
        with fake_board() as boards:
            run_main(["--db", "/y/b.db", "ls"], env={"EARMARK_DB": "/x/b.db"})
        self.assertEqual(self.opened(boards).calls[0][1][0], "/y/b.db")

    def test_no_identity_no_touch(self):
        with fake_board() as boards:
            code, _, _ = run_main(["ls"], env={"EARMARK_AS": ""})
        b = self.opened(boards)
        self.assertEqual((code, b.calls[0]), (0, ("open", (None, None), {})))
        self.assertEqual(b.called("touch"), [])
        self.assertEqual(b.called("set_actor"), [])

    def test_owner_only_when_both_ttys(self):
        for stdin_tty, stdout_tty, expect in ((True, True, "hector"), (True, False, None),
                                              (False, True, None)):
            def isatty(stream, _i=stdin_tty, _o=stdout_tty):
                return _i if stream is sys.stdin else (_o if stream is sys.stdout else False)
            with fake_board() as boards, mock.patch.object(app, "_isatty", isatty):
                run_main(["ls", "--json"])
            b = self.opened(boards)
            self.assertEqual(b.actor, expect, (stdin_tty, stdout_tty))
            self.assertEqual(len(b.called("touch")), 1 if expect else 0)

    def test_board_closed(self):
        with fake_board() as boards:
            run_main(["ls"])
        self.assertTrue(self.opened(boards).closed)


if __name__ == "__main__":
    unittest.main()
