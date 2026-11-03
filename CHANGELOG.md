# Changelog

## 0.1.1 (2026-11-03)

Changes from two live team runs on one board: five Claude subagents, and Claude Code, Codex,
Antigravity and Grok working in parallel.

- `join` without `--as` resumes the offline handle of the same profile and CLI, with its work
  and cursors (`(resumed)` in the briefing).
- `check` header counts what the role can act on: `ready for you` and `waiting on blockers`, or
  `reviews waiting`. Events and comments the agent already saw through `next` or `show --new` are
  not reported again. Link changes name the link (`+blocks SHOP-5`). A pm's feed covers its whole
  scope.
- `question for you` only for questions addressed to you; other @mentions read `mentions you`.
- `wait` follows the check feed (`--for changes`), wakes for a pm on board-wide changes, re-checks
  when a lease lapses, and returns `wait: board` when nothing is open.
- Empty `next` lines name reviews and changes for you; `ls --fields` takes every `show` field;
  reads never bring back an agent that left; over-long titles hint at `-b`.
- Skill: short `need` titles with details in `-b`, waiting on an existing issue with `link` and
  `move blocked`, two-minute waits that fit agent shells, plain foreground commands.

## 0.1.0 (2026-11-03)

First release.

### Board
- One SQLite file per project in `.earmark/` (WAL, `synchronous=NORMAL`, no daemon), found from
  any subdirectory and from git worktrees of the same repository.
- Issues with type, priority, labels, due dates, parents, `blocks` / `duplicates` / `relates`
  links, manual rank and optimistic versions; nested topics as materialized paths.
- Atomic `next`: pick and claim in one transaction, ordered by assignment, effective priority
  (a blocker takes the priority of what it blocks), blockers first, due date and rank.
- Leases renewed by the holder's own writes; stale claims return to the pool with a `takeover`.
- `need`: work only another specialist can do becomes a blocking issue; the parked issue returns
  to its owner automatically when the blocker is done.
- Review flow with handoff, approval and changes comments, and a cap on review rounds after
  which the owner or a pm decides.
- Global comment ids, per-issue and inbox cursors, @mentions.
- `check`: a per-agent change feed, one line per changed issue; write commands end with a hint
  when it has something.
- Attachments stored once by content hash in `.earmark/files/`.
- Profiles with a role (`worker`, `reviewer`, `pm`), prompt and topic scope; `join`, `leave`,
  presence, `team`, `triage`.

### CLI
- Every command in `skills/earmark/references/commands.md`: plain aligned text for agents,
  `--json` envelope, exit codes 0 to 5, errors with a `hint:`.
- `show` selects fields and parts (`body`, `comments`, `attachments`, `links`, `children`) for
  several issues in one call; `review`, `approve`, `reject`, `done` and `release` take `--next`.
- `wait` blocks on read-only change detection; `backup`, `export`, `doctor`, `config`.

### Web UI
- `earmark ui` on 127.0.0.1: topic tree, list and kanban views, issue panel and full page,
  comment thread, attachment gallery and players, team and profile editor, live updates, light
  and dark themes. No build step; Preact, htm and Lucide icons vendored.

### Skill
- `SKILL.md` (Agent Skills spec fields only) with the worker, reviewer and pm loops, synced to the
  repo root, a Claude Code plugin and a Codex plugin; `install.sh` for the CLI and the skill
  links of Claude Code, Codex, Grok, Antigravity and OpenCode.
- Evaluation scenarios in `evals/`, run against Claude Code, Codex, Grok and Antigravity.
