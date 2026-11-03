---
name: earmark
description: >-
  Works a local issue board shared by a team of coding agents through the
  earmark CLI. Joins the board as a worker, reviewer or pm profile, claims the
  next issue in scope, posts progress, hands work off to review, approves or
  rejects handoffs, triages and assigns the backlog, and reads only new
  comments. Use when the project has a .earmark/ board, when the user mentions
  issues, tickets, a backlog, the board, a review queue or earmark, or says
  things like "join as worker", "join as reviewer", "pick up the next issue",
  "work the board", "check the review queue", "triage the backlog", "file an
  issue for this" or "what are the agents working on". Same commands in Claude
  Code, Codex, Grok, Antigravity and OpenCode.
license: MIT
compatibility: >-
  Requires the earmark CLI (Python 3.10+, stdlib only) and a board created
  with earmark init in the project. Runs from any agent shell; no MCP server.
metadata:
  author: Hector Oviedo
  version: "0.1.0"
allowed-tools: Bash(earmark:*)
---

# earmark

The project's agents share one issue board in `.earmark/`, reached only through the `earmark`
CLI. Output is plain text sized for agents; errors print `error:` and a `hint:` to run next.

## Start

1. Resolve the CLI once: `earmark` on PATH, else the `earmark` launcher beside this file. Neither:
   ask the user to run `uv tool install git+https://github.com/hec-ovi/earmark-skill`. Never install it yourself.
2. Join once: `earmark join <profile> --cli <claude-code|codex|grok|agy|opencode>`, with the profile
   the user named (`earmark profiles`), else `worker`. Omit `--as` unless the user gave you a handle.
   The briefing prints your handle, role prompt, scope, assigned issues and loop: follow them.
   On `no board`, run `earmark init` only if the user asks for one.
3. Pass `--as <handle>` on every later command. Each shell call starts clean, so an exported
   `EARMARK_AS` is gone by the next one and writes fail without it. Lost it: `earmark team`.

## Read in levels

Load the least that answers the question. Never list the whole board or scan the codebase for
work: the issue you hold names its topic and files.

| Need | Command |
|---|---|
| Where work lives | `earmark topics --depth 2` |
| Issues in one area | `earmark ls -t <topic> --ready` |
| One issue | `earmark show KEY` (fields, description, last comment id) |
| Its discussion | `earmark comments KEY --new` (only unseen; or `--since ID`, `--last N`) |
| New for you anywhere | `earmark inbox` |
| Who is doing what | `earmark team` |

## Worker loop

1. `earmark next` claims your best ready issue and prints it. Exit 4: go to 6.
2. `earmark comments KEY --new`, then read only the code the issue needs. A rejected issue comes
   back to you first; its `changes` comment is your checklist.
3. Work that issue alone. At each milestone: `earmark comment KEY "<done, next>" --kind progress`.
   Any write renews your lease (120 min by default); a silent claim goes stale and is taken.
4. Need a decision: comment `--kind question` with `@<handle>` and go on with what you can.
   Cannot go on at all: `earmark move KEY blocked -m "<blocked by what>"`, then go to 1.
5. Done: `earmark review KEY -m "<what changed> · <files> · <how to verify>"`, then go to 1.
6. Nothing to claim: `earmark next --review` claims someone else's handoff (never yours).
   Review it as in reviewer steps 2 and 3, then go to 1.
7. Still exit 4: `earmark inbox`; answer with `earmark comment KEY "..." --reply-to ID`.
8. Nothing left: stop and report in three lines: done, in review, open or blocked. Only when told
   to stay on duty: `earmark wait --timeout 9m` (allow 10 min for that call), then go to 1.

## Reviewer loop

1. `earmark next --review` claims the next handoff. Exit 4: worker steps 7 and 8.
2. `earmark comments KEY --new` shows the `handoff` note. Verify exactly what it claims: run its
   tests or steps and read every changed file.
3. Pass: `earmark approve KEY -m "<what you checked>"`.
   Fail: `earmark reject KEY -m "<each defect: file:line, expected, actual>"`; it returns to the
   same worker. Never fix it yourself. Then go to 1.

## PM loop

1. `earmark triage` lists untriaged backlog, unassigned todo, stale claims, review queue age,
   blocked, overdue and idle agents. Work it top to bottom.
2. Shape: `earmark edit KEY -t <topic> -p <priority> --type <type>`, then `earmark move KEY todo`.
   Duplicate: `earmark link KEY duplicates KEY2`, then `earmark cancel KEY -m "duplicate of KEY2"`.
3. Assign by scope: `earmark assign KEY <handle>` to the idle agent whose profile covers the topic.
   Stale claim: ask the holder in a comment; no answer: `earmark move KEY todo -m "<why>"`, then assign.
4. Order: `earmark rank KEY --top` or `--before KEY2`. Split: `earmark new "<title>" --parent KEY`.
5. New work: `earmark new "<title>" -b "<goal · acceptance>" -t <topic> -p <priority>`.
6. Triage clean: stop with a three-line summary, or wait as in worker step 8.

## Exit codes

| Code | Meaning | Do |
|---|---|---|
| 1 | invalid request, identity, permission | read `hint:` and fix the command once |
| 2 | not found | check the key or topic; the error lists close matches |
| 3 | conflict: someone else holds it, or the version moved | `earmark next` for another, or `show` and redo |
| 4 | empty: nothing to claim, or wait timed out | next step of your loop |
| 5 | busy: the board stayed locked | retry the same command after a few seconds |

## Rules

- One claimed issue at a time. To drop one: `earmark release KEY -m "<why>"`.
- Comments are short and concrete: what changed, paths, ids (`#1190`), `--reply-to ID`. No log dumps.
- Out-of-scope finding: `earmark new "<title>" -b "<evidence>" -t <topic>`, then carry on; never fix it in passing.
- Never edit the code, fields or status of an issue someone else holds; comment on it instead.
- Touch the board only through `earmark`, never `.earmark/earmark.db` directly.
- Ending the session: `earmark leave` returns your unfinished work to the queue.

References: [commands.md](references/commands.md) (every command, flag and output format) ·
[workflow.md](references/workflow.md) (statuses, leases, `next` order, review flow, cursors).
