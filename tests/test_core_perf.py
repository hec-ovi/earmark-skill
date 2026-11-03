"""Measurements: bytes written for 1,000 comment writes; list_issues on 2,000 issues;
pending_changes (the dirty hint) on 2,000 issues and 10,000 comments.

Results print to stderr; set EARMARK_PERF_QUIET=1 to silence them.
"""

from __future__ import annotations

import os
import random
import shutil
import statistics
import sys
import tempfile
import time
import unittest

from tests.support import make_board

from earmark import db, timeutil
from earmark.board import Board

QUIET = os.environ.get("EARMARK_PERF_QUIET") == "1"


def report(line: str) -> None:
    if not QUIET:
        print(f"\n[perf] {line}", file=sys.stderr)


def wchar() -> int | None:
    """Bytes this process passed to write syscalls (Linux), or None elsewhere."""
    try:
        with open("/proc/self/io", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("wchar:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def timed(fn, runs: int = 7) -> float:
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples)


class Measure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="earmark-perf-")
        timeutil.reset_clock()
        self.board = make_board(self.tmp)

    def tearDown(self):
        self.board.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bytes_written_for_1000_comments(self):
        b = self.board
        keys = [b.create_issue(f"issue {n}")["key"] for n in range(20)]
        b.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        page = b.conn.execute("PRAGMA page_size").fetchone()[0]
        before = wchar()
        for n in range(1000):
            b.add_comment(keys[n % len(keys)], f"progress note {n}: moved the widget state @owner",
                          kind="progress")
        written = wchar()
        if before is not None:
            total = written - before
            report(f"1,000 comment writes (default autocheckpoint): {total:,} bytes written to "
                   f"db+wal ({total / 1000:,.0f} bytes per comment, page size {page})")
            self.assertLess(total / 1000, 64 * 1024)
        conn = db.connect(b.path)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        b.conn.execute("PRAGMA wal_autocheckpoint=0")
        for n in range(1000):
            b.add_comment(keys[n % len(keys)], f"second round {n}", kind="note")
        wal = os.path.getsize(b.path + "-wal")
        frames = (wal - 32) // (page + 24)
        before = wchar()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        after = wchar()
        checkpoint = (after - before) if before is not None else None
        report(f"1,000 comment writes (no checkpoint): WAL {wal:,} bytes = {frames:,} frames "
               f"({frames / 1000:.1f} pages per comment); checkpoint into the db file: "
               f"{checkpoint if checkpoint is None else f'{checkpoint:,}'} bytes")
        self.assertLess(frames / 1000, 12)

    def test_list_issues_on_2000_issues(self):
        b = self.board
        self._seed(2000)
        open_default = timed(lambda: b.list_issues())
        everything = timed(lambda: b.list_issues(status="all", limit=2000), runs=5)
        filtered = timed(lambda: b.list_issues(topic=["area-1"], label=["ui"], sort="updated", desc=True))
        ready = timed(lambda: b.list_issues(ready=True, assignee="none", limit=100))
        count = b.list_issues(status="all", limit=2000)
        self.assertEqual((count["total"], len(count["items"])), (2000, 2000))
        report(f"list_issues on 2,000 issues: default page {open_default:.1f} ms, all 2,000 rows "
               f"{everything:.1f} ms, topic+label filter {filtered:.1f} ms, ready+unassigned "
               f"{ready:.1f} ms (median)")
        self.assertLess(open_default, 50)
        self.assertLess(filtered, 50)
        self.assertLess(everything, 250)

    def test_pending_changes_on_2000_issues_and_10000_comments(self):
        """The dirty hint runs after every write command: it must stay well under a millisecond."""
        b = self.board
        b.join("worker", handle="w1")
        b.set_actor("owner")
        self._seed(2000)
        rng = random.Random(11)
        ts = timeutil.now_iso()
        others = ["w2", "w3", "r1", "pm-1"]
        with db.write(b.conn) as conn:
            for handle in others:
                conn.execute("INSERT INTO agents(handle, kind, profile, cli, online, joined_at, "
                             "last_seen) VALUES (?, 'agent', 'worker', 'perf', 1, ?, ?)", (handle, ts, ts))
            conn.execute("UPDATE issues SET assignee = 'w1' WHERE id % 15 = 0")
            conn.executemany("INSERT INTO events(issue_id, actor, action, old, new, at) "
                             "VALUES (?, ?, ?, 'a', 'b', ?)",
                             [(rng.randrange(1, 2001), rng.choice(others),
                               rng.choice(["status", "claim", "title", "assignee", "rank"]), ts)
                              for _ in range(8000)])
            rows = [(rng.randrange(1, 2001), rng.choice(others), ts) for _ in range(6000)]
            conn.executemany("INSERT INTO comments(issue_id, author, kind, body, created_at) "
                             "VALUES (?, ?, 'note', 'ping @w1', ?)", rows)
            conn.execute("INSERT INTO mentions(comment_id, handle) SELECT id, 'w1' FROM comments "
                         "WHERE id % 40 = 0")
        b.conn.execute("DROP TABLE IF EXISTS sqlite_stat1")  # boards never run ANALYZE
        comments = b.conn.execute("SELECT count(*) FROM comments").fetchone()[0]
        self.assertGreaterEqual(comments, 10000)
        heads = b.heads()
        b.close()
        self.board = b = Board.open(db=b.path, actor="w1")  # a fresh connection, as each command
        results = {}
        for name, behind in (("fresh", 0), ("200 events behind", 200), ("never checked", None)):
            ev = 0 if behind is None else heads["event_id"] - behind
            cm = 0 if behind is None else heads["comment_id"] - behind // 2
            with db.write(b.conn) as conn:
                conn.execute("UPDATE cursors SET last_event_id = ?, last_comment_id = ? "
                             "WHERE agent = 'w1' AND scope = 'check'", (ev, cm))
            count = b.pending_changes()
            per_call = timed(lambda: [b.pending_changes() for _ in range(200)], runs=5) / 200
            feed = b.check(peek=True)
            self.assertEqual(count, len(feed["changed"]) + feed["more"], name)
            results[name] = (per_call, count)
        report("pending_changes on 2,000 issues / {:,} comments / {:,} events, 133 issues "
               "assigned: ".format(comments, heads["event_id"]) + ", ".join(
                   f"{name} {ms:.3f} ms ({n} issues)" for name, (ms, n) in results.items()))
        self.assertLess(results["fresh"][0], 2)
        self.assertLess(results["200 events behind"][0], 2)
        self.assertLess(results["never checked"][0], 10)

    def _seed(self, n: int) -> None:
        """Bulk rows through SQL in one transaction (setup only; the reads are what we time)."""
        b = self.board
        rng = random.Random(7)
        for area in range(5):
            for sub in range(4):
                b.add_topic(f"area-{area}/sub-{sub}")
        topic_ids = [r[0] for r in b.conn.execute("SELECT id FROM topics")]
        ts = timeutil.now_iso()
        statuses = ["todo"] * 5 + ["backlog", "in_progress", "in_review", "blocked", "done", "canceled"]
        with db.write(b.conn) as conn:
            rows = [(f"Issue {i}", "body text " * 5, rng.choice(["bug", "task", "feature"]),
                     rng.choice(statuses), rng.randrange(5), rng.choice(topic_ids), "owner",
                     float(i * 1024), ts, ts) for i in range(1, n + 1)]
            conn.executemany("INSERT INTO issues(title, body, type, status, priority, topic_id, "
                             "reporter, rank, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            conn.executemany("INSERT INTO labels(issue_id, label) VALUES (?, ?)",
                             [(i, rng.choice(["ui", "api", "perf"])) for i in range(1, n + 1)])
            conn.executemany("INSERT INTO comments(issue_id, author, kind, body, created_at) "
                             "VALUES (?, 'owner', 'note', 'c', ?)",
                             [(rng.randrange(1, n + 1), ts) for _ in range(n * 2)])
            conn.executemany("INSERT OR IGNORE INTO links(src, dst, kind, created_by, created_at) "
                             "VALUES (?, ?, 'blocks', 'owner', ?)",
                             [(i, i + 1, ts) for i in range(1, n, 7)])
            conn.executemany("UPDATE issues SET parent_id = ? WHERE id = ?",
                             [(i - 1, i) for i in range(2, n + 1, 11)])
        b.conn.execute("ANALYZE")


if __name__ == "__main__":
    unittest.main()
