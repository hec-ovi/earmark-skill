"""Many processes on one board: 12 workers loop next → comment → review until empty while
4 reviewers approve. Every issue is claimed once, no busy error surfaces, comment ids stay
unique and ordered per issue. Eight processes racing `init` in one directory: exactly one
wins, every loser gets "a board already exists", and the winner's board stays whole."""

from __future__ import annotations

import multiprocessing
import os
import shutil
import tempfile
import time
import traceback
import unittest

from tests.support import make_board

from earmark.board import Board
from earmark.errors import Busy, Empty

WORKERS = 12
REVIEWERS = 4
ISSUES = 72
DEADLINE_S = 120


def _worker(path: str, handle: str, start, results) -> None:
    out = {"handle": handle, "claimed": [], "busy": 0, "errors": []}
    try:
        board = Board.open(db=path)
        board.join("worker", handle=handle, cli="stress")
        start.wait(DEADLINE_S)
        while True:
            try:
                issue = board.next()
            except Empty:
                break
            except Busy:
                out["busy"] += 1
                continue
            key = issue["key"]
            out["claimed"].append(key)
            board.add_comment(key, f"progress by {handle}", kind="progress")
            board.submit_review(key, note=f"handoff by {handle}: run the tests")
        board.leave()
        board.close()
    except Exception:  # reported to the parent, which fails the test
        out["errors"].append(traceback.format_exc())
    results.put(out)


def _reviewer(path: str, handle: str, start, results) -> None:
    out = {"handle": handle, "approved": [], "busy": 0, "errors": []}
    try:
        board = Board.open(db=path)
        board.join("reviewer", handle=handle, cli="stress")
        start.wait(DEADLINE_S)
        deadline = time.monotonic() + DEADLINE_S
        while time.monotonic() < deadline:
            try:
                issue = board.next()
            except Empty:
                if board.list_issues(status="open", limit=0)["total"] == 0:
                    break
                time.sleep(0.01)
                continue
            except Busy:
                out["busy"] += 1
                continue
            board.approve(issue["key"], note=f"verified by {handle}")
            out["approved"].append(issue["key"])
        board.close()
    except Exception:
        out["errors"].append(traceback.format_exc())
    results.put(out)


INIT_RACERS = 8
INIT_TRIALS = 30


def _init_racer(dirs: list, barrier, results) -> None:
    out = []
    for directory in dirs:
        barrier.wait(DEADLINE_S)
        try:
            Board.init(directory, prefix="RACE", owner="owner").close()
            out.append(("ok", None))
        except Exception as exc:  # the parent checks every outcome
            out.append((type(exc).__name__, getattr(exc, "message", str(exc))))
    results.put(out)


class InitRace(unittest.TestCase):
    def test_concurrent_init_has_one_winner_and_a_whole_board(self):
        tmp = tempfile.mkdtemp(prefix="earmark-init-race-")
        self.addCleanup(shutil.rmtree, tmp, True)
        dirs = []
        for n in range(INIT_TRIALS):
            dirs.append(os.path.join(tmp, f"trial-{n}"))
            os.makedirs(dirs[-1])
        ctx = multiprocessing.get_context("spawn")
        barrier, results = ctx.Barrier(INIT_RACERS), ctx.Queue()
        procs = [ctx.Process(target=_init_racer, args=(dirs, barrier, results))
                 for _ in range(INIT_RACERS)]
        for proc in procs:
            proc.start()
        outcomes = [results.get(timeout=DEADLINE_S + 30) for _ in procs]
        for proc in procs:
            proc.join(30)
        self.assertEqual([p.exitcode for p in procs], [0] * INIT_RACERS)
        for n, directory in enumerate(dirs):
            trial = [o[n] for o in outcomes]
            with self.subTest(trial=n):
                self.assertEqual(sum(1 for kind, _ in trial if kind == "ok"), 1, trial)
                for kind, message in trial:
                    if kind != "ok":
                        self.assertEqual(kind, "InvalidRequest", message)
                        self.assertIn("a board already exists at", message)
                board_dir = os.path.join(directory, ".earmark")
                leftovers = [f for f in os.listdir(board_dir) if f.startswith(".init")]
                self.assertEqual(leftovers, [])
                board = Board.open(db=board_dir, actor="owner")
                try:
                    self.assertEqual(board.prefix, "RACE")
                    self.assertEqual(board.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                    slugs = {p["slug"] for p in board.profiles()}
                    self.assertTrue({"worker", "reviewer", "pm"} <= slugs)
                    self.assertEqual([a["handle"] for a in board.team()["agents"]], ["owner"])
                    self.assertEqual(board.create_issue("works")["key"], "RACE-1")
                finally:
                    board.close()


class Stress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="earmark-stress-")
        self.board = make_board(self.tmp, "LOAD")
        self.keys = [self.board.create_issue(f"job {n}", priority=["high", "medium", "low"][n % 3])["key"]
                     for n in range(ISSUES)]

    def tearDown(self):
        self.board.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parallel_workers_and_reviewers(self):
        ctx = multiprocessing.get_context("spawn")
        start, results = ctx.Event(), ctx.Queue()
        procs = [ctx.Process(target=_worker, args=(self.board.path, f"w{n}", start, results))
                 for n in range(WORKERS)]
        procs += [ctx.Process(target=_reviewer, args=(self.board.path, f"r{n}", start, results))
                  for n in range(REVIEWERS)]
        for proc in procs:
            proc.start()
        started = time.monotonic()
        start.set()
        outcomes = [results.get(timeout=DEADLINE_S + 30) for _ in procs]
        for proc in procs:
            proc.join(30)
        elapsed = time.monotonic() - started
        self.assertEqual([p.exitcode for p in procs], [0] * len(procs))
        self.assertEqual([e for o in outcomes for e in o["errors"]], [])
        self.assertEqual(sum(o["busy"] for o in outcomes), 0)

        claimed = [k for o in outcomes if "claimed" in o for k in o["claimed"]]
        self.assertEqual(sorted(claimed), sorted(self.keys))
        approved = [k for o in outcomes if "approved" in o for k in o["approved"]]
        self.assertEqual(sorted(approved), sorted(self.keys))
        self.assertGreater(len({o["handle"] for o in outcomes if o.get("claimed")}), 1)

        conn = self.board.conn
        claims = conn.execute("SELECT issue_id, count(*) FROM events WHERE action = 'claim' "
                              "AND new LIKE 'w%' GROUP BY issue_id").fetchall()
        self.assertEqual(sorted(n for _, n in claims), [1] * ISSUES)
        starts = conn.execute("SELECT count(*) FROM events WHERE action = 'status' AND "
                              "old = 'todo' AND new = 'in_progress'").fetchone()[0]
        self.assertEqual(starts, ISSUES)
        statuses = {r[0] for r in conn.execute("SELECT DISTINCT status FROM issues")}
        self.assertEqual(statuses, {"done"})
        owners = {r[0]: r[1] for r in conn.execute("SELECT id, assignee FROM issues")}
        by_handle = {k: o["handle"] for o in outcomes for k in o.get("claimed", [])}
        self.assertEqual({int(k.split("-")[1]): h for k, h in by_handle.items()}, owners)

        ids = [r[0] for r in conn.execute("SELECT id FROM comments ORDER BY id")]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), ISSUES * 3)
        for issue_id in owners:
            rows = conn.execute("SELECT id, kind FROM comments WHERE issue_id = ? ORDER BY id",
                                (issue_id,)).fetchall()
            self.assertEqual([k for _, k in rows], ["progress", "handoff", "approval"])
            self.assertEqual([i for i, _ in rows], sorted({i for i, _ in rows}))
        if os.environ.get("EARMARK_PERF_QUIET") != "1":
            print(f"\n[stress] {WORKERS} workers + {REVIEWERS} reviewers, {ISSUES} issues, "
                  f"{ISSUES * 5} writes in {elapsed:.2f} s", flush=True)


if __name__ == "__main__":
    unittest.main()
