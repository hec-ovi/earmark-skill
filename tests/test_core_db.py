"""db.py and Board.init/open: discovery, pragmas, write transactions, migrations, seeding."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from tests.support import make_board

from earmark import db, seed
from earmark.board import Board, default_owner
from earmark.errors import Busy, InternalError, InvalidRequest, NoBoard


def _git_env() -> dict:
    env = dict(os.environ)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME="test",
               GIT_AUTHOR_EMAIL="test@example.invalid", GIT_COMMITTER_NAME="test",
               GIT_COMMITTER_EMAIL="test@example.invalid")
    return env


def _git(cwd: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, env=_git_env(), check=True, capture_output=True,
                   timeout=30)


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="earmark-db-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def board(self, sub: str = "proj", **kw) -> Board:
        root = os.path.join(self.tmp, sub)
        os.makedirs(root, exist_ok=True)
        board = make_board(root, **kw)
        self.addCleanup(board.close)
        return board


class Discovery(TempDir):
    def test_flag_file_and_directories(self):
        board = self.board()
        root = os.path.dirname(os.path.dirname(board.path))
        self.assertEqual(db.find_board(board.path, env={}), board.path)
        self.assertEqual(db.find_board(root, env={}), board.path)
        self.assertEqual(db.find_board(os.path.dirname(board.path), env={}), board.path)
        self.assertEqual(db.find_board("proj", cwd=self.tmp, env={}), board.path)
        with self.assertRaises(NoBoard):
            db.find_board(os.path.join(self.tmp, "missing.db"), env={})
        with self.assertRaises(NoBoard):
            db.find_board(self.tmp, env={})

    def test_env_file_or_directory_and_precedence(self):
        first = self.board("one")
        second = self.board("two")
        root_two = os.path.dirname(os.path.dirname(second.path))
        self.assertEqual(db.find_board(env={"EARMARK_DB": second.path}, cwd=self.tmp), second.path)
        self.assertEqual(db.find_board(env={"EARMARK_DB": root_two}, cwd=self.tmp), second.path)
        self.assertEqual(db.find_board(first.path, env={"EARMARK_DB": second.path}), first.path)
        nested = os.path.join(os.path.dirname(os.path.dirname(first.path)), "a", "b")
        os.makedirs(nested)
        self.assertEqual(db.find_board(cwd=nested, env={}), first.path)
        self.assertEqual(db.find_board(cwd=nested, env={"EARMARK_DB": second.path}), second.path)

    def test_walk_up_and_nothing_found(self):
        board = self.board()
        deep = os.path.join(self.tmp, "proj", "src", "x", "y")
        os.makedirs(deep)
        self.assertEqual(db.find_board(cwd=deep, env={}), board.path)
        lonely = os.path.join(self.tmp, "elsewhere")
        os.makedirs(lonely)
        with mock.patch.object(db, "git_main_checkout", return_value=None):
            with self.assertRaises(NoBoard) as ctx:
                db.find_board(cwd=lonely, env={})
        self.assertEqual(ctx.exception.hint, "earmark init")

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_git_worktree_shares_the_main_board(self):
        main = os.path.join(self.tmp, "repo")
        os.makedirs(main)
        _git(main, "init", "-q")
        _git(main, "commit", "-q", "--allow-empty", "-m", "init")
        board = make_board(main)
        self.addCleanup(board.close)
        worktree = os.path.join(self.tmp, "wt-feature")
        _git(main, "worktree", "add", "-q", worktree)
        sub = os.path.join(worktree, "pkg")
        os.makedirs(sub)
        with mock.patch.dict(os.environ, _git_env()):
            self.assertEqual(db.git_main_checkout(sub), main)
            self.assertEqual(db.find_board(cwd=sub, env={}), board.path)
            opened = Board.open(cwd=sub)
        self.addCleanup(opened.close)
        self.assertEqual(opened.path, board.path)

    def test_git_missing_is_harmless(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("git")):
            self.assertIsNone(db.git_main_checkout(self.tmp))


class Connections(TempDir):
    def test_pragmas(self):
        board = self.board()
        conn = db.connect(board.path)
        self.addCleanup(conn.close)
        pragma = lambda name: conn.execute(f"PRAGMA {name}").fetchone()[0]  # noqa: E731
        self.assertEqual(pragma("journal_mode"), "wal")
        self.assertEqual(pragma("synchronous"), 1)
        self.assertEqual(pragma("foreign_keys"), 1)
        self.assertEqual(pragma("busy_timeout"), 10000)
        self.assertEqual(pragma("journal_size_limit"), 8388608)
        self.assertIsNone(conn.isolation_level)

    @unittest.skipUnless(db.NO_CKPT_ON_CLOSE is not None, "needs Python 3.12+ (setconfig)")
    def test_a_solitary_write_leaves_the_wal_for_the_next_process(self):
        board = self.board()
        path = board.path
        board.close()
        solo = Board.open(db=path, actor="owner")  # the only connection, like one CLI command
        key = solo.create_issue("last write")["key"]
        solo.close()
        self.assertGreater(os.path.getsize(path + "-wal"), 0)  # not checkpointed on close
        self.assertTrue(os.path.exists(path + "-shm"))
        copy_dir = os.path.join(self.tmp, "copy", ".earmark")
        os.makedirs(copy_dir)
        for suffix in ("", "-wal", "-shm"):  # a board copied with its -wal and -shm
            shutil.copy(path + suffix, os.path.join(copy_dir, "earmark.db" + suffix))
        copied = Board.open(db=copy_dir, actor="owner")
        self.addCleanup(copied.close)
        self.assertEqual(copied.get_issue(key)["title"], "last write")
        again = Board.open(db=path, actor="owner")
        self.addCleanup(again.close)
        self.assertEqual([c["name"] for c in again.doctor() if not c["ok"]], [])
        self.assertEqual([i["key"] for i in again.export()["issues"]], [key])
        copy = again.backup(os.path.join(self.tmp, "bk"))["path"]
        with sqlite3.connect(copy) as plain:
            self.assertEqual(plain.execute("SELECT title FROM issues").fetchall(), [("last write",)])

    def test_write_commits_and_rolls_back(self):
        board = self.board()
        conn = db.connect(board.path)
        self.addCleanup(conn.close)
        with db.write(conn):
            conn.execute("INSERT INTO meta(key, value) VALUES ('x', '1')")
        with self.assertRaises(RuntimeError):
            with db.write(conn):
                conn.execute("INSERT INTO meta(key, value) VALUES ('y', '1')")
                raise RuntimeError("boom")
        keys = {r[0] for r in conn.execute("SELECT key FROM meta")}
        self.assertIn("x", keys)
        self.assertNotIn("y", keys)
        self.assertFalse(conn.in_transaction)

    def test_busy_after_three_retries(self):
        board = self.board()
        holder = db.connect(board.path)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        self.addCleanup(lambda: holder.in_transaction and holder.execute("ROLLBACK"))
        waiter = db.connect(board.path, busy_timeout_ms=20)
        self.addCleanup(waiter.close)
        sleeps = []
        with self.assertRaises(Busy) as ctx:
            with db.write(waiter, sleep=sleeps.append):
                self.fail("must not get the lock")
        self.assertEqual(sleeps, [0, 1, 2])
        self.assertTrue(ctx.exception.retriable)
        self.assertEqual(ctx.exception.exit_code, 5)

    def test_retry_succeeds_once_the_lock_frees(self):
        board = self.board()
        holder = db.connect(board.path)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        waiter = db.connect(board.path, busy_timeout_ms=20)
        self.addCleanup(waiter.close)
        with db.write(waiter, sleep=lambda attempt: holder.execute("ROLLBACK")):
            waiter.execute("INSERT INTO meta(key, value) VALUES ('after', 'retry')")
        self.assertEqual(waiter.execute("SELECT value FROM meta WHERE key = 'after'").fetchone()[0],
                         "retry")

    def test_old_sqlite_is_refused_clearly(self):
        with mock.patch.object(sqlite3, "sqlite_version_info", (3, 31, 1)):
            with self.assertRaises(InternalError) as ctx:
                db.check_sqlite()
        self.assertIn("3.35", ctx.exception.message)


class Migrations(TempDir):
    def test_schema_is_v1_and_complete(self):
        statements = db.split_sql(db.schema_sql())
        code = ["\n".join(l for l in s.splitlines() if not l.lstrip().startswith("--")).strip()
                for s in statements]
        self.assertTrue(all(c.upper().startswith("CREATE") for c in code))
        self.assertEqual(len(code), db.schema_sql().count("CREATE "))
        board = self.board()
        self.assertEqual(db.schema_version(board.conn), 1)
        self.assertEqual(db.migrate(board.conn), 1)

    def test_numbered_migration_applies_once(self):
        board = self.board()
        calls = []

        def v2(conn):
            calls.append(1)
            conn.execute("CREATE TABLE extra (x INTEGER)")

        with mock.patch.dict(db.MIGRATIONS, {2: v2}):
            self.assertEqual(db.migrate(board.conn, target=2), 2)
            self.assertEqual(db.migrate(board.conn, target=2), 2)
        self.assertEqual(calls, [1])
        self.assertEqual(db.schema_version(board.conn), 2)
        with self.assertRaises(InvalidRequest):
            db.open_board(board.path)

    def test_failed_migration_rolls_back(self):
        board = self.board()

        def broken(conn):
            conn.execute("CREATE TABLE half (x INTEGER)")
            raise RuntimeError("bad migration")

        with mock.patch.dict(db.MIGRATIONS, {2: broken}):
            with self.assertRaises(RuntimeError):
                db.migrate(board.conn, target=2)
        self.assertEqual(db.schema_version(board.conn), 1)
        self.assertIsNone(board.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'half'").fetchone())

    def test_non_board_files(self):
        path = os.path.join(self.tmp, "plain.db")
        sqlite3.connect(path).execute("CREATE TABLE t (x)").connection.close()
        with self.assertRaises(NoBoard):
            db.open_board(path)
        junk = os.path.join(self.tmp, "junk.db")
        with open(junk, "wb") as fh:
            fh.write(b"not a database at all" * 100)
        with self.assertRaises(NoBoard):
            db.open_board(junk)


class Init(TempDir):
    def test_layout_meta_owner_and_profiles(self):
        board = self.board(prefix="GAME", owner="hector")
        board_dir = os.path.dirname(board.path)
        with open(os.path.join(board_dir, ".gitignore")) as fh:
            self.assertEqual(fh.read().strip(), "*")
        fresh = sqlite3.connect(board.path)
        self.addCleanup(fresh.close)
        self.assertEqual(fresh.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        meta = dict(fresh.execute("SELECT key, value FROM meta"))
        self.assertEqual(meta["schema_version"], "1")
        self.assertEqual(meta["prefix"], "GAME")
        self.assertEqual(meta["owner"], "hector")
        self.assertEqual(meta["require_review"], "1")
        self.assertEqual(meta["lease_minutes"], "120")
        self.assertRegex(meta["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        owner = fresh.execute("SELECT kind, online FROM agents WHERE handle = 'hector'").fetchone()
        self.assertEqual(owner, ("human", 1))
        self.assertEqual(board.actor, "hector")
        roles = {p["slug"]: p["role"] for p in board.profiles()}
        self.assertEqual({k: roles[k] for k in ("worker", "reviewer", "pm")},
                         {"worker": "worker", "reviewer": "reviewer", "pm": "pm"})
        self.assertTrue(all(p["prompt"] for p in board.profiles()))

    def test_defaults(self):
        root = os.path.join(self.tmp, "my-game")
        os.makedirs(root)
        with mock.patch("earmark.board.default_owner", return_value="ada"):
            board = Board.init(root, require_review=False)
        self.addCleanup(board.close)
        self.assertEqual((board.prefix, board.owner, board.require_review), ("MYGAME", "ada", False))
        digits = os.path.join(self.tmp, "2048")
        os.makedirs(digits)
        other = Board.init(digits, owner="ada")
        self.addCleanup(other.close)
        self.assertEqual(other.prefix, "EM")

    def test_init_twice_and_bad_input(self):
        board = self.board()
        with self.assertRaises(InvalidRequest):
            Board.init(os.path.dirname(os.path.dirname(board.path)), owner="x")
        with self.assertRaises(InvalidRequest):
            Board.init(os.path.join(self.tmp, "nope"), owner="x")
        os.makedirs(os.path.join(self.tmp, "bad"))
        with self.assertRaises(InvalidRequest):
            Board.init(os.path.join(self.tmp, "bad"), prefix="9X", owner="x")

    def test_failed_seed_leaves_no_half_board(self):
        root = os.path.join(self.tmp, "half")
        os.makedirs(root)
        with mock.patch.object(seed, "profile_seeds", side_effect=RuntimeError("bad seed")):
            with self.assertRaises(RuntimeError):
                Board.init(root, owner="x")
        self.assertFalse(os.path.exists(os.path.join(root, ".earmark", "earmark.db")))
        Board.init(root, owner="x").close()

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_owner_from_git_then_user_then_owner(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.name", "Ada Lovelace")
        with mock.patch.dict(os.environ, _git_env()):
            os.environ.pop("GIT_AUTHOR_NAME")
            self.assertEqual(default_owner(repo), "ada-lovelace")
            plain = os.path.join(self.tmp, "plain")
            os.makedirs(plain)
            with mock.patch.dict(os.environ, {"USER": "Grace H"}):
                self.assertEqual(default_owner(plain), "grace-h")
            with mock.patch.dict(os.environ, {"USER": "", "USERNAME": ""}):
                self.assertEqual(default_owner(plain), "owner")

    def test_restored_backup_returns_to_wal(self):
        board = self.board()
        board.create_issue("x")
        copy = board.backup(os.path.join(self.tmp, "bk"))["path"]
        with sqlite3.connect(copy) as plain:
            self.assertEqual(plain.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        restored = os.path.join(self.tmp, "restored", ".earmark")
        os.makedirs(restored)
        shutil.copy(copy, os.path.join(restored, "earmark.db"))
        again = Board.open(db=restored, actor="owner")
        self.addCleanup(again.close)
        again.create_issue("y")
        self.assertEqual(again.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertTrue([c for c in again.doctor() if c["name"] == "wal"][0]["ok"])

    def test_init_publishes_only_a_complete_board(self):
        root = os.path.join(self.tmp, "atomic")
        os.makedirs(root)
        board_dir = os.path.join(root, ".earmark")
        seen = []

        def spy(conn, **kw):
            seen.append(os.path.exists(os.path.join(board_dir, "earmark.db")))
            raise RuntimeError("seed fails")

        with mock.patch("earmark.board._seed_board", side_effect=spy):
            with self.assertRaises(RuntimeError):
                Board.init(root, owner="x")
        self.assertEqual(seen, [False])  # never visible under its real name while building
        self.assertEqual(sorted(os.listdir(board_dir)), [".gitignore", "files"])
        with open(os.path.join(board_dir, ".gitignore"), "w") as fh:
            fh.write("custom\n")
        Board.init(root, owner="x").close()
        with open(os.path.join(board_dir, ".gitignore")) as fh:
            self.assertEqual(fh.read(), "custom\n")  # an existing file is never rewritten

    def test_init_without_hard_links_still_refuses_an_existing_board(self):
        root = os.path.join(self.tmp, "nolink")
        os.makedirs(root)
        with mock.patch("os.link", side_effect=PermissionError("no hard links here")):
            Board.init(root, owner="x").close()
            path = os.path.join(root, ".earmark", "earmark.db")
            with self.assertRaises(InvalidRequest) as ctx:
                db.create(path)
        self.assertIn("a board already exists", ctx.exception.message)
        self.assertEqual([f for f in os.listdir(os.path.dirname(path)) if f.startswith(".init")], [])

    def test_open_is_cheap(self):
        board = self.board()
        for _ in range(200):
            board.create_issue("filler")
        started = time.perf_counter()
        for _ in range(20):
            Board.open(db=board.path, actor="owner").close()
        per_open_ms = (time.perf_counter() - started) * 1000 / 20
        self.assertLess(per_open_ms, 25)


class Seeds(unittest.TestCase):
    def test_frontmatter(self):
        meta, body = seed.parse_frontmatter("---\nname: UI lead\nrole: worker\nicon: 'brush'\n---\n"
                                            "Prompt line 1\nline 2\n")
        self.assertEqual(meta, {"name": "UI lead", "role": "worker", "icon": "brush"})
        self.assertEqual(body, "Prompt line 1\nline 2")
        self.assertEqual(seed.parse_frontmatter("no frontmatter"), ({}, "no frontmatter"))
        self.assertEqual(seed.parse_frontmatter("---\nname: x\nunterminated"),
                         ({}, "---\nname: x\nunterminated"))

    def test_builtin_fallback_when_files_are_missing(self):
        with mock.patch.object(seed, "_profiles_dir", return_value=None):
            seeds = {s["slug"]: s for s in seed.profile_seeds()}
            self.assertEqual(sorted(seeds), ["pm", "reviewer", "worker"])
            self.assertTrue(all(s["prompt"] for s in seeds.values()))
            for role in ("worker", "reviewer", "pm"):
                self.assertIn("earmark", seed.loop_text(role))
            self.assertEqual(seed.loop_text(None), seed.BUILTIN_LOOPS["worker"])

    def test_package_seeds_are_valid(self):
        for item in seed.profile_seeds():
            self.assertIn(item["role"], ("worker", "reviewer", "pm"))
            self.assertTrue(item["name"])


if __name__ == "__main__":
    unittest.main()
