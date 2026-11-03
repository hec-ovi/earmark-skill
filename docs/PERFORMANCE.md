# Performance

Measured with [`scripts/soak.py`](../scripts/soak.py), which drives the real CLI. Every agent
is its own OS process and calls `earmark` the way an agent shell does, one subprocess per
command (`python3 earmark --db <board> --as <handle> …`), so every latency below includes the
Python interpreter start, opening the board, the query or write, rendering and exit.

## Summary

| Run | Setup | Result |
|---|---|---|
| 1. Max pressure | 12 workers + 4 reviewers, no think time, 300 issues until empty | 3,656 commands in 13.5 s (270 per second); p50 40 ms, p99 ≤ 93 ms per command; 0 busy, 0 conflicts, every invariant holds |
| 2. Realistic pacing | 6 workers + 2 reviewers, 2–6 s between commands, 10 min | 1,220 commands; 0 busy, 0 conflicts, every invariant holds; 47.8 MiB written per agent-hour with nothing else connected, 22.9 MiB with `earmark ui` open |
| 3. Reads | 2,000 issues, 10,000 comments, one reader | `ls`, `show` 48–51 ms p50 with nothing written; `comments --new`, `inbox` 64 ms p50 |
| 4. Idle server | `earmark ui` with an open `/api/stream`, 60 s | `earmark.db`, `-wal` and `-shm` unchanged; the server's read and write counters stayed at 0 |

Peak RSS of one CLI process: 20.5 MiB at the median of run 1, 24.7 MiB at most (`join`, which
prints the briefing).

## Machine

| | |
|---|---|
| CPU | AMD Ryzen AI Max+ 395, 16 cores / 32 threads |
| Memory | 121 GiB |
| OS | Ubuntu 26.04.1 LTS, Linux 7.0.0-42-generic |
| Python, SQLite | Python 3.14.4, SQLite 3.46.1 |
| Board disk | Lexar NQ790 2 TB NVMe, ext4 (`relatime`), boards under `/var/tmp` |
| Code | commit `0268915`, run from a `git archive` snapshot (`--cli`) |
| Host load | other work ran on the machine during every run; the 1-minute load average is given per run |

## 1. Max pressure: 12 workers + 4 reviewers, 300 issues, until empty

`python3 scripts/soak.py` · load average 3.8–6.1 · board seeded through the CLI in 2.4 s
(`init`, 9 topics, 300 `earmark new` calls 8 at a time).

Workers loop `next` → `comments --new` → 1–3 progress comments → `review`, take a handoff with
`next --review` when nothing is ready, and read `ls`, `show`, `inbox`, `team` or `topics` now
and then. Reviewers approve or reject (20 % rejected, so 66 issues went back to their worker).
Idle agents run `inbox`, `ls --count` and `earmark wait --timeout 3s`.

| Command | N | p50 ms | p95 ms | p99 ms | max ms | write_bytes KiB/call | net KiB/call | peak RSS MiB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `next` | 555 | 40 | 60 | 80 | 132 | 41.8 | 39.0 | 21.0 |
| `comments --new` | 732 | 40 | 59 | 77 | 123 | 16.4 | 12.5 | 20.7 |
| `comment` | 713 | 40 | 58 | 79 | 156 | 32.2 | 29.2 | 20.6 |
| `review` | 366 | 40 | 67 | 93 | 162 | 64.2 | 61.7 | 20.7 |
| `next --review` | 412 | 43 | 59 | 75 | 106 | 40.5 | 37.8 | 20.8 |
| `approve` | 300 | 43 | 64 | 78 | 100 | 67.5 | 63.7 | 20.7 |
| `reject` | 66 | 45 | 61 | 72 | 77 | 71.8 | 68.4 | 20.7 |
| `show` | 189 | 38 | 47 | 52 | 58 | 5.0 | 3.3 | 20.6 |
| `ls` | 97 | 38 | 49 | 53 | 54 | 5.4 | 2.4 | 20.9 |
| `inbox` | 98 | 38 | 57 | 66 | 67 | 11.1 | 8.5 | 21.0 |
| `team` | 30 | 42 | 51 | 60 | 63 | 9.6 | 6.4 | 21.6 |
| `topics` | 22 | 37 | 44 | 44 | 44 | 6.5 | 3.6 | 20.5 |
| `ls --count` | 46 | 37 | 46 | 47 | 48 | 2.1 | 0 | 20.6 |
| `wait --timeout 3s` | 30 | 1,041 | 3,041 | 3,049 | 3,051 | 11.9 | 9.7 | 20.7 |
| `join` | 16 | 68 | 78 | 79 | 79 | 23.8 | 21.8 | 24.7 |
| `leave` | 16 | 37 | 57 | 60 | 61 | 28.0 | 20.0 | 20.5 |

`next` and `next --review` include the calls that found nothing (exit 4: 189 and 46).
Reads show small write averages because whichever process closes the last connection runs
the checkpoint for everyone's earlier writes (see [How bytes written are counted](#how-bytes-written-are-counted)).

| Check | Result |
|---|---|
| Exit codes | 3,442 ok · 246 empty (4) · 0 conflict (3) · **0 busy (5)** · 0 other |
| Every issue done exactly once | 300 of 300 done, each with one `approve` event |
| No issue held by two agents at once | 0 double claims replaying the events table (worker seat and reviewer seat per issue: 366 worker claims, 366 review claims, 366 handoffs, 300 approvals, 66 rejections); 0 overlapping holds from the agents' own timings; 0 lease takeovers |
| Comment ids | 713 ids printed by `comment`, 713 unique, each stored with the printing agent and issue; stored kinds match the successful calls (713 progress, 366 handoff, 300 approval, 66 changes) |
| WAL size | largest seen 4.2 MiB (sampled every 0.1 s); `journal_size_limit` is 8 MiB |
| Bytes written | 113.6 MiB net for 3,688 calls: 31.5 KiB per command, 388 KiB per issue taken from todo to done. `earmark.db` grew by 456 KiB |

## 2. Realistic pacing: 8 agents, 2–6 s between commands, 10 minutes

`python3 scripts/soak.py --agents 6 --reviewers 2 --think 2-6 --minutes 10 --idle 0`, run
twice at the same time on two boards: once with nothing else connected, once with `earmark ui`
and an open `/api/stream` (`--with-ui`), as when someone keeps the board open in a browser.
With the same seed both runs issued the same 1,220 commands. Every agent waits 2–6 s before
each command, so it calls the board about 870 times an hour, once every 4 s.

| | Nothing else connected | `earmark ui` open |
|---|---:|---:|
| Duration | 630 s | 628 s |
| Commands | 1,220 | 1,220 |
| Commands per agent-hour | 871 | 874 |
| Issues done · rejected and reworked | 81 · 7 | 81 · 7 |
| Exit 5 busy · exit 3 conflict | 0 · 0 | 0 · 0 |
| Invariants (as in run 1) | pass | pass |
| `comment` p50 · p99 · max | 59 · 68 · 374 ms | 45 · 50 · 55 ms |
| `approve` p50 · p99 · max | 59 · 65 · 65 ms | 45 · 58 · 62 ms |
| `show` p50 · p99 | 46 · 62 ms | 45 · 53 ms |
| CLI net written | 66.9 MiB | 32.0 MiB |
| per command | 56 KiB | 27 KiB |
| per issue done | 846 KiB | 405 KiB |
| **per agent-hour** | **47.8 MiB** | **22.9 MiB** |
| `earmark ui` process | - | 4 KiB while agents ran, 260 KiB with its final checkpoint |
| `earmark.db` + `-wal` growth | 192 KiB | 200 KiB |
| Largest WAL | 121 KiB | 4.0 MiB |
| Load average (1 min) | 2.7–7.1 | 2.7–7.1 |

With nothing else connected, most commands are the only connection to the board, so the
process that closes it checkpoints its own pages into `earmark.db`, fsyncs the WAL and the
database, and deletes `-wal` and `-shm` (37 MiB of the 104 MiB dirtied in that run was
`-shm`, deleted before writeback). With a long-lived connection open, commits stay in the WAL until the
1,000-page checkpoint copies each changed page once: half the bytes, fsyncs only at that
checkpoint, and a flat latency tail. An agent blocked in `earmark wait` holds a connection the
same way.

## 3. Read latency: 2,000 issues, 10,000 comments

`python3 scripts/soak.py --reads` · load average 2.6–2.8 · board seeded through the Board API
in 3.1 s: 2,000 issues over 30 topics (1,015 todo, 285 in progress, 200 in review, 500 done),
10,000 comments, 13 agents; `earmark.db` 4.2 MiB. One agent (`reader`, assignee of 400
issues) runs each command 30 times, interleaved, with no other connection open.
`comments --new` reads a different issue each time (a fresh cursor); `inbox` returns the
next 50 unseen comments each time. The in-process column times the same Board query without
starting a process.

| Command | p50 ms | p95 ms | max ms | in-process p50 ms | rows | net written per call |
|---|---:|---:|---:|---:|---:|---:|
| `ls` (open issues, first page) | 50 | 54 | 54 | 1.6 | 50 | 0 |
| `ls -t <topic> --ready` | 51 | 54 | 55 | 1.2 | 50 | 0 |
| `show KEY` | 48 | 50 | 51 | 0.08 | 1 | 0 |
| `comments KEY --new` | 64 | 67 | 76 | 0.05 | 4 | 24.0 KiB |
| `inbox` | 65 | 68 | 210 | 5.9 | 50 | 16.3 KiB |

`python3 -c pass` takes 16 ms on this machine, and `ls` without the I/O wrapper 48 ms (2 ms
less). The query itself is 0.05–6 ms; the rest is the interpreter, imports, opening the
board and rendering. `comments --new` and `inbox` advance the reader's cursor, which is one
small write transaction; on a board with no other connection open that process also runs the
checkpoint and two fsyncs when it closes, which is the extra 15 ms or so. Plain reads write nothing
(each one dirties the 32 KiB `-shm` index, which is deleted before it reaches the disk).

## 4. Idle server

At the end of run 1, `earmark ui` is started on the finished board (300 done issues, 1,445
comments), one client opens `/api/stream`, and after 2 s the harness records size and mtime of
`earmark.db`, `-wal` and `-shm` and the server's `/proc/<pid>/io`, waits 60 s and records them
again.

| Check | Result |
|---|---|
| `earmark.db`, `-wal`, `-shm` size and mtime | unchanged |
| Server `write_bytes`, `wchar`, `syscw` | 0, 0, 0 |
| Server `rchar`, `syscr` | 0, 0: the change check reads `PRAGMA data_version` from shared memory |
| Stream | 2 keep-alive pings, 0 change events while idle |
| Liveness after the window | one `earmark comment` reached the open stream in 0.95 s (the watcher checks once per second) |

During the same 60 s, other software on this machine wrote 16.4 MiB to the partition that
holds the board.

## How bytes written are counted

SQLite writes whole 4 KiB pages. A write transaction appends one WAL frame for each page it
changes (table and index leaves, the event row, the issue row whose lease it renews), and a
checkpoint later copies each changed page once into `earmark.db`. With `synchronous=NORMAL`
the fsyncs happen at checkpoints. When the last connection to the board closes, SQLite
checkpoints, fsyncs and deletes the WAL and `-shm`; with a long-lived connection open (the web
UI, or an agent blocked in `earmark wait`) the WAL is instead checkpointed every 1,000 pages,
so a page changed by many commands is copied once. The harness reports, per CLI process:

| Figure | Source | Meaning |
|---|---|---|
| write_bytes | `wait4()` rusage (`ru_oublock` × 512), checked against the child's `/proc/self/io` at exit (difference 0 in every run) | pages the process dirtied: WAL frames, checkpoint copies into `earmark.db`, the `-shm` index |
| cancelled | `cancelled_write_bytes` from the child's `/proc/self/io` | dirty pages dropped before writeback because their file was deleted, mostly the 32 KiB `-shm`. Charged to the process that deletes the file |
| **net** | write_bytes − cancelled | what the CLI sends to the drive, before ext4 journal blocks and the drive's own write amplification |
| file growth | size of `earmark.db` + `-wal` before and after | data the board keeps |
| partition | `/sys/dev/block/…/stat` sectors written | the whole partition during the run, including all other software; context only |

Peak RSS is `VmHWM` from the child's `/proc/self/status` at exit (`ru_maxrss` from `wait4()`
also carries the parent's size at fork). The I/O wrapper adds about 2 ms per call;
`--no-wrap` drops it and keeps the `wait4()` totals.

## What this means for an SSD

At the realistic pace an agent caused 47.8 MiB of writes per hour with nothing else connected
and 22.9 MiB with the web UI open, while calling the board every 4 s; agents doing real work
spend most of their time in the code and call it less often. Take ten such agents working
eight hours a day, every day of the year, at the higher figure: 10 × 8 × 365 × 47.8 MiB is
about 1.5 TB a year. SSD endurance is rated in terabytes written (TBW): low-end 1 TB QLC
drives around 200 TBW, mainstream 1 TB TLC drives around 600 TBW. 1.5 TB is 0.7 % of a
200 TBW rating per year and 0.24 % of 600 TBW; doubled to cover ext4 journal and metadata
blocks, which the per-process counters leave out, it is 1.5 % and 0.5 %. Nothing is written
between commands: no heartbeats and no polling writes, and an idle board with the UI open and
a browser streaming writes nothing (run 4).

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
interpreter. The exit status is 1 when an invariant or the idle check fails.
