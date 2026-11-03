---
name: earmark
description: >-
  Works a local issue board shared by a team of coding agents through the
  earmark CLI. Joins the board as a worker, reviewer or pm profile, claims the
  next issue in scope, reads only what changed, hands work off to review,
  files work only another specialist can do (images, audio, copy) as blocking
  issues, approves or rejects handoffs, and triages and assigns the backlog.
  Use when the project has a .earmark/ board, when the user mentions issues,
  tickets, a backlog, the board, a review queue or earmark, or says things like
  "join as worker", "join as reviewer", "pick up the next issue", "work the
  board", "check the review queue", "triage the backlog", "file an issue for
  this" or "what are the agents working on". Same commands in Claude Code,
  Codex, Grok, Antigravity and OpenCode.
license: MIT
compatibility: >-
  Requires the earmark CLI (Python 3.10+, stdlib only) and a board created
  with earmark init in the project. Runs from any agent shell; no MCP server.
metadata:
  author: Hector Oviedo
  version: "0.1.1"
allowed-tools: Bash(earmark:*)
---

# earmark

The project's agents share one issue board in `.earmark/`, reached only through the `earmark`
CLI. Agents never message each other: work, dependencies and handoffs live on the board. Output
is plain text sized for agents; errors print `error:` and a `hint:` to run next.

## Start

1. `earmark info`. Command not found: ask the user to run
   `uv tool install git+https://github.com/hec-ovi/earmark-skill`. Never install it yourself.
2. `earmark join <profile> --cli <claude-code|codex|grok|agy|opencode>` with the profile the user
   named, else `worker`. The briefing prints your handle, role prompt, scope and loop: follow them.
   The same command in a later session resumes your handle (`(resumed)`) with its work and cursors.
   On `no board`, run `earmark init` only if the user asks for one.
3. Pass `--as <handle>` on every later command; each shell call starts clean. Lost it: `earmark team`.

## Spend tokens only on what changed

| Need | Command |
|---|---|
| Start work: claim, the issue, its unseen comments | `earmark next` |
| What changed for you, one line per issue | `earmark check` |
| Just some fields or parts, several issues at once | `earmark show KEY [KEY…] -f status,title` · `-f comments --new` · `-f links` · `-f attachments` |
| Files (images, audio, logs) | `earmark attachments KEY`, then open the printed paths with your own tools |
| One area, rarely | `earmark topics PATH --depth 2` · `earmark ls -t PATH --ready` |

Run `earmark check` when a command ends with `· N changes for you`, and between issues. Never list
the whole board, never re-read what you already saw, and read only the code your issue names. In a
new session the unseen comments are enough: your cursor marks what you read before.

## Worker loop

1. `earmark next` claims your best ready issue and prints it with its unseen comments. Exit 4: go to 6.
2. Work that issue alone. On long work, `earmark comment KEY "<done, next>" --kind progress` every
   30 to 60 min: it renews your lease (120 min by default); a silent claim goes stale and is taken.
3. Needs something only another specialist makes (an image, audio, copy):
   `earmark need KEY "<short title>" -t <topic> -b "<format, size, where it goes>" [--assign <handle>]`.
   KEY is parked and returns to your `next` when that issue is done; go to 1.
   Waits on an issue that already exists: `earmark link KEY2 blocks KEY`, then
   `earmark move KEY blocked -m "waiting on KEY2"`; it returns to you when KEY2 is done. Linking
   another agent's issue is fine; it does not touch their work.
   Needs a decision: `earmark comment KEY "<question>" --kind question` with `@<handle>`; go on with
   what you can. Blocked by something outside the board: `earmark move KEY blocked -m "<what>"`.
4. Done: deliverables first (`earmark attach KEY FILE…`), then
   `earmark review KEY -m "<what changed> · <files> · <how to verify>" --next`: it hands off and
   claims your next issue in the same call. Exit 4: go to 6.
5. A resumed issue shows `blocked by: KEY2 (done)`: `earmark show KEY2 -f comments,attachments --last 3`,
   then open the printed paths.
6. `earmark next --review` claims someone else's handoff (never yours); review it as below.
7. Still exit 4: `earmark check` and handle what needs you (questions, rejections).
8. Nothing left: `earmark leave`, then report in three lines: done, in review, open or blocked.
   Only when told to stay on duty: `earmark wait --timeout 2m` (it fits every agent shell's command
   limit), then go to 1; it also returns when the board has no open issues.

## Reviewer loop

1. `earmark next --review` claims the next handoff and prints it with the `handoff` note. Exit 4:
   worker steps 7 and 8.
2. Verify exactly what the note claims: run its tests or steps, read every changed file.
3. Pass: `earmark approve KEY -m "<what you checked>" --next`. Fail:
   `earmark reject KEY -m "<each defect: file:line, expected, actual>" --next`; it returns to the
   same worker. Never fix it yourself. The third reject parks it for the pm or owner.

## PM loop

1. `earmark triage`: untriaged backlog, unassigned todo, stale claims, review queue, blocked,
   overdue, exhausted reviews, idle agents. Work it top to bottom.
2. Shape: `earmark edit KEY -t <topic> -p <priority> --type <type>`, then `earmark move KEY todo`.
   Duplicate: `earmark link KEY duplicates KEY2`, then `earmark cancel KEY -m "duplicate of KEY2"`.
3. Assign by scope after a fresh `earmark team`: `earmark assign KEY <handle>` whose profile and CLI fit.
   Stale claim: `earmark move KEY todo -m "@<holder> lease expired: <why>"`, then assign.
4. Exhausted review: clarify the issue, then reassign, split (`earmark new "<title>" --parent KEY`)
   or cancel it. Order the queue with `earmark rank KEY --top` or `--before KEY2`.
5. Triage clean: `earmark leave` and a three-line summary, or wait as in worker step 8.

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
- Comments inform; they are not a chat. Answer only a question addressed to you, once. Never post
  acknowledgements or thanks. Two exchanges without agreement: leave it to the pm or owner.
- Comments are short and concrete: what changed, paths, ids (`#1190`). No log dumps; attach them.
- Out-of-scope finding: `earmark new "<title>" -b "<evidence>" -t <topic>`; never fix it in passing.
- Never edit the code, fields or status of an issue someone else holds; comment on it instead.
- Run `earmark` plainly in the foreground (no variables, pipes, `echo $?`; the exit code is in your
  tool result), `wait` only when on duty, and a command's help only after an error.
- Touch the board only through `earmark`, never `.earmark/` directly.

References: [commands.md](references/commands.md) (every command, flag and output format) ·
[workflow.md](references/workflow.md) (statuses, leases, `next` order, review flow, cursors).
