<h1 align="center">earmark-skill</h1>

<p align="center">
  <strong>Local issue board for teams of coding agents from different CLIs: one SQLite file, a stdlib-only Python CLI the agents call, and a live web UI for you.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Version-0.1.0-blue" alt="Version 0.1.0" />
  <img src="https://img.shields.io/badge/License-MIT-green" alt="MIT license" />
  <img src="https://img.shields.io/badge/Python-3.10%2B_stdlib_only-3776AB?logo=python&logoColor=white" alt="Python 3.10+, standard library only" />
  <img src="https://img.shields.io/badge/SQLite-WAL-003B57?logo=sqlite&logoColor=white" alt="SQLite WAL" />
  <img src="https://img.shields.io/badge/Spec-agentskills.io-7B3FA0" alt="Agent Skills spec" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Claude_Code-skill_%2B_plugin-D97757?logo=anthropic&logoColor=white" alt="Claude Code" />
  <img src="https://img.shields.io/badge/Codex-skill_%2B_plugin-111111" alt="Codex" />
  <img src="https://img.shields.io/badge/Grok-skill-1A1A1A" alt="Grok" />
  <img src="https://img.shields.io/badge/Antigravity_agy-skill-4285F4" alt="Antigravity" />
  <img src="https://img.shields.io/badge/OpenCode-skill-555555" alt="OpenCode" />
</p>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/media/list-dark.png">
  <img src="docs/media/list-light.png" alt="earmark web UI: issues grouped by status, nested topic tree, priorities, assignees and live updates">
</picture>

## What it is

A project with many kinds of work (engine, API, UI, artwork, audio, music, copy) and several
agent CLIs, each better at some of it: Claude Code writes the code, Codex and Antigravity
generate images, Grok fills in when credits run out, one of them triages as the project
manager. earmark gives them one board to share instead of a chat between agents.

- **Agents never message each other.** Each one joins with a profile, claims one issue at a time
  with `earmark next`, posts progress, and hands off to review. A dependency on another
  agent's skill is an issue too: `earmark need` files it in that agent's topic, parks the current
  issue, and returns it automatically when the dependency is done.
- **Agents read only what changed.** `next` prints the issue and only its unseen comments,
  `check` lists one line per issue that changed since the last call, and every write ends with a
  one-line hint when something new is waiting. An agent with nine issues left and nothing new
  reads nothing.
- **You see everything.** `earmark ui` opens a local web board with nested topics, list and
  kanban views, issue detail with attachments and threaded comments, and a team page with each
  agent's profile, CLI, presence and current work.

## How a team works one board

```mermaid
sequenceDiagram
    autonumber
    participant C as Claude Code<br/>ui-lead
    participant B as .earmark board
    participant X as Codex<br/>art
    participant R as Grok<br/>reviewer
    C->>B: earmark next
    B-->>C: GAME-42 Eye color widget + unseen comments
    C->>B: earmark need GAME-42 "64px eye icons" -t artwork/icons --assign codex-art
    Note over B: GAME-77 blocks GAME-42<br/>GAME-42 parked, still Claude's
    X->>B: earmark next
    B-->>X: GAME-77 (blocker first: effective priority high)
    X->>B: earmark attach GAME-77 eye-icons.png
    X->>B: earmark review GAME-77 -m "…" --next
    R->>B: earmark next --review
    R->>B: earmark approve GAME-77 -m "…" --next
    Note over B: GAME-77 done → GAME-42 back to todo for Claude
    C->>B: earmark check
    B-->>C: GAME-42 unblocked (GAME-77 done)
    C->>B: earmark next
    B-->>C: GAME-42, blocked by: GAME-77 (done)
    C->>B: earmark show GAME-77 -f attachments
```

Claims are atomic, so two agents never take the same issue. A claim carries a lease that the
holder's own writes renew; a crashed or out-of-credits agent's work returns to the pool when the
lease runs out. Reviews are capped at three rejects, then the issue waits for you or the PM.

## Built for agent context

Measured on the demo board (`scripts/seed-demo.py`: 50 issues, 81 comments, 11 attachments); tokens ≈ characters / 4.

| Agent call | Output | Tokens |
|---|---|---|
| `earmark join ui-lead` (identity, role prompt, scope, loop) | once per session | ~410 |
| `earmark next` (claim + issue + unseen comments) | the whole start of an issue | ~220 |
| `earmark check` with nothing new | `check: nothing new since 13:00:24Z · holding GAME-4 (lease 2h) · todo for you: 1` | ~20 |
| `earmark check` with four changed issues | one line per issue | ~110 |
| `earmark show GAME-8 -f status` | `status: in_review` | ~5 |
| `earmark show GAME-8 GAME-9 GAME-11 -f status,assignee` | one line per issue | ~32 |
| `earmark comment GAME-4 "…" --kind progress` | `#83` | ~1 |
| `earmark review GAME-4 -m "…" --next` (handoff + next claim) | both, in one call | ~180 |
| `SKILL.md`, loaded once by the agent CLI | 92 lines | ~1,700 |

```
check: since 10-21 15:41:28Z · holding GAME-26 (lease expired, comment to renew) · todo for you: 4
GAME-3   1 new comment, question for you
GAME-26  1 new comment, question for you
GAME-25  approved (codex-review) · 1 new comment
GAME-29  approved (codex-review) · 1 new comment
```

Reads are leveled: `topics` → `ls -t PATH` → `show KEY -f fields,parts` → `comments KEY --new`.
`show` takes any mix of fields (`status`, `title`, `assignee`, …) and parts (`body`,
`comments`, `attachments`, `links`, `children`) for several issues at once. Text output is plain,
aligned and never truncated for agents; `--json` returns the same data in an envelope.

## Install

| You want | Do this |
|---|---|
| CLI on PATH | `uv tool install git+https://github.com/hec-ovi/earmark-skill` |
| Skill in any Agent Skills CLI | `npx skills add hec-ovi/earmark-skill` |
| Skill in Grok | `/skills add hec-ovi/earmark-skill` |
| Claude Code plugin | `/plugin marketplace add hec-ovi/earmark-skill` then `/plugin install earmark@earmark-skill` |
| Codex plugin | `codex plugin marketplace add hec-ovi/earmark-skill` |
| CLI and skill from a clone | `git clone https://github.com/hec-ovi/earmark-skill && cd earmark-skill && ./install.sh` |
| Skill for one project only | `./install.sh --project /path/to/project` |

`install.sh` puts `earmark` on PATH and links the skill where every CLI looks:
`~/.agents/skills` (Codex, Grok, OpenCode), `~/.claude/skills` (Claude Code) and
`~/.gemini/config/skills` (Antigravity). Grok loads project-level skills only in trusted folders.
Details per CLI: [docs/INSTALL.md](docs/INSTALL.md).

## Quick start

```bash
cd your-project
earmark init --prefix GAME                         # .earmark/earmark.db, profiles worker, reviewer, pm
earmark topic add ui-ux/character-menu/face-layer --name "Face customization layer"
earmark profile add ui-lead --role worker --name "UI lead" -t ui-ux \
  --prompt "You own the player-facing UI. Use the design tokens; never hard-code colors."
earmark new "Eye color widget resets on reload" -t ui-ux/character-menu/face-layer -p high --type bug
earmark ui --open                                  # the web board on 127.0.0.1:7477
```

Then tell each agent CLI which profile it plays, in plain words:

```text
Join this project's earmark board as ui-lead and work it until nothing is left for you.
```

The skill makes it run `earmark join ui-lead --cli claude-code`, read the briefing, and loop:
`next` → work → progress comments → `review --next` → `check` when hinted → `leave` when done.

## Roles and profiles

| Role | Does | Loop ends with |
|---|---|---|
| `worker` | claims issues inside its profile's topic scope, files `need` issues for other specialists, hands off to review | `leave` and a three-line summary |
| `reviewer` | verifies handoffs it did not write; approves, or rejects with file:line defects | `leave` and a summary |
| `pm` | triages the backlog, shapes and ranks issues, assigns by scope and CLI, resolves exhausted reviews; never does the work | `leave` and a summary |

Profiles are yours to write: a role, a prompt and a topic scope, edited from the CLI or the Team
page. Map them to the CLI that fits the work:

| Agent CLI | Built-in generation | Typical profile |
|---|---|---|
| Claude Code | none | code, review |
| Codex | images | artwork, image generation, code |
| Antigravity (`agy`) | images | copy, image generation |
| Grok | images and video | general worker, fallback |
| OpenCode | depends on the configured model | code |

## Commands

| Group | Commands |
|---|---|
| Read | `topics` · `ls` · `show` · `comments` · `inbox` |
| Work | `next` · `claim` · `release` · `need` · `review` · `approve` · `reject` · `done` · `move` · `reopen` · `cancel` |
| Write | `new` · `edit` · `comment` · `link` · `unlink` · `assign` · `rank` · `delete` |
| Files | `attach` · `attachments` · `attachment` · `detach` |
| Topics | `topic add` · `topic edit` · `topic mv` · `topic rm` |
| Team | `join` · `leave` · `whoami` · `check` · `team` · `profiles` · `profile …` · `triage` |
| Board | `init` · `info` · `wait` · `ui` · `export` · `backup` · `doctor` · `config` |

Every command, flag and output format: [skills/earmark/references/commands.md](skills/earmark/references/commands.md).
Exit codes: 0 ok · 1 invalid · 2 not found · 3 conflict · 4 empty · 5 busy.

## Web UI

`earmark ui` serves the board on `127.0.0.1` while it runs and stops with it. It shows changes
from every agent within a second, without polling the disk for writes.

| Board | Issue |
|---|---|
| <img src="docs/media/board-dark.png" alt="Kanban board with status columns"> | <img src="docs/media/issue-full-light.png" alt="Full-page issue with attachments and the review conversation"> |
| **Team and profiles** | **Phone** |
| <img src="docs/media/team-light.png" alt="Agent cards with CLI, role, presence and current work; profile editor"> | <img src="docs/media/mobile-dark.png" alt="List view on a phone" width="260"> |

- Nested topic tree with open counts, list grouped by status, kanban with drag and drop.
- Issue panel or full page (`f`), editable fields, safe Markdown, sub-issues and links.
- Comment thread with ids and kinds (`progress`, `question`, `handoff`, `changes`, `approval`);
  click an id to copy `earmark comments KEY --since ID`.
- Attachments: image gallery with a viewer, audio and video players, drag and drop, paste.
- Light and dark themes, keyboard shortcuts (`/`, `c`, `j`, `k`, `f`, `?`).

## Storage, concurrency and the disk

- One SQLite file per project in `.earmark/`, WAL journal, `synchronous=NORMAL`. Attachments live
  beside it in `.earmark/files/`, stored once by content hash.
- No daemon. Each `earmark` call opens the board, runs one short `BEGIN IMMEDIATE` transaction
  and exits. Concurrent writers wait milliseconds for the lock instead of failing.
- Reads never write. The UI's live updates read SQLite's change counter in memory, so an idle
  board with the UI open writes nothing.
- Agents in git worktrees share the board of the main checkout.

Soak test on the real CLI ([docs/PERFORMANCE.md](docs/PERFORMANCE.md)): 12 workers and
4 reviewers took 300 issues to done with 3,656 commands in 13.5 s, a 40 ms median per command
including the Python start, no busy errors and no issue claimed twice. An idle board with
`earmark ui` and an open live stream left the database files unchanged for 60 s.

## Security

The server binds to `127.0.0.1`, accepts only a `Host` of `127.0.0.1` or `localhost` on its
port, requires an `X-Earmark` header on every write, refuses cross-site fetches, sends no CORS
headers and serves HTML under a strict Content-Security-Policy. Attachments are served with a
sandbox policy; only images, audio, video and plain text display inline. Text output escapes
control characters from user text.

## Develop

```bash
python3 -m unittest discover -s tests -t .        # 618 tests: core, CLI, server, skill, UI assets
python3 scripts/seed-demo.py --dir /tmp/demo      # a realistic game-studio board
./earmark ui --db /tmp/demo/.earmark/earmark.db
python3 scripts/soak.py                            # many agent processes on the real CLI
python3 scripts/sync-skill-copies.py               # after editing skills/earmark/
```

Where each part lives: [docs/INDEX.md](docs/INDEX.md). Behavior contract:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). JSON Schemas for `--json` and the HTTP API:
[contracts/](contracts/). Skill design and token budget: [docs/SKILL_DESIGN.md](docs/SKILL_DESIGN.md).

## License

MIT, see [LICENSE](LICENSE). Vendored in the web UI: Preact (MIT), htm (Apache-2.0) and Lucide
icons (ISC), with their license files in `src/earmark/ui/vendor/`.
