# Performance

Measured with [`scripts/soak.py`](../scripts/soak.py), which drives the real CLI. Every agent
is its own OS process and calls `earmark` the way an agent shell does, one subprocess per
command (`python3 earmark --db <board> --as <handle> …`), so every latency below includes the
Python interpreter start, opening the board, the query or write, rendering and exit.

The agents follow the skill's loop. Workers run `next`, which claims an issue and prints its
unseen comments, then post 1–3 progress comments and run `review … --next`, which hands off
and claims the next issue in the same call. When nothing is ready they take a handoff with
`next --review`. Reviewers run `next --review`, then `approve … --next` or `reject … --next`;
20 % of reviews are rejected, so issues go back to their worker. A command that ends with
`· N changes for you` is followed by `earmark check`. Now and then an agent reads `show`,
`ls`, `team` or `topics`. Idle agents run `check`, `ls --count` and
`earmark wait --for work|review --timeout 3s`. The review cap is raised to 100 rounds so that
random rejects never park an issue.

## Summary

| Run | Setup | Result |
|---|---|---|
| 1. Max pressure | 12 workers + 4 reviewers, no think time, 300 issues until empty | 2,300 commands in 11.7 s (197 per second); p50 45–57 ms and p99 ≤ 103 ms per command (`join` 85 ms p50; `wait` blocks by design); 0 busy, 0 conflicts, every invariant holds |
| 2. Realistic pacing | 6 workers + 2 reviewers, 2–6 s between commands, 10 min | about 1,230 commands and 160 issues done per run; 0 busy, 0 conflicts, every invariant holds; 382 KiB written per issue (44.9 MiB per agent-hour) with nothing else connected, 359 KiB (40.8 MiB) with `earmark ui` open |
| 3. Reads | 2,000 issues, 10,000 comments, one reader | `ls`, `show`, `comments --new` 46–50 ms p50; `inbox` 58 ms, `check --peek` 63 ms; plain reads write nothing |
| 4. Idle server | `earmark ui` with an open `/api/stream`, 60 s | `earmark.db`, `-wal` and `-shm` unchanged; the server's read and write counters stayed at 0 |

Peak RSS of one CLI process: 16.5 MiB at the median of run 1, 20.5 MiB at most (`join`, which
prints the briefing).

## Machine

| | |
|---|---|
| CPU | AMD Ryzen AI Max+ 395, 16 cores / 32 threads |
| Memory | 121 GiB |
| OS | Ubuntu 26.04.1 LTS, Linux 7.0.0-42-generic |
| Python, SQLite | Python 3.14.4, SQLite 3.46.1 |
| Board disk | Lexar NQ790 2 TB NVMe, ext4 (`relatime`), boards under `/var/tmp` |
| Code | commit `610c4a6`, run from a `git archive` snapshot (`--cli`) |
| Host load | other work ran on the machine during every run; the 1-minute load average was 8–16 and is given per run |

## 1. Max pressure: 12 workers + 4 reviewers, 300 issues, until empty

`python3 scripts/soak.py` · load average 9.0–11.7 · board seeded through the CLI in 2.4 s
(`init`, 9 topics, 300 `earmark new` calls 8 at a time).

| Command | N | p50 ms | p95 ms | p99 ms | max ms | write_bytes KiB/call | net KiB/call | peak RSS MiB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `next` | 26 | 47 | 91 | 103 | 105 | 36.5 | 34.3 | 16.6 |
| `review --next` | 366 | 56 | 72 | 84 | 141 | 125.7 | 121.1 | 16.9 |
| `next --review` | 62 | 48 | 60 | 62 | 63 | 20.8 | 18.6 | 16.8 |
| `approve --next` | 300 | 55 | 72 | 89 | 96 | 118.2 | 112.4 | 16.9 |
| `reject --next` | 66 | 57 | 73 | 82 | 85 | 122.2 | 116.3 | 16.8 |
| `comment` | 722 | 52 | 62 | 74 | 86 | 32.2 | 27.6 | 16.6 |
| `check` | 459 | 56 | 67 | 77 | 107 | 13.6 | 7.7 | 16.9 |
| `show` | 107 | 50 | 57 | 58 | 60 | 5.5 | 0 | 16.3 |
| `ls` | 76 | 51 | 59 | 65 | 65 | 4.8 | 0 | 16.5 |
| `team` | 35 | 53 | 61 | 65 | 67 | 5.6 | 0 | 16.8 |
| `topics` | 15 | 50 | 58 | 58 | 58 | 9.3 | 0 | 16.1 |
| `ls --count` | 41 | 48 | 55 | 59 | 59 | 1.4 | 0 | 16.2 |
| `wait --timeout 3s` | 25 | 3,040 | 3,052 | 3,054 | 3,054 | 2.2 | 0 | 16.3 |
| `join` | 16 | 85 | 98 | 99 | 99 | 34.0 | 32.2 | 20.5 |
| `leave` | 16 | 45 | 50 | 54 | 55 | 22.8 | 21.0 | 16.2 |

The `--next` rows are two steps in one call (the action, then `next`), so they carry both
writes. Their exit code is `next`'s: 4 (17, 18 and 7 calls) means the action succeeded and
nothing was left to claim. `next` and `next --review` include the calls that found nothing
(12 and 34).

| Check | Result |
|---|---|
| Exit codes | 2,229 ok · 103 empty (4) · 0 conflict (3) · **0 busy (5)** · 0 other |
| Every issue done exactly once | 300 of 300 done, each with one `approve` event |
| No issue held by two agents at once | 0 double claims replaying the events table (worker seat and reviewer seat per issue: 366 worker claims, 366 review claims, 366 handoffs, 300 approvals, 66 rejections); 0 overlapping holds from the agents' own timings; 0 lease takeovers |
| Comment ids | 722 ids printed by `comment`, 722 unique, each stored with the printing agent and issue; stored kinds match the successful calls (722 progress, 366 handoff, 300 approval, 66 changes) |
| WAL size | largest seen 8.0 MiB (sampled every 0.1 s), 8.0 MiB at the end; `journal_size_limit` is 8 MiB. In two other max-pressure runs the WAL file peaked at 20.9 MiB (300 issues) and 13.2 MiB (1,200 issues) and was back at 8.0 MiB afterwards, see [How bytes written are counted](#how-bytes-written-are-counted) |
| Bytes written | 109.4 MiB net for 2,332 calls: 48 KiB per command, 374 KiB per issue taken from todo to done. `earmark.db` grew by 532 KiB |

## 2. Realistic pacing: 8 agents, 2–6 s between commands, 10 minutes

`python3 scripts/soak.py --agents 6 --reviewers 2 --think 2-6 --minutes 10 --idle 0`, run
twice at the same time on two boards: once with nothing else connected, once with `earmark ui`
and an open `/api/stream` (`--with-ui`), as when someone keeps the board open in a browser.
Every agent waits 2–6 s before each command, so it calls the board about 890 times an hour,
once every 4 s.

| | Nothing else connected | `earmark ui` open |
|---|---:|---:|
| Duration | 624 s | 623 s |
| Commands | 1,239 | 1,225 |
| Commands per agent-hour | 894 | 886 |
| Issues done · rejected and reworked | 167 · 39 | 161 · 35 |
| Issues done per agent-hour | 120 | 116 |
| Exit 5 busy · exit 3 conflict | 0 · 0 | 0 · 0 |
| Invariants (as in run 1) | pass | pass |
| `comment` p50 · p99 · max | 51 · 69 · 112 ms | 50 · 69 · 113 ms |
| `review --next` p50 · p99 · max | 54 · 75 · 84 ms | 54 · 106 · 116 ms |
| `approve --next` p50 · p99 · max | 53 · 75 · 79 ms | 53 · 68 · 79 ms |
| `check` p50 · p99 | 55 · 68 ms | 55 · 75 ms |
| CLI net written | 62.3 MiB | 56.4 MiB |
| per command | 51 KiB | 47 KiB |
| **per issue done** | **382 KiB** | **359 KiB** |
| **per agent-hour** | **44.9 MiB** | **40.8 MiB** |
| `earmark ui` process | - | 4 KiB while agents ran, 88 KiB from start to exit |
| `earmark.db` growth | 312 KiB | 300 KiB |
| WAL file at the end | 7.5 MiB | 8.0 MiB |
| Load average (1 min) | 8.0–12.9 | 8.0–12.8 |

With connections closing without a checkpoint, the two setups now write about the same per
issue: commits collect in the WAL and the 1,000-page checkpoint copies each changed page into
`earmark.db` once, whether or not a long-lived connection is open.

**Compared with the previous measurement** (CLI at `0268915`, before connections closed
without a checkpoint and before `next` printed comments and handoffs took `--next`; same
machine and harness settings, load 2.7–7.1): writes per issue dropped from 846 to 382 KiB
with nothing else connected and from 405 to 359 KiB with the UI open. Writes per agent-hour
went from 47.8 to 44.9 MiB and from 22.9 to 40.8 MiB. The harness paces commands, not
issues: the new loop does in one call what took two, so each command carries more work and
the same 890 commands an hour now finish 120 issues instead of 58. On its own, closing
without a checkpoint cut a solitary `comment` from 52 to about 22 KiB. Under max pressure
(run 1) the same 300 issues took 2,300 commands instead of 3,656, at 374 instead of 388 KiB
per issue.

## 3. Read latency: 2,000 issues, 10,000 comments

`python3 scripts/soak.py --reads` · load average 12.9–13.5 · board seeded through the Board
API in 3.7 s: 2,000 issues over 30 topics (1,015 todo, 285 in progress, 200 in review, 500
done), 10,000 comments, 13 agents; `earmark.db` 4.4 MiB plus a 4.0 MiB WAL. One agent
(`reader`, assignee of 400 issues) runs each command 30 times, interleaved, with no other
connection open. `comments --new` reads a different issue each time (a fresh cursor); `inbox`
returns the next 50 unseen comments each time; `check --peek` summarizes everything that
changed on the reader's 400 issues since it joined, without moving its cursor. The in-process
column times the same Board query without starting a process.

| Command | p50 ms | p95 ms | max ms | in-process p50 ms | rows | net written per call |
|---|---:|---:|---:|---:|---:|---:|
| `ls` (open issues, first page) | 50 | 60 | 64 | 1.6 | 50 | 0 |
| `ls -t <topic> --ready` | 49 | 58 | 65 | 1.3 | 50 | 0 |
| `show KEY` | 46 | 56 | 59 | 0.10 | 1 | 0 |
| `comments KEY --new` | 46 | 53 | 53 | 0.05 | 4 | 8.0 KiB |
| `inbox` | 58 | 64 | 66 | 6.5 | 50 | 4.3 KiB |
| `check --peek` | 63 | 75 | 76 | 15.9 | 10 | 0 |

`python3 -c pass` takes 16 ms on this machine, and `ls` without the I/O wrapper 48 ms (2 ms
less). The query itself is 0.05–16 ms; the rest is the interpreter, imports, opening the
board and rendering. `comments --new` and `inbox` advance the reader's cursor, one small write
transaction each. Plain reads write nothing.

## 4. Idle server

At the end of run 1, `earmark ui` is started on the finished board (300 done issues, 1,454
comments; `earmark.db` 0.8 MiB, `-wal` 8 MiB, `-shm` 32 KiB), one client opens
`/api/stream`, and after 2 s the harness records size and mtime of the three files and the
server's `/proc/<pid>/io`, waits 60 s and records them again.

| Check | Result |
|---|---|
| `earmark.db`, `-wal`, `-shm` size and mtime | unchanged |
| Server `write_bytes`, `wchar`, `syscw` | 0, 0, 0 |
| Server `rchar`, `syscr` | 0, 0: the change check reads `PRAGMA data_version` from shared memory |
| Stream | 2 keep-alive pings, 0 change events in the window and 0 in the 2 s before it |
| Liveness after the window | one `earmark comment` reached the open stream in 0.97 s (the watcher checks once per second) |

During the same 60 s, other software on this machine wrote 22.6 MiB to the partition that
holds the board.

## How bytes written are counted

SQLite writes whole 4 KiB pages. A write transaction appends one WAL frame for each page it
changes (table and index leaves, the event row, the issue row whose lease it renews), and a
checkpoint later copies each changed page once into `earmark.db`. With `synchronous=NORMAL`
the fsyncs happen at checkpoints. On Python 3.12+ earmark closes connections without a
checkpoint (`SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE`), so the WAL stays between commands and the
automatic checkpoint at 1,000 pages does the copying; Python 3.10 and 3.11 keep SQLite's
default, where the last connection to close checkpoints, fsyncs and deletes the WAL, which
roughly doubles the bytes of a command that runs alone (the previous measurement above).
The first process to open the board when no other connection is open rebuilds the 32 KiB
`-shm` index; the old index pages are dropped before writeback, which the table below counts
as cancelled. After a complete checkpoint SQLite restarts the WAL from its beginning once no
reader is using it, and truncates the file to `journal_size_limit` (8 MiB) at that point. Under
max pressure a reader is almost always open, so the file can pass 8 MiB before the next
restart (20.9 MiB was the largest seen). The WAL file stays beside `earmark.db` between
commands and after the agents stop; it was 8 MiB or less at the end of every run here. The harness reports, per CLI process:

| Figure | Source | Meaning |
|---|---|---|
| write_bytes | `wait4()` rusage (`ru_oublock` × 512), checked against the child's `/proc/self/io` at exit (difference 0 in every run) | pages the process dirtied: WAL frames, checkpoint copies into `earmark.db`, the `-shm` index |
| cancelled | `cancelled_write_bytes` from the child's `/proc/self/io` | dirty pages dropped before writeback because their file was truncated or deleted, mostly the 32 KiB `-shm`. Charged to the process that truncates the file |
| **net** | write_bytes − cancelled | what the CLI sends to the drive, before ext4 journal blocks and the drive's own write amplification |
| file growth | size of `earmark.db` before and after; WAL size at the end | data the board keeps |
| partition | `/sys/dev/block/…/stat` sectors written | the whole partition during the run, including all other software; context only |

Peak RSS is `VmHWM` from the child's `/proc/self/status` at exit (`ru_maxrss` from `wait4()`
also carries the parent's size at fork). The I/O wrapper adds about 2 ms per call;
`--no-wrap` drops it and keeps the `wait4()` totals.

## What this means for an SSD

At the realistic pace an agent caused 44.9 MiB of writes per hour with nothing else connected
and 40.8 MiB with the web UI open, about 380 KiB for each issue it took from todo to done,
while calling the board every 4 s; agents doing real work spend most of their time in the code
and call it less often. Take ten such agents working eight hours a day, every day of the year,
at the higher figure: 10 × 8 × 365 × 44.9 MiB is about 1.4 TB a year. SSD endurance is rated
in terabytes written (TBW): low-end 1 TB QLC drives around 200 TBW, mainstream 1 TB TLC drives
around 600 TBW. 1.4 TB is 0.7 % of a 200 TBW rating per year and 0.23 % of 600 TBW; doubled to
cover ext4 journal and metadata blocks, which the per-process counters leave out, it is 1.4 %
and 0.46 %. Nothing is written between commands: no heartbeats and no polling writes, and an
idle board with the UI open and a browser streaming writes nothing (run 4).

## Reproduce

```bash
python3 scripts/soak.py                                    # run 1, then the 60 s idle check (run 4)
python3 scripts/soak.py --agents 6 --reviewers 2 --think 2-6 --minutes 10 --idle 0
python3 scripts/soak.py --agents 6 --reviewers 2 --think 2-6 --minutes 10 --idle 0 --with-ui
python3 scripts/soak.py --reads                            # run 3
```

Add `--json > result.json` for the full result: every command row, invariants, file states,
load samples. Boards go to a fresh directory under `/var/tmp` (a disk; tmpfs has no block
writes to count) or under `--dir PATH`, never inside the repository, and are removed
afterwards unless `--keep`. `--cli PATH` drives another checkout and `--python PATH` another
interpreter. The exit status is 1 when an invariant or the idle check fails; the WAL check
passes when the file is back at or under `journal_size_limit` after the run and never passed
four times it.
