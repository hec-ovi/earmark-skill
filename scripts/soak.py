#!/usr/bin/env python3
"""Soak and performance harness: many agent processes drive the real earmark CLI on one board.

Every agent is its own OS process. It runs the skill's loop by calling the CLI exactly as an
agent shell does, one subprocess per command (`<python> <repo>/earmark --db B --as H ...`):
workers `next` (the issue and its unseen comments) -> 1-3 progress comments -> `review --next`
(hand off and claim the next issue in one call), then `next --review` when nothing is ready;
reviewers `next --review` -> `approve --next` or `reject --next` (about 20 % rejected, so
rework happens). A command that ends with `· N changes for you` is followed by `earmark check`;
idle agents run `check`, `ls --count` and `earmark wait`; everyone reads `show`, `ls`, `team`
or `topics` now and then.

    python3 scripts/soak.py                                   # 12 workers + 4 reviewers, until empty
    python3 scripts/soak.py --agents 6 --reviewers 2 --think 2-6 --minutes 10
    python3 scripts/soak.py --reads                           # read latency, 2,000 issues
    python3 scripts/soak.py --json > soak.json                # machine-readable result on stdout

Measured per command: wall latency (process start to exit), exit code, bytes written and peak
RSS of the CLI process (Linux: wait4() rusage; ru_oublock is the kernel's write_bytes / 512,
the counter /proc/<pid>/io shows, which cannot be read once the child has exited). After the
run: board invariants from the events and comments tables, file growth of earmark.db and its
WAL, the largest WAL seen, and the idle check (`earmark ui` with an open /api/stream for
--idle seconds: earmark.db, -wal and -shm must keep their size and mtime).

Boards live in a fresh temporary directory, never inside the repository. The default base is
/var/tmp when it sits on a disk (tmpfs has no block writes to count), else the system temp
directory; --dir chooses another base. Standard library only, Python 3.10+.
"""

from __future__ import annotations

import argparse
import itertools
import json
import multiprocessing
import os
import platform
import queue
import random
import re
import selectors
import shutil
import signal
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
LINUX = sys.platform.startswith("linux")
JOURNAL_SIZE_LIMIT = 8 * 1024 * 1024  # PRAGMA journal_size_limit set by earmark/db.py

AREAS = ["api", "engine", "ui-ux", "audio", "text-content", "tools"]
TOPICS = ["api/auth", "api/matchmaking", "engine/rendering", "engine/physics", "ui-ux/hud",
          "ui-ux/settings", "audio/sfx", "text-content/quests", "tools/build"]
VERBS = ["Fix", "Add", "Refactor", "Speed up", "Document", "Test", "Remove", "Port"]
THINGS = ["token refresh", "match queue", "shadow cascade", "ragdoll joints", "minimap zoom",
          "key rebinding", "footstep mixer", "quest journal", "asset cache", "save migration"]
PRIORITIES = ["urgent", "high", "medium", "medium", "low", "none"]
TYPES = ["bug", "feature", "task", "chore"]
KEY_RE = re.compile(r"^([A-Z][A-Z0-9]*-\d+)  \S", re.MULTILINE)  # issue header of next/show
HINT_RE = re.compile(r"changes? for you: earmark check")
COMMENT_RE = re.compile(r"^#(\d+)\b", re.MULTILINE)
COUNT_RE = re.compile(r"·\s*(\d+)\s+comments?\b")


# ---- one CLI call ---------------------------------------------------------------------------

def cli_env() -> dict:
    """The caller's environment without EARMARK_* so --db/--as alone decide board and actor."""
    return {k: v for k, v in os.environ.items() if not k.startswith("EARMARK_")}


# Runs the launcher in the same interpreter and, at exit, hands /proc/self/io to the harness
# through an inherited pipe: write_bytes, cancelled_write_bytes (dirty pages dropped by unlink
# or truncate before writeback, such as a deleted -shm), wchar and syscw, plus VmHWM from
# /proc/self/status, the peak RSS of the CLI's own address space (wait4's ru_maxrss also
# carries the parent's RSS at fork time, so it is only an upper bound). The CLI closes the
# board before returning, so the snapshot sees the close-time checkpoint; wait4() still gives
# the total, and `late` in each record is the difference. Adds about 2 ms per call
# (`--reads` measures it as `ls (no wrapper)`).
WRAPPER = (
    "import atexit, os, sys\n"
    "fd = int(os.environ.pop('SOAK_IO_FD'))\n"
    "def _io():\n"
    "    with open('/proc/self/io', 'rb') as fh:\n"
    "        data = fh.read()\n"
    "    with open('/proc/self/status', 'rb') as fh:\n"
    "        data += b''.join(l for l in fh if l.startswith(b'VmHWM:'))\n"
    "    os.write(fd, data)\n"
    "atexit.register(_io)\n"
    "sys.argv = sys.argv[1:]\n"
    "with open(sys.argv[0], 'rb') as fh:\n"
    "    code = compile(fh.read(), sys.argv[0], 'exec')\n"
    "exec(code, {'__name__': '__main__', '__file__': sys.argv[0], '__builtins__': __builtins__})\n"
)
WRAP = LINUX and os.path.exists("/proc/self/io")


def run(cmd: list[str], env: dict, timeout: float = 300.0, cwd: str | None = None,
        wrap: bool = False) -> dict:
    """Run one process to completion. Returns code, out, err, t0, t1 (monotonic), bytes
    written (wait4 rusage), peak RSS (KiB), CPU seconds and, with wrap (cmd = [python, cli,
    ...]), the child's own /proc/self/io counters and VmHWM at exit."""
    io_r = io_w = None
    if wrap:
        io_r, io_w = os.pipe()
        cmd = [cmd[0], "-c", WRAPPER] + cmd[1:]
        env = dict(env, SOAK_IO_FD=str(io_w))
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, cwd=cwd,
                                pass_fds=(io_w,) if wrap else ())
    finally:
        if io_w is not None:
            os.close(io_w)
    out, err, io_text = _drain(proc, t0 + timeout, io_r)
    _, status, ru = os.wait4(proc.pid, 0)
    t1 = time.monotonic()
    code = os.waitstatus_to_exitcode(status)
    proc.returncode = code  # reaped here; keep Popen from waiting again
    if LINUX:
        written, rss_kb = ru.ru_oublock * 512, ru.ru_maxrss
    else:  # macOS: ru_oublock counts operations, ru_maxrss is in bytes
        written, rss_kb = None, ru.ru_maxrss // 1024
    io = {}
    for line in io_text.splitlines():
        k, _, v = line.partition(":")
        v = v.strip().removesuffix(" kB")
        if v.isdigit():
            io[k.strip()] = int(v)
    if "VmHWM" in io:
        rss_kb = io["VmHWM"]
    return {"code": code, "out": out, "err": err, "t0": t0, "t1": t1, "written": written,
            "rss_kb": rss_kb, "cpu": ru.ru_utime + ru.ru_stime,
            "cancelled": io.get("cancelled_write_bytes"), "wchar": io.get("wchar"),
            "io_write_bytes": io.get("write_bytes")}


def rec(op: str, r: dict) -> tuple:
    """Compact record of one call: (op, t0, t1, code, written, rss_kib, cpu_s, cancelled, wchar,
    late), late = bytes wait4 counted after the child's own exit snapshot (expected 0)."""
    late = (r["written"] - r["io_write_bytes"]) if r["written"] is not None and \
        r["io_write_bytes"] is not None else None
    return (op, r["t0"], r["t1"], r["code"], r["written"], r["rss_kb"], r["cpu"], r["cancelled"],
            r["wchar"], late)


def _drain(proc: subprocess.Popen, deadline: float, io_fd: int | None = None) -> tuple[str, str, str]:
    bufs: dict = {proc.stdout.fileno(): [], proc.stderr.fileno(): []}
    if io_fd is not None:
        bufs[io_fd] = []
    sel = selectors.DefaultSelector()
    for fd in bufs:
        sel.register(fd, selectors.EVENT_READ)
    while sel.get_map():
        left = deadline - time.monotonic()
        if left <= 0:
            proc.kill()
            left = 5.0
        for key, _ in sel.select(left):
            chunk = os.read(key.fd, 65536)
            if chunk:
                bufs[key.fd].append(chunk)
            else:
                sel.unregister(key.fd)
    sel.close()
    text = [b"".join(bufs[fd]).decode("utf-8", "replace")
            for fd in (proc.stdout.fileno(), proc.stderr.fileno())]
    proc.stdout.close()
    proc.stderr.close()
    if io_fd is not None:
        text.append(b"".join(bufs[io_fd]).decode("ascii", "replace"))
        os.close(io_fd)
    else:
        text.append("")
    return text[0], text[1], text[2]


# ---- agents ---------------------------------------------------------------------------------

class Agent:
    """One simulated coding agent; every board access is a CLI subprocess."""

    def __init__(self, cfg: dict, handle: str, role: str, seed: int) -> None:
        self.cfg, self.handle, self.role = cfg, handle, role
        self.rng = random.Random(seed)
        self.env = cli_env()
        self.base = [cfg["python"], cfg["cli"], "--db", cfg["db"], "--as", handle]
        self.calls: list[tuple] = []        # (op, t0, t1, code, written, rss_kb, cpu)
        self.holds: list[tuple] = []        # (key, seat, t_claimed, t_released)
        self.comment_ids: list[tuple] = []  # (id, key)
        self.unexpected: list[tuple] = []   # (op, code, first stderr line)
        self.first_call = True

    def call(self, op: str, args: list[str], ok: tuple = (0,)) -> dict:
        lo, hi = self.cfg["think"]
        if hi > 0 and not self.first_call:
            time.sleep(self.rng.uniform(lo, hi))
        self.first_call = False
        r = run(self.base + args, self.env, cwd=self.cfg["cwd"], wrap=self.cfg["wrap"])
        self.calls.append(rec(op, r))
        if r["code"] not in ok and r["code"] not in (3, 5):
            line = (r["err"].strip().splitlines() or r["out"].strip().splitlines() or [""])[0]
            self.unexpected.append((op, r["code"], line[:300]))
            if len(self.unexpected) > 50:
                raise RuntimeError(f"{self.handle}: more than 50 unexpected exit codes; stopping")
        if r["code"] not in ok:
            time.sleep(0.05)  # conflict, busy or error: do not spin
        return r

    def key_of(self, r: dict, chained: bool = False) -> str | None:
        """Key of the issue `next` printed; after `--next` the action's own line comes first."""
        out = r["out"].split("\n", 1)[1] if chained and "\n" in r["out"] else r["out"]
        m = KEY_RE.search(out)
        if m is None:
            self.unexpected.append(("parse", r["code"], r["out"][:120]))
        return m.group(1) if m else None

    def call_and_check(self, op: str, args: list[str], ok: tuple = (0,)) -> dict:
        """Run a command; when it ends with the change hint, run `earmark check` as the skill says."""
        r = self.call(op, args, ok)
        if HINT_RE.search(r["out"]):
            self.call("check", ["check"])
        return r

    # -- loops --------------------------------------------------------------------------
    # Each step returns what the agent holds next: (key, seat, claimed_at), "empty" when a
    # chained --next found nothing (exit 4), or None to start over with `next`.
    def claimed(self, r: dict, seat: str, chained: bool = False):
        if r["code"] == 0:
            key = self.key_of(r, chained)
            return (key, seat, r["t1"]) if key else None
        return "empty" if r["code"] == 4 else None

    def work(self, key: str, claimed_at: float):
        if self.rng.random() < 0.1:
            self.call("show", ["show", key])
        steps = self.rng.randint(1, 3)
        for n in range(1, steps + 1):
            text = f"step {n}/{steps}: moved the state into the store, tests for {key.lower()} pass"
            r = self.call_and_check("comment", ["comment", key, text, "--kind", "progress"])
            if r["code"] == 0:
                for cid in COMMENT_RE.findall(r["out"]):
                    self.comment_ids.append((int(cid), key))
        self.occasional_read()
        note = f"changed src/{key.lower()}.py · run pytest tests/test_{key.lower()}.py"
        r = self.call_and_check("review --next", ["review", key, "-m", note, "--next"], ok=(0, 4))
        self.holds.append((key, "work", claimed_at, r["t0"]))
        return self.claimed(r, "work", chained=True)

    def review(self, key: str, claimed_at: float):
        if self.rng.random() < 0.2:
            self.call("show", ["show", key])
        if self.rng.random() < self.cfg["reject_rate"]:
            note = f"1. src/{key.lower()}.py:42 expected the cached value, got a fresh read"
            r = self.call_and_check("reject --next", ["reject", key, "-m", note, "--next"], ok=(0, 4))
        else:
            r = self.call_and_check("approve --next", ["approve", key, "-m",
                                    "ran the tests and read the diff", "--next"], ok=(0, 4))
        self.holds.append((key, "review", claimed_at, r["t0"]))
        # --next claims in the role's default mode: a reviewer's next handoff, a worker's work
        return self.claimed(r, "review" if self.role == "reviewer" else "work", chained=True)

    def occasional_read(self) -> None:
        x = self.rng.random()
        if x < 0.2:
            self.call("ls", ["ls", "-t", self.rng.choice(AREAS), "--ready"])
        elif x < 0.3:
            self.call("team", ["team"])
        elif x < 0.35:
            self.call("topics", ["topics", "--depth", "2"])

    def idle(self) -> bool:
        """Skill steps 7-8: check, then stop when the board is empty, else wait. True = stop.
        `wait --for any` also wakes on unseen inbox comments, which this loop (like the skill)
        reads through `check`, so it would return at once; wait for work or reviews instead."""
        self.call("check", ["check"])
        r = self.call("ls --count", ["ls", "--count"])
        if r["code"] == 0 and r["out"].strip() == "0":
            return True
        mode = "review" if self.role == "reviewer" else "work"
        self.call("wait", ["wait", "--for", mode, "--timeout", f"{self.cfg['wait_s']}s"], ok=(0, 4))
        return False

    def loop(self, deadline: float) -> None:
        held = None
        while time.monotonic() < deadline:
            if isinstance(held, tuple):
                key, seat, at = held
                held = self.work(key, at) if seat == "work" else self.review(key, at)
                continue
            if held == "empty" and self.role == "reviewer":  # approve/reject --next found none
                held = None
                if self.idle():
                    return
                continue
            if self.role == "worker" and held != "empty":
                r = self.call_and_check("next", ["next"], ok=(0, 4))
                held = self.claimed(r, "work")
                if held != "empty":
                    continue
            r = self.call_and_check("next --review", ["next", "--review"], ok=(0, 4))
            held = self.claimed(r, "review")
            if held == "empty":
                held = None
                if self.idle():
                    return

    def result(self, error: str | None = None) -> dict:
        return {"handle": self.handle, "role": self.role, "calls": self.calls, "holds": self.holds,
                "comment_ids": self.comment_ids, "unexpected": self.unexpected[:20],
                "unexpected_total": len(self.unexpected), "error": error}


def agent_main(cfg: dict, handle: str, role: str, seed: int, ready, start, start_at,
               results) -> None:
    agent = Agent(cfg, handle, role, seed)
    error = None
    try:
        agent.call("join", ["join", role, "--cli", "soak"])
        with ready.get_lock():
            ready.value += 1
        agent.first_call = True
        start.wait()
        limit = cfg["minutes"] if cfg["minutes"] else cfg["max_minutes"]
        agent.loop(start_at.value + limit * 60)
        agent.first_call = True
        agent.call("leave", ["leave"])
    except Exception:  # noqa: BLE001 (reported to the parent, which fails the run)
        error = traceback.format_exc()
    results.put(agent.result(error))


# ---- machine and disk context -------------------------------------------------------------

def mount_of(path: str) -> dict:
    """Filesystem type, source and mount point holding path (Linux /proc/mounts)."""
    best = {"fstype": None, "source": None, "mount": None}
    real = os.path.realpath(path)
    try:
        with open("/proc/mounts", encoding="utf-8") as fh:
            for line in fh:
                src, mnt, fstype = line.split()[:3]
                mnt = mnt.replace("\\040", " ")
                if (real == mnt or real.startswith(mnt.rstrip("/") + "/")) and \
                        len(mnt) >= len(best["mount"] or ""):
                    best = {"fstype": fstype, "source": src, "mount": mnt}
    except OSError:
        pass
    return best


def block_stat_path(path: str) -> str | None:
    st = os.stat(path)
    candidate = f"/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}/stat"
    return candidate if os.path.exists(candidate) else None


def sectors_written(stat_path: str | None) -> int | None:
    if not stat_path:
        return None
    try:
        with open(stat_path, encoding="ascii") as fh:
            return int(fh.read().split()[6])
    except (OSError, IndexError, ValueError):
        return None


def machine(python: str, path: str) -> dict:
    info = {"os": f"{platform.system()} {platform.release()}", "cpu": platform.processor() or None,
            "threads": os.cpu_count(), "cores": None, "ram_gib": None}
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            cores, model = set(), None
            phys = core = None
            for line in fh:
                k, _, v = line.partition(":")
                k, v = k.strip(), v.strip()
                if k == "model name" and model is None:
                    model = v
                elif k == "physical id":
                    phys = v
                elif k == "core id":
                    core = v
                    cores.add((phys, core))
            info["cpu"] = model or info["cpu"]
            info["cores"] = len(cores) or None
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    info["ram_gib"] = round(int(line.split()[1]) / 1024 / 1024, 1)
    except OSError:
        pass
    probe = "import sys, sqlite3; print(sys.version.split()[0], sqlite3.sqlite_version)"
    out = subprocess.run([python, "-c", probe], capture_output=True, text=True, check=True)
    info["python"], info["sqlite"] = out.stdout.split()
    m = mount_of(path)
    info["filesystem"] = {"type": m["fstype"], "device": m["source"], "mount": m["mount"]}
    info["loadavg_start"] = loadavg()
    return info


def loadavg() -> list[float] | None:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:
        return None


class Monitor(threading.Thread):
    """Samples WAL size every 0.1 s and the 1-minute load average every second. stat() only:
    the harness never opens the board while agents run."""

    def __init__(self, db: str) -> None:
        super().__init__(daemon=True)
        self.db, self.stop_event = db, threading.Event()
        self.wal_max = 0
        self.db_max = 0
        self.loads: list[float] = []

    def run(self) -> None:
        tick = 0
        while not self.stop_event.is_set():
            self.wal_max = max(self.wal_max, _size(self.db + "-wal"))
            self.db_max = max(self.db_max, _size(self.db))
            if tick % 10 == 0:
                la = loadavg()
                if la:
                    self.loads.append(la[0])
            tick += 1
            self.stop_event.wait(0.1)

    def stop(self) -> None:
        self.stop_event.set()
        self.join(5)


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def file_state(db: str) -> dict:
    out = {}
    for suffix in ("", "-wal", "-shm"):
        try:
            st = os.stat(db + suffix)
            out["earmark.db" + suffix] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
        except OSError:
            out["earmark.db" + suffix] = None
    return out


# ---- statistics -----------------------------------------------------------------------------

def pct(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile of values (q in 0..100)."""
    if not values:
        return None
    s = sorted(values)
    pos = (len(s) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def op_table(calls: list[tuple]) -> dict:
    by_op: dict[str, list] = defaultdict(list)
    for c in calls:
        by_op[c[0]].append(c)
    table = {}
    for op, rows in sorted(by_op.items(), key=lambda kv: -len(kv[1])):
        ms = [(c[2] - c[1]) * 1000 for c in rows]
        written = [c[4] for c in rows if c[4] is not None]
        cancelled = [c[7] for c in rows if c[7] is not None]
        wchar = [c[8] for c in rows if c[8] is not None]
        net = sum(written) - sum(cancelled) if written and cancelled else None
        table[op] = {
            "n": len(rows),
            "p50_ms": pct(ms, 50), "p95_ms": pct(ms, 95), "p99_ms": pct(ms, 99), "max_ms": max(ms),
            "written_total": sum(written) if written else None,
            "written_per_call": (sum(written) / len(written)) if written else None,
            "cancelled_total": sum(cancelled) if cancelled else None,
            "net_per_call": (net / len(rows)) if net is not None else None,
            "wchar_per_call": (sum(wchar) / len(wchar)) if wchar else None,
            "rss_max_kib": max(c[5] for c in rows), "rss_p50_kib": pct([c[5] for c in rows], 50),
            "cpu_p50_ms": pct([c[6] * 1000 for c in rows], 50),
            "exit_codes": dict(sorted(Counter(c[3] for c in rows).items())),
        }
    return table


# ---- invariants ------------------------------------------------------------------------------

def check_invariants(db: str, outcomes: list[dict], until_empty: bool) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return _invariants(conn, outcomes, until_empty)
    finally:
        conn.close()


def _invariants(conn, outcomes: list[dict], until_empty: bool) -> dict:
    problems: list[str] = []
    statuses = dict(conn.execute("SELECT id, status FROM issues").fetchall())
    status_counts = dict(Counter(statuses.values()))

    # Replay the events table per issue: one worker seat and one reviewer seat each.
    state: dict[int, dict] = {}
    counts = Counter()
    approvals = Counter()
    rows = conn.execute("SELECT id, issue_id, actor, action, old, new FROM events WHERE issue_id "
                        "IS NOT NULL ORDER BY issue_id, id").fetchall()
    for _, issue, actor, action, old, new in rows:
        s = state.setdefault(issue, {"status": "todo", "worker": None, "reviewer": None,
                                     "left_worker": None, "left_reviewer": None})
        if action == "status":
            if old != s["status"]:
                problems.append(f"issue {issue}: status event {old}->{new} while replay has {s['status']}")
            # the review/approve/reject event of the same transaction follows this one
            s["left_worker"], s["left_reviewer"] = s["worker"], s["reviewer"]
            if old == "in_progress":
                s["worker"] = None
            if old == "in_review":
                s["reviewer"] = None
            s["status"] = new
        elif action == "claim":
            seat = {"in_progress": "worker", "in_review": "reviewer"}.get(s["status"])
            if seat is None:
                problems.append(f"issue {issue}: claim while {s['status']}")
                continue
            if s[seat] is not None:
                counts["double_claims"] += 1
                problems.append(f"issue {issue}: {new} claimed the {seat} seat held by {s[seat]}")
            s[seat] = new
            counts[f"{seat}_claims"] += 1
        elif action == "takeover":
            counts["takeovers"] += 1
            problems.append(f"issue {issue}: takeover {old}->{new} (leases are 120 min)")
        elif action == "release" and s["status"] == "in_review":
            s["reviewer"] = None
        elif action == "review":
            counts["handoffs"] += 1
            if actor != s["left_worker"]:
                problems.append(f"issue {issue}: handoff by {actor}, worker seat {s['left_worker']}")
        elif action in ("approve", "reject"):
            counts[action + "s"] += 1
            if action == "approve":
                approvals[issue] += 1
            if actor != s["left_reviewer"]:
                problems.append(f"issue {issue}: {action} by {actor}, reviewer seat {s['left_reviewer']}")
    for issue, s in state.items():
        if statuses.get(issue) != s["status"]:
            problems.append(f"issue {issue}: table says {statuses.get(issue)}, events say {s['status']}")

    done = [i for i, st in statuses.items() if st == "done"]
    done_once = sum(1 for i in done if approvals[i] == 1)
    approved_twice = sum(1 for n in approvals.values() if n > 1)
    approved_not_done = sum(1 for i in approvals if statuses.get(i) != "done")
    if approved_twice or approved_not_done or done_once != len(done):
        problems.append(f"done issues approved exactly once: {done_once}/{len(done)}, "
                        f"approved twice: {approved_twice}, approved but not done: {approved_not_done}")
    if until_empty and len(done) != len(statuses):
        problems.append(f"until-empty run ended with {len(statuses) - len(done)} issues not done")

    # Client view: the time between a claim's answer and the handoff/verdict call lies inside
    # the hold, so two such windows on one seat must never overlap.
    windows = defaultdict(list)
    for o in outcomes:
        for key, seat, t0, t1 in o["holds"]:
            windows[(key, seat)].append((t0, t1, o["handle"]))
    overlaps = 0
    for (key, seat), spans in windows.items():
        spans.sort()
        for a, b in itertools.pairwise(spans):
            if b[0] < a[1]:
                overlaps += 1
                problems.append(f"{key} {seat}: {a[2]} and {b[2]} held it at the same time")

    # Comment ids printed by `comment` are unique and stored under the right author and issue.
    printed = [(cid, key, o["handle"]) for o in outcomes for cid, key in o["comment_ids"]]
    ids = [p[0] for p in printed]
    duplicate_ids = len(ids) - len(set(ids))
    stored = {r[0]: r[1:] for r in conn.execute(
        "SELECT c.id, c.author, c.issue_id, c.kind FROM comments c")}
    mismatched = sum(1 for cid, key, handle in printed
                     if stored.get(cid) is None or stored[cid][0] != handle
                     or stored[cid][1] != int(key.split("-")[1]))
    if duplicate_ids or mismatched:
        problems.append(f"comment ids: {duplicate_ids} duplicates, {mismatched} not stored as printed")
    kinds = dict(Counter(v[2] for v in stored.values()))
    ok_calls = Counter(c[0] for o in outcomes for c in o["calls"] if c[3] == 0)
    acted = Counter(c[0].split()[0] for o in outcomes for c in o["calls"]  # --next: 4 = nothing next
                    if c[0].endswith("--next") and c[3] in (0, 4))
    expect = {"progress": ok_calls["comment"], "handoff": acted["review"],
              "approval": acted["approve"], "changes": acted["reject"]}
    kind_mismatch = {k: (kinds.get(k, 0), v) for k, v in expect.items() if kinds.get(k, 0) != v}
    if kind_mismatch:
        problems.append(f"comment kinds stored vs successful calls: {kind_mismatch}")

    return {
        "ok": not problems,
        "problems": problems[:50],
        "issues": len(statuses),
        "status_counts": status_counts,
        "done_exactly_once": done_once,
        "double_claims": counts["double_claims"],
        "overlapping_holds": overlaps,
        "takeovers": counts["takeovers"],
        "worker_claims": counts["worker_claims"],
        "review_claims": counts["reviewer_claims"],
        "handoffs": counts["handoffs"],
        "approvals": counts["approves"],
        "rejections": counts["rejects"],
        "comment_ids_printed": len(ids),
        "comment_ids_unique": len(set(ids)),
        "comment_ids_mismatched": mismatched,
        "comments_by_kind": kinds,
    }


# ---- setup -----------------------------------------------------------------------------------

def default_base() -> str:
    for candidate in ("/var/tmp", tempfile.gettempdir()):
        if os.path.isdir(candidate) and os.access(candidate, os.W_OK) and \
                mount_of(candidate)["fstype"] not in ("tmpfs", "ramfs"):
            return candidate
    return tempfile.gettempdir()


def make_workdir(base: str | None, prefix: str) -> str:
    base = os.path.realpath(os.path.expanduser(base or default_base()))
    if base == REPO or base.startswith(REPO + os.sep):
        sys.exit(f"soak: --dir {base} is inside the repository; choose a directory outside it")
    os.makedirs(base, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=base)


def precompile(python: str, cli: str) -> None:
    """Compile the package once so no CLI call writes .pyc files during the measurement."""
    src = os.path.join(os.path.dirname(os.path.realpath(cli)), "src")
    if os.path.isdir(src):
        subprocess.run([python, "-m", "compileall", "-q", src], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def log(msg: str) -> None:
    print(f"soak: {msg}", file=sys.stderr, flush=True)


def seed_board(args, workdir: str) -> tuple[str, list[tuple]]:
    """earmark init + topics + issues, all through the CLI (8 parallel `new` calls)."""
    env = cli_env()
    r = run([args.python, args.cli, "init", "--prefix", "SOAK", "--owner", "owner"], env, cwd=workdir)
    if r["code"] != 0:
        sys.exit(f"soak: init failed: {r['err'] or r['out']}")
    db = os.path.join(workdir, ".earmark", "earmark.db")
    owner = [args.python, args.cli, "--db", db, "--as", "owner"]
    calls: list[tuple] = []
    # Random 20 % rejects would reach the default review cap (3) on a few issues, which parks
    # them in blocked for a pm; raise it so every issue can finish.
    r = run(owner + ["config", "max_review_rounds", "100"], env, cwd=workdir)
    if r["code"] != 0:
        sys.exit(f"soak: config max_review_rounds failed: {r['err'] or r['out']}")
    for path in TOPICS:
        r = run(owner + ["topic", "add", path], env, cwd=workdir, wrap=args.wrap)
        calls.append(rec("topic add", r))
    rng = random.Random(args.seed)
    specs = []
    for n in range(1, args.issues + 1):
        title = f"{rng.choice(VERBS)} {rng.choice(THINGS)} #{n}"
        body = (f"Goal: {title.lower()} without changing the public API. Acceptance: the tests "
                f"under tests/{rng.choice(TOPICS).replace('/', '_')} pass and the change is "
                f"described in the handoff.")
        specs.append(["new", title, "-b", body, "-t", rng.choice(TOPICS), "-p",
                      rng.choice(PRIORITIES), "--type", rng.choice(TYPES)])
    lock = threading.Lock()

    def worker(chunk: list) -> None:
        for spec in chunk:
            r = run(owner + spec, env, cwd=workdir, wrap=args.wrap)
            with lock:
                calls.append(rec("new", r))
    threads = [threading.Thread(target=worker, args=(specs[i::8],)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    bad = [c for c in calls if c[3] != 0]
    if bad:
        sys.exit(f"soak: {len(bad)} seeding calls failed")
    return db, calls


# ---- soak ------------------------------------------------------------------------------------

def soak(args) -> dict:
    workdir = make_workdir(args.dir, "earmark-soak-")
    try:
        return _soak(args, workdir)
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


def _soak(args, workdir: str) -> dict:
    precompile(args.python, args.cli)
    ctx_info = machine(args.python, workdir)
    if ctx_info["filesystem"]["type"] in ("tmpfs", "ramfs"):
        log("the board is on tmpfs: block writes read 0; pass --dir on the disk to measure")
    log(f"seeding {args.issues} issues in {workdir}")
    t = time.monotonic()
    db, seed_calls = seed_board(args, workdir)
    seed_s = time.monotonic() - t
    run([args.python, args.cli, "--db", db, "ls"], cli_env(), cwd=workdir)  # warm the page cache

    cfg = {"python": args.python, "cli": args.cli, "db": db, "think": args.think,
           "reject_rate": args.reject_rate, "minutes": args.minutes, "max_minutes": args.max_minutes,
           "wait_s": args.wait, "cwd": workdir, "wrap": args.wrap}
    mp = multiprocessing.get_context("spawn")
    start, results = mp.Event(), mp.Queue()
    start_at, ready = mp.Value("d", 0.0), mp.Value("i", 0)
    roles = [("worker", f"w{n:02d}") for n in range(1, args.agents + 1)]
    roles += [("reviewer", f"r{n:02d}") for n in range(1, args.reviewers + 1)]
    procs = [mp.Process(target=agent_main, args=(cfg, handle, role, args.seed * 1000 + i, ready,
                                                 start, start_at, results), daemon=True)
             for i, (role, handle) in enumerate(roles)]
    files_before = file_state(db)
    stat_path = block_stat_path(workdir) if LINUX else None
    sectors_before = sectors_written(stat_path)
    ui = UiSession(args, db) if args.with_ui else None
    ui_io0 = ui.io() if ui else None
    monitor = Monitor(db)
    monitor.start()
    for p in procs:
        p.start()
    joined_by = time.monotonic() + 120
    while ready.value < len(procs) and time.monotonic() < joined_by \
            and any(p.is_alive() for p in procs):
        time.sleep(0.05)  # every agent has joined (measured too) before the clock starts
    span = f"{args.minutes:g} min" if args.minutes else "until empty"
    log(f"running {args.agents} workers + {args.reviewers} reviewers ({span}, think "
        f"{args.think[0]:g}-{args.think[1]:g} s)")
    load_start = loadavg()
    start_at.value = time.monotonic()
    started = start_at.value
    start.set()
    outcomes = []
    limit = (args.minutes or args.max_minutes) * 60 + 120
    for _ in procs:
        try:
            outcomes.append(results.get(timeout=max(1.0, started + limit - time.monotonic())))
        except queue.Empty:
            break
    elapsed = time.monotonic() - started
    for p in procs:
        p.join(10)
        if p.is_alive():
            p.terminate()
    monitor.stop()
    ui_during = None
    if ui is not None:
        ui_io1 = ui.io()
        ui_during = {"write_bytes": io_delta(ui_io0, ui_io1, "write_bytes"),
                     "cancelled_write_bytes": io_delta(ui_io0, ui_io1, "cancelled_write_bytes"),
                     "wchar": io_delta(ui_io0, ui_io1, "wchar"),
                     "change_events": ui.events["changes"], "pings": ui.events["pings"]}
        ui.close()
        ui_during.update(lifetime_write_bytes=ui.lifetime_written, exit_code=ui.proc.returncode,
                         exit_seconds=ui.exit_seconds, killed=ui.killed)
    load_end = loadavg()
    files_after = file_state(db)
    sectors_after = sectors_written(stat_path)
    hung = len(procs) - len(outcomes)

    calls = [c for o in outcomes for c in o["calls"]]
    run_calls = [c for c in calls if c[0] not in ("join", "leave")]
    table = op_table(calls)
    codes = Counter(c[3] for c in calls)
    written = [c[4] for c in calls if c[4] is not None]
    cancelled = [c[7] for c in calls if c[7] is not None]
    net_total = sum(written) - sum(cancelled) if written and cancelled else None
    inv = check_invariants(db, outcomes, until_empty=not args.minutes)
    issues_done = inv["status_counts"].get("done", 0)

    def total(state: dict) -> int:
        return sum((state.get(k) or {}).get("size", 0) for k in ("earmark.db", "earmark.db-wal"))

    agent_hours = (args.agents + args.reviewers) * elapsed / 3600
    disk = {
        "cli_written_total": sum(written) if written else None,
        "cli_written_per_command": (sum(written) / len(written)) if written else None,
        "cli_written_per_issue_done": (sum(written) / issues_done) if written and issues_done else None,
        "cli_written_per_agent_hour": (sum(written) / agent_hours) if written and agent_hours else None,
        "cli_cancelled_total": sum(cancelled) if cancelled else None,
        "cli_net_total": net_total,
        "cli_net_per_command": (net_total / len(calls)) if net_total is not None else None,
        "cli_net_per_issue_done": (net_total / issues_done) if net_total is not None and issues_done else None,
        "cli_net_per_agent_hour": (net_total / agent_hours) if net_total is not None and agent_hours else None,
        "cli_wchar_total": sum(c[8] for c in calls if c[8] is not None) if cancelled else None,
        "wrapped_calls": sum(1 for c in calls if c[7] is not None),
        "late_bytes_total": sum(c[9] for c in calls if c[9] is not None) if cancelled else None,
        "files_before": files_before,
        "files_after": files_after,
        "db_wal_growth": total(files_after) - total(files_before),
        "db_growth": ((files_after.get("earmark.db") or {}).get("size", 0)
                      - (files_before.get("earmark.db") or {}).get("size", 0)),
        "wal_after": (files_after.get("earmark.db-wal") or {}).get("size", 0),
        "wal_max": monitor.wal_max,
        "db_max": monitor.db_max,
        "wal_limit": JOURNAL_SIZE_LIMIT,
        "partition_written": ((sectors_after - sectors_before) * 512
                              if sectors_before is not None and sectors_after is not None else None),
        "partition_note": "whole partition holding the board, including unrelated host activity",
        "ui_during_run": ui_during,
    }
    inv["busy_exit_5"] = codes.get(5, 0)
    inv["conflict_exit_3"] = codes.get(3, 0)
    inv["unexpected_exit_codes"] = sum(o["unexpected_total"] for o in outcomes)
    inv["unexpected_samples"] = [u for o in outcomes for u in o["unexpected"]][:20]
    inv["agent_errors"] = [o["error"] for o in outcomes if o["error"]][:5]
    inv["agents_hung"] = hung
    inv["wal_bounded"] = monitor.wal_max <= JOURNAL_SIZE_LIMIT
    if inv["busy_exit_5"] or inv["unexpected_exit_codes"] or inv["agent_errors"] or hung \
            or not inv["wal_bounded"]:
        inv["ok"] = False
    rss = [c[5] for c in calls]
    result = {
        "kind": "soak",
        "machine": ctx_info,
        "config": {"agents": args.agents, "reviewers": args.reviewers, "issues": args.issues,
                   "minutes": args.minutes, "until_empty": not args.minutes,
                   "think_s": list(args.think), "reject_rate": args.reject_rate,
                   "wait_timeout_s": args.wait, "seed": args.seed, "cli": args.cli,
                   "python": args.python, "proc_io_wrapper": args.wrap, "with_ui": args.with_ui},
        "board": db if args.keep else None,
        "seed": {"seconds": seed_s, "commands": op_table(seed_calls)},
        "run": {"seconds": elapsed, "commands": len(run_calls), "commands_total": len(calls),
                "commands_per_s": len(run_calls) / elapsed if elapsed else None,
                "issues_done": issues_done, "agent_hours": agent_hours,
                "load_start": load_start, "load_end": load_end,
                "load_1m_min": min(monitor.loads) if monitor.loads else None,
                "load_1m_max": max(monitor.loads) if monitor.loads else None,
                "load_1m_mean": statistics.mean(monitor.loads) if monitor.loads else None},
        "exit_codes": dict(sorted(codes.items())),
        "ops": table,
        "rss_max_kib": max(rss) if rss else None,
        "rss_p50_kib": pct(rss, 50),
        "disk": disk,
        "invariants": inv,
    }
    if args.idle > 0:
        log(f"idle check: earmark ui + open /api/stream for {args.idle:g} s")
        result["idle"] = idle_check(args, db)
    return result


# ---- idle check ---------------------------------------------------------------------------------

def proc_io(pid: int) -> dict | None:
    try:
        with open(f"/proc/{pid}/io", encoding="ascii") as fh:
            return {k: int(v) for k, v in (line.split(": ") for line in fh)}
    except OSError:
        return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class UiSession:
    """`earmark ui` on a free port with one open /api/stream, read by a background thread."""

    def __init__(self, args, db: str) -> None:
        self.port = free_port()
        self.proc = subprocess.Popen([args.python, args.cli, "--db", db, "ui", "--port", str(self.port)],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=cli_env(),
                                     cwd=os.path.dirname(os.path.dirname(db)))
        self.events = {"pings": 0, "changes": 0, "last_change": None, "bytes": 0}
        self.sock = None
        self.lifetime_written = None
        self.exit_seconds, self.killed = None, False
        self.stop_event = threading.Event()
        try:
            self.banner = self.proc.stdout.readline().decode("utf-8", "replace").strip()
            self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
            self.sock.sendall(f"GET /api/stream HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                              f"Accept: text/event-stream\r\n\r\n".encode())
            buf = b""
            while b"event: change" not in buf:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise RuntimeError("stream closed before the handshake")
                buf += chunk
            self.status_line = buf.split(b"\r\n", 1)[0].decode()
            self.sock.settimeout(None)
        except BaseException:
            self.close()
            raise
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        pending = b""
        while not self.stop_event.is_set():
            try:
                chunk = self.sock.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            self.events["bytes"] += len(chunk)
            pending += chunk
            while b"\n\n" in pending:
                block, pending = pending.split(b"\n\n", 1)
                if block.startswith(b": ping"):
                    self.events["pings"] += 1
                elif b"event: change" in block:
                    self.events["changes"] += 1
                    self.events["last_change"] = time.monotonic()

    def io(self) -> dict | None:
        return proc_io(self.proc.pid)

    def close(self) -> None:
        """Stop the server and reap it with wait4(), whose rusage covers its whole life,
        including the checkpoint it runs when it closes the last connection. SIGTERM, which
        `earmark ui` handles like Ctrl+C: a background job of a script inherits SIGINT ignored."""
        self.stop_event.set()
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        if self.proc.returncode is not None:
            return
        self.proc.send_signal(signal.SIGTERM)
        started = time.monotonic()
        while True:
            pid, status, ru = os.wait4(self.proc.pid, os.WNOHANG)
            if pid:
                break
            if time.monotonic() > started + 30:
                self.killed = True
                self.proc.kill()
                pid, status, ru = os.wait4(self.proc.pid, 0)
                break
            time.sleep(0.02)
        self.exit_seconds = time.monotonic() - started
        self.proc.returncode = os.waitstatus_to_exitcode(status)
        self.proc.stdout.close()
        self.lifetime_written = ru.ru_oublock * 512 if LINUX else None


def io_delta(io0: dict | None, io1: dict | None, key: str) -> int | None:
    return (io1[key] - io0[key]) if io0 and io1 and key in io0 and key in io1 else None


def idle_check(args, db: str) -> dict:
    ui = UiSession(args, db)
    try:
        time.sleep(2.0)  # server startup settles (watcher opened, first heads published)
        io0, files0 = ui.io(), file_state(db)
        stat_path = block_stat_path(db) if LINUX else None
        sec0 = sectors_written(stat_path)
        t0 = time.monotonic()
        time.sleep(args.idle)
        io1, files1 = ui.io(), file_state(db)
        sec1 = sectors_written(stat_path)
        window = time.monotonic() - t0
        pings, changes_idle = ui.events["pings"], ui.events["changes"]
        # Liveness: one write by the owner must reach the open stream.
        before = ui.events["changes"]
        w = run([args.python, args.cli, "--db", db, "--as", "owner", "comment", "SOAK-1",
                 "idle check: stream liveness"], cli_env(), cwd=os.path.dirname(os.path.dirname(db)))
        deadline = time.monotonic() + 10
        while ui.events["changes"] == before and time.monotonic() < deadline:
            time.sleep(0.01)
        delivered = ui.events["changes"] > before
        latency = (ui.events["last_change"] - w["t1"]) if delivered else None
    finally:
        ui.close()
    unchanged = files0 == files1
    wrote = io_delta(io0, io1, "write_bytes")
    return {
        "seconds": window, "banner": ui.banner, "stream_status": ui.status_line,
        "files_unchanged": unchanged, "files_start": files0, "files_end": files1,
        "ui_write_bytes": wrote, "ui_wchar": io_delta(io0, io1, "wchar"),
        "ui_syscw": io_delta(io0, io1, "syscw"), "ui_rchar": io_delta(io0, io1, "rchar"),
        "ui_syscr": io_delta(io0, io1, "syscr"),
        "partition_written": (sec1 - sec0) * 512 if sec0 is not None and sec1 is not None else None,
        "pings": pings, "changes_during_idle": changes_idle,
        "liveness_write_exit": w["code"], "liveness_delivered": delivered,
        "liveness_latency_s": latency,
        "ok": unchanged and changes_idle == 0 and delivered and wrote in (0, None),
    }


# ---- read latency -------------------------------------------------------------------------------

def reads(args) -> dict:
    workdir = make_workdir(args.dir, "earmark-reads-")
    try:
        return _reads(args, workdir)
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


def _reads(args, workdir: str) -> dict:
    precompile(args.python, args.cli)
    src = os.path.join(os.path.dirname(os.path.realpath(args.cli)), "src")
    sys.path.insert(0, src)  # read-only use of the package for seeding and in-process timing
    from earmark.board import Board

    info = machine(args.python, workdir)
    rng = random.Random(args.seed)
    log(f"seeding {args.read_issues:,} issues and {args.read_comments:,} comments through the "
        f"Board API in {workdir}")
    t = time.monotonic()
    owner = Board.init(workdir, prefix="READ", owner="owner")
    path = owner.path
    areas = [f"{a}/{s}" for a in AREAS for s in ("core", "tools", "tests", "docs")]
    for topic in areas:
        owner.add_topic(topic)
    workers = [f"w{n:02d}" for n in range(1, 11)]
    reviewers = ["r01", "r02"]
    boards = {}
    for handle in workers + reviewers + ["reader"]:
        b = Board.open(db=path)
        b.join("reviewer" if handle in reviewers else "worker", handle=handle, cli="soak")
        boards[handle] = b
    keys = []
    for n in range(1, args.read_issues + 1):
        title = f"{rng.choice(VERBS)} {rng.choice(THINGS)} #{n}"
        body = " ".join([f"Goal: {title.lower()}."] + ["Steps, context and acceptance."] * rng.randint(2, 12))
        assignee = "reader" if n % 5 == 0 else None
        keys.append(owner.create_issue(title, body=body, topic=rng.choice(areas),
                                       priority=rng.choice(PRIORITIES), type=rng.choice(TYPES),
                                       labels=rng.sample(["ui", "api", "perf", "flaky", "p1"], 2),
                                       assignee=assignee)["key"])
    # Status mix through the workflow: done, in review, in progress; the rest stays todo.
    free = [k for i, k in enumerate(keys, 1) if i % 5 != 0]
    rng.shuffle(free)
    n_done, n_review, n_prog = len(keys) // 4, len(keys) // 10, len(keys) // 7
    flows = 0
    for i, key in enumerate(free[:n_done + n_review + n_prog]):
        worker = boards[workers[i % len(workers)]]
        worker.claim(key)
        if i < n_done + n_review:
            worker.submit_review(key, note="changed the module · run the tests")
            flows += 1
            if i < n_done:
                reviewer = boards[reviewers[i % 2]]
                reviewer.claim(key)
                reviewer.approve(key, note="verified")
                flows += 1
    weights = [1 / (r + 1) ** 0.7 for r in range(len(keys))]
    order = keys[:]
    rng.shuffle(order)
    authors = workers + reviewers + ["owner"]
    for _ in range(max(0, args.read_comments - flows)):
        key = rng.choices(order, weights)[0]
        who = rng.choice(authors)
        b = owner if who == "owner" else boards[who]
        text = f"{rng.choice(['Moved', 'Checked', 'Measured', 'Split'])} the {rng.choice(THINGS)}; " \
               f"{'@reader please look. ' if rng.random() < 0.1 else ''}" + "details " * rng.randint(4, 30)
        b.add_comment(key, text.strip(), kind=rng.choice(["note", "progress", "question"]))
    for b in boards.values():
        b.close()
    seed_s = time.monotonic() - t
    reader = Board.open(db=path, actor="reader")
    total_comments = reader.conn.execute("SELECT count(*) FROM comments").fetchone()[0]
    by_issue = dict(reader.conn.execute("SELECT issue_id, count(*) FROM comments GROUP BY issue_id"))
    statuses = dict(Counter(r[0] for r in reader.conn.execute("SELECT status FROM issues")))
    owner.close()
    reader.close()  # CLI calls below run with no other connection open, as on a quiet board

    # Issues for `comments --new`: distinct per sample, each read once with a fresh cursor.
    prefix = "READ-"
    with_comments = [k for k in keys if by_issue.get(int(k.split("-")[1]), 0) >= 3]
    rng.shuffle(with_comments)
    samples = args.samples
    plans = {
        "ls": lambda i: ["ls"],
        "ls -t TOPIC --ready": lambda i: ["ls", "-t", AREAS[i % len(AREAS)], "--ready"],
        "show": lambda i: ["show", with_comments[(i * 7) % len(with_comments)]],
        "comments --new": lambda i: ["comments", with_comments[i], "--new"],
        "inbox": lambda i: ["inbox"],
        "check --peek": lambda i: ["check", "--peek"],
        "ls (no wrapper)": lambda i: ["ls"],
    }
    env = cli_env()
    base = [args.python, args.cli, "--db", path, "--as", "reader"]
    measured: dict[str, list] = defaultdict(list)
    returned: dict[str, list] = defaultdict(list)
    floor = []
    monitor = Monitor(path)  # load average while the reads run
    monitor.start()
    for i in range(samples):  # interleaved so host load shifts hit every command alike
        r = run([args.python, "-c", "pass"], env, cwd=workdir)
        floor.append((r["t1"] - r["t0"]) * 1000)
        for name, plan in plans.items():
            r = run(base + plan(i), env, cwd=workdir, wrap=args.wrap and "no wrapper" not in name)
            if r["code"] != 0:
                sys.exit(f"soak: {name} exited {r['code']}: {r['err']}")
            measured[name].append(r)
            m = COUNT_RE.search(r["out"])
            if name in ("comments --new", "inbox"):
                returned[name].append(int(m.group(1)) if m else len(
                    re.findall(r"^\S*\s*#\d+\s", r["out"], re.MULTILINE)))
            elif name.startswith(("ls", "check")):
                returned[name].append(sum(1 for line in r["out"].splitlines()
                                          if line.startswith(prefix)))

    monitor.stop()

    # The same queries in process (no interpreter start), read-only calls only.
    def timed(fn) -> list[float]:
        out = []
        for _ in range(samples):
            t0 = time.perf_counter()
            fn()
            out.append((time.perf_counter() - t0) * 1000)
        return out

    show_key = with_comments[0]
    reader = Board.open(db=path, actor="reader")
    inproc = {
        "ls": timed(lambda: reader.list_issues()),
        "ls -t TOPIC --ready": timed(lambda: reader.list_issues(topic=["api"], ready=True)),
        "show": timed(lambda: reader.get_issue(show_key)),
        "comments --new": timed(lambda: reader.list_comments(show_key, since=0)),
        "inbox": timed(lambda: reader.inbox(peek=True)),
    }
    if hasattr(reader, "check"):
        inproc["check --peek"] = timed(lambda: reader.check(peek=True))
    inproc["ls (no wrapper)"] = inproc["ls"]
    reader.close()
    table = {}
    for name, rows in measured.items():
        ms = [(r["t1"] - r["t0"]) * 1000 for r in rows]
        wr = [r["written"] for r in rows if r["written"] is not None]
        cn = [r["cancelled"] for r in rows if r["cancelled"] is not None]
        table[name] = {"n": len(rows), "p50_ms": pct(ms, 50), "p95_ms": pct(ms, 95),
                       "max_ms": max(ms), "inproc_p50_ms": pct(inproc.get(name, []), 50),
                       "inproc_p95_ms": pct(inproc.get(name, []), 95),
                       "written_per_call": (sum(wr) / len(wr)) if wr else None,
                       "net_per_call": ((sum(wr) - sum(cn)) / len(rows)) if wr and cn else None,
                       "rss_max_kib": max(r["rss_kb"] for r in rows),
                       "items_p50": pct(returned[name], 50) if returned.get(name) else None}
    return {
        "kind": "reads",
        "machine": info,
        "board": {"issues": len(keys), "comments": total_comments, "statuses": statuses,
                  "topics": len(areas) + len(AREAS), "agents": len(boards),
                  "db_bytes": _size(path), "wal_bytes": _size(path + "-wal"),
                  "seed_seconds": seed_s},
        "samples": samples,
        "python_start_p50_ms": pct(floor, 50),
        "python_start_p95_ms": pct(floor, 95),
        "ops": table,
        "load_1m_min": min(monitor.loads) if monitor.loads else None,
        "load_1m_max": max(monitor.loads) if monitor.loads else None,
        "load_end": loadavg(),
    }


# ---- output ---------------------------------------------------------------------------------------

def size(n: float | None) -> str:
    if n is None:
        return "n/a"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024 or unit == "GiB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return str(n)


def ms(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:,.0f}" if v >= 100 else f"{v:.1f}" if v >= 1 else f"{v:.2f}"


def print_machine(m: dict, out) -> None:
    fs = m["filesystem"]
    print(f"machine: {m['cpu']} ({m['cores']} cores / {m['threads']} threads, {m['ram_gib']} GiB) · "
          f"{m['os']} · Python {m['python']} · SQLite {m['sqlite']} · board on {fs['type']} "
          f"({fs['device']} at {fs['mount']})", file=out)


def print_soak(res: dict, out) -> None:
    c, r, inv, d = res["config"], res["run"], res["invariants"], res["disk"]
    mode = f"{c['minutes']:g} min" if c["minutes"] else "until empty"
    if c.get("with_ui"):
        mode += " · earmark ui open"
    print(f"\nearmark soak · {c['agents']} workers + {c['reviewers']} reviewers · {c['issues']} issues · "
          f"{mode} · think {c['think_s'][0]:g}-{c['think_s'][1]:g} s · reject {c['reject_rate']:.0%}",
          file=out)
    print_machine(res["machine"], out)
    print(f"load average (1 min): start {r['load_start'][0] if r['load_start'] else '-'}, "
          f"during {r['load_1m_min']}-{r['load_1m_max']} (mean {r['load_1m_mean']:.1f}), "
          f"end {r['load_end'][0] if r['load_end'] else '-'}", file=out)
    seed_new = res["seed"]["commands"].get("new", {})
    print(f"seed: {c['issues']} x new in {res['seed']['seconds']:.1f} s (8 parallel), p50 "
          f"{ms(seed_new.get('p50_ms'))} ms, {size(seed_new.get('written_per_call'))} written per call",
          file=out)
    print(f"run: {r['commands']:,} commands in {r['seconds']:.1f} s ({r['commands_per_s']:.1f}/s) · "
          f"{r['issues_done']}/{inv['issues']} issues done · {inv['rejections']} rejected and reworked",
          file=out)
    head = f"{'COMMAND':<16}{'N':>7}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}{'max ms':>9}" \
           f"{'WRITTEN/CALL':>14}{'NET/CALL':>11}{'RSS MAX':>10}  EXIT CODES"
    print("\n" + head, file=out)
    for op, row in res["ops"].items():
        codes = " ".join(f"{k}:{v}" for k, v in row["exit_codes"].items())
        print(f"{op:<16}{row['n']:>7,}{ms(row['p50_ms']):>9}{ms(row['p95_ms']):>9}"
              f"{ms(row['p99_ms']):>9}{ms(row['max_ms']):>9}{size(row['written_per_call']):>14}"
              f"{size(row['net_per_call']):>11}{size(row['rss_max_kib'] * 1024):>10}  {codes}", file=out)
    ec = res["exit_codes"]
    other = sum(v for k, v in ec.items() if k not in (0, 3, 4, 5))
    print(f"\nexit codes: 0 ok {ec.get(0, 0):,} · 3 conflict {ec.get(3, 0)} · 4 empty {ec.get(4, 0)} · "
          f"5 busy {ec.get(5, 0)} · other {other}", file=out)
    print(f"invariants: {'PASS' if inv['ok'] else 'FAIL'} · done exactly once "
          f"{inv['done_exactly_once']}/{inv['status_counts'].get('done', 0)} · double claims "
          f"{inv['double_claims']} · overlapping holds {inv['overlapping_holds']} · takeovers "
          f"{inv['takeovers']} · comment ids unique {inv['comment_ids_unique']:,}/"
          f"{inv['comment_ids_printed']:,} · WAL max {size(d['wal_max'])} (limit "
          f"{size(d['wal_limit'])})", file=out)
    for p in inv["problems"][:10]:
        print(f"  problem: {p}", file=out)
    for u in inv["unexpected_samples"][:10]:
        print(f"  unexpected: {u}", file=out)
    for e in inv["agent_errors"][:3]:
        print(f"  agent error: {e.strip().splitlines()[-1]}", file=out)
    print(f"disk: CLI write_bytes {size(d['cli_written_total'])} "
          f"({size(d['cli_written_per_command'])}/command, {size(d['cli_written_per_agent_hour'])}"
          f"/agent-hour) · cancelled before writeback {size(d['cli_cancelled_total'])} · net "
          f"{size(d['cli_net_total'])} ({size(d['cli_net_per_command'])}/command, "
          f"{size(d['cli_net_per_issue_done'])}/issue done, {size(d['cli_net_per_agent_hour'])}"
          f"/agent-hour)", file=out)
    print(f"files: earmark.db growth {size(d['db_growth'])}, WAL left {size(d['wal_after'])} · "
          f"write() calls passed "
          f"{size(d['cli_wchar_total'])} · partition sectors written {size(d['partition_written'])} "
          f"(whole partition, includes other host activity)", file=out)
    if d.get("ui_during_run"):
        u = d["ui_during_run"]
        print(f"earmark ui during the run: wrote {size(u['write_bytes'])} while agents ran, "
              f"{size(u['lifetime_write_bytes'])} from start to exit · {u['change_events']:,} "
              f"change events on the open stream · SIGTERM exit "
              f"{u['exit_code']} in {u['exit_seconds']:.2f} s{' (killed)' if u['killed'] else ''}",
              file=out)
    print(f"peak RSS of one CLI call: {size(res['rss_max_kib'] * 1024)} (p50 "
          f"{size(res['rss_p50_kib'] * 1024)})", file=out)
    if "idle" in res:
        print_idle(res["idle"], out)


def print_idle(i: dict, out) -> None:
    latency = "-" if i["liveness_latency_s"] is None else f"{i['liveness_latency_s']:.2f}"
    print(f"idle: {i['seconds']:.0f} s with earmark ui and an open /api/stream · files "
          f"{'unchanged' if i['files_unchanged'] else 'CHANGED'} (earmark.db, -wal, -shm size and "
          f"mtime) · ui process wrote {size(i['ui_write_bytes'])} · change events "
          f"{i['changes_during_idle']} · pings {i['pings']} · a write reached the stream in "
          f"{latency} s · "
          f"{'PASS' if i['ok'] else 'FAIL'}", file=out)


def print_reads(res: dict, out) -> None:
    b = res["board"]
    print(f"\nearmark reads · {b['issues']:,} issues · {b['comments']:,} comments · "
          f"{b['topics']} topics · db {size(b['db_bytes'])} · {res['samples']} samples each", file=out)
    print_machine(res["machine"], out)
    print(f"python -c pass: p50 {ms(res['python_start_p50_ms'])} ms (interpreter start floor)", file=out)
    print(f"\n{'COMMAND':<22}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}{'in-proc p50':>13}"
          f"{'WRITTEN/CALL':>14}{'NET/CALL':>11}{'ROWS':>7}", file=out)
    for name, row in res["ops"].items():
        rows = "-" if row["items_p50"] is None else f"{row['items_p50']:.0f}"
        print(f"{name:<22}{ms(row['p50_ms']):>9}{ms(row['p95_ms']):>9}{ms(row['max_ms']):>9}"
              f"{ms(row['inproc_p50_ms']):>13}{size(row['written_per_call']):>14}"
              f"{size(row['net_per_call']):>11}{rows:>7}", file=out)
    print(f"load average (1 min) during the reads: {res['load_1m_min']}-{res['load_1m_max']}",
          file=out)


# ---- main -------------------------------------------------------------------------------------------

def think_range(raw: str) -> tuple[float, float]:
    parts = raw.split("-")
    try:
        lo = float(parts[0])
        hi = float(parts[1]) if len(parts) > 1 else lo
    except ValueError:
        raise argparse.ArgumentTypeError("think time is S or MIN-MAX seconds, e.g. 2-6") from None
    if lo < 0 or hi < lo:
        raise argparse.ArgumentTypeError("think time needs 0 <= MIN <= MAX")
    return lo, hi


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Soak and performance harness for the earmark CLI.")
    p.add_argument("--agents", type=int, default=12, help="worker agents (default 12)")
    p.add_argument("--reviewers", type=int, default=4, help="reviewer agents (default 4)")
    p.add_argument("--issues", type=int, default=300, help="issues to seed (default 300)")
    stop = p.add_mutually_exclusive_group()
    stop.add_argument("--minutes", type=float, default=None, help="run N minutes (or until empty)")
    stop.add_argument("--until-empty", action="store_true", help="run until every issue is done (default)")
    p.add_argument("--think", type=think_range, default=(0.0, 0.0),
                   help="seconds before each command, S or MIN-MAX (default 0: max pressure)")
    p.add_argument("--reject-rate", type=float, default=0.2, help="share of reviews rejected (0.2)")
    p.add_argument("--wait", type=int, default=3, help="earmark wait --timeout when idle, s (3)")
    p.add_argument("--max-minutes", type=float, default=30, help="safety cap for --until-empty (30)")
    p.add_argument("--idle", type=float, default=60, help="idle check seconds after the run; 0 skips (60)")
    p.add_argument("--with-ui", action="store_true", help="keep earmark ui and an open "
                   "/api/stream running during the soak, as when someone watches the board")
    p.add_argument("--reads", action="store_true", help="read-latency run instead of the soak")
    p.add_argument("--read-issues", type=int, default=2000, help="issues for --reads (2000)")
    p.add_argument("--read-comments", type=int, default=10000, help="comments for --reads (10000)")
    p.add_argument("--samples", type=int, default=30, help="samples per command for --reads (30)")
    p.add_argument("--dir", default=None, help="base directory for the temporary board "
                   "(default /var/tmp when on a disk, else the system temp dir)")
    p.add_argument("--keep", action="store_true", help="keep the board directory")
    p.add_argument("--python", default=sys.executable, help="interpreter that runs the CLI")
    p.add_argument("--cli", default=os.path.join(REPO, "earmark"), help="earmark launcher to drive")
    p.add_argument("--seed", type=int, default=7477, help="random seed (7477)")
    p.add_argument("--no-wrap", action="store_true", help="run the CLI without the /proc/self/io "
                   "wrapper (wait4 totals only)")
    p.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
    args = p.parse_args(argv)
    args.wrap = WRAP and not args.no_wrap
    args.cli = os.path.realpath(args.cli)
    if args.agents < 1 or args.reviewers < 1:
        p.error("--agents and --reviewers need at least 1 each")

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # clean up the board directory
    result = reads(args) if args.reads else soak(args)
    out = sys.stderr if args.json else sys.stdout
    (print_reads if args.reads else print_soak)(result, out)
    if args.json:
        json.dump(result, sys.stdout, indent=2, default=str)
        print()
    ok = result.get("invariants", {}).get("ok", True) and result.get("idle", {}).get("ok", True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
