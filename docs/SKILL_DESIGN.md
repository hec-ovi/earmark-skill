# Skill design notes

Why the agent-facing skill is shaped the way it is. Checked against the
[Agent Skills specification](https://agentskills.io/specification) and Anthropic's skill
authoring guidance as of November 2026.

## Progressive disclosure

| Level | What | Loaded | This skill |
|---|---|---|---|
| 1 Metadata | `name` + `description` | every session, for every installed skill | 717 chars, about 180 tokens (limit 1024 chars) |
| 2 Instructions | `SKILL.md` body | when the skill activates | 86 lines, about 1.3k tokens (spec guidance: under 500 lines and 5k tokens) |
| 3 References | `references/commands.md`, `references/workflow.md` | only when the body sends the agent there | about 2.2k and 0.9k tokens |
| Briefing | profile prompt + role loop | printed by `earmark join` | about 0.4k tokens per role |

A full activation costs about 1.5k tokens. The test suite holds the body to 100 lines and
2.5k tokens so it cannot grow quietly.

## Frontmatter

- Spec keys only: `name`, `description`, `license`, `compatibility`, `metadata` (author and
  version as strings), `allowed-tools`. Harness-specific keys such as `when_to_use` are left
  out because some agent CLIs reject unknown keys; the "when" lives in the description.
- `allowed-tools: Bash(earmark:*)` is experimental in the spec. Claude Code uses it to run
  `earmark` without a permission prompt while the skill is active; other CLIs ignore it.
- The description is third person and states what the skill does, then when to use it, with
  the words users actually type: issues, tickets, backlog, board, review queue, "join as
  worker", "pick up the next issue", "what are the agents working on". It also names the
  `.earmark/` directory, so an agent that sees the board in a listing activates the skill.
- Block scalars (`>-`) keep the YAML valid without quoting, and the tests parse the
  frontmatter by hand to enforce that small subset.

## Body

The body holds only what the model cannot infer from the CLI: the default path, the order of
the loop, when to stop and the house rules. Flags, output formats and the status table stay in
`references/`, and the CLI's own `hint:` lines carry recovery steps, so the skill does not
restate them.

1. **Start** resolves the CLI once and never installs it. The skill prefers `earmark` on PATH,
   then the launcher beside `SKILL.md`, then asks the user to run `uv tool install`.
   Installing packages is the user's decision.
2. **Read in levels** maps a question to the cheapest command that answers it: topics, `ls`,
   `show`, `comments --new`, `inbox`. On a board with hundreds of issues and thousands of
   comments, the agent reads its own issue and the comments it has not seen, nothing else.
   The same discipline applies to code: the issue names its topic and files.
3. **Loops** are numbered checklists, one per role, because agents follow ordered steps more
   reliably than prose and each step is one command with a clear exit code.
4. **Exit codes** turn every outcome into a next step. 3 means someone else holds the issue
   (pick another), 4 means nothing to do (move down the loop), 5 means the database stayed
   locked (retry).
5. **Rules** are the team norms that keep many agents from stepping on each other.

## Why `--as` on every command

Agent shells do not keep state between tool calls: Claude Code, Codex, Grok, Antigravity and
OpenCode each run a command in a fresh shell, so `export EARMARK_AS=...` is gone by the next
call. The CLI refuses an anonymous write from a non-terminal (`identity_required`) rather than
guessing, so the skill tells the agent to pass `--as <handle>` explicitly each time and to
recover it with `earmark whoami` if lost. `join` without `--as` lets the board pick a free
handle (`worker-2`), so two sessions of the same CLI never share an identity by accident.

## Why the loop and its stop conditions

The user wants agents that keep the board moving without being prompted for each issue: finish
one, take the next, review others' work when nothing is ready, answer the inbox. The loop
encodes that order. `next` claims atomically, so agents never collide, and its exit 4 message
gives the review queue and inbox counts that point to the next step.

Stopping is explicit. When the worker queue, the review queue and the inbox are all empty, the
agent stops with a three-line summary instead of polling, because an idle loop burns tokens
and shell time for nothing. `earmark wait --timeout 9m` is reserved for an agent the user put
on duty. It blocks read-only on SQLite's `data_version`. 9 minutes stays under the 10-minute
maximum of Claude Code's shell tool; the skill tells the agent to allow 10 minutes for that
call, since default shell timeouts are shorter.

Leases need no heartbeat: any write by the holder renews them. The progress comment at each
milestone does two jobs, telling the team what changed and keeping the claim alive. A silent
agent's issue goes stale after `lease_minutes` and returns to the queue.

## Profiles and loops

`src/earmark/profiles/{worker,reviewer,pm}.md` hold each role's prompt (name, role and Lucide
icon in a frontmatter block, then 4 to 10 lines in the second person), and
`src/earmark/profiles/loops/<role>.md` holds the loop that `earmark join` prints after it.
`earmark init` seeds the profiles into the board, where a pm or the owner can edit them.
An agent told only to run `earmark join worker` therefore gets its role and loop even without
the skill installed. The tests check that every command and flag in `SKILL.md`, the loops and
the Codex command exists in `references/commands.md`, and, once the CLI runs, in
`earmark <command> --help`.

## Packaging

One canonical package in `skills/earmark/`, mirrored to the repo root and both plugin trees by
`scripts/sync-skill-copies.py`; `tests/test_skill_package.py` fails on drift. `install.sh`
symlinks the canonical folder, so a `git pull` updates the skill in every CLI at once.

## Evaluations

`evals/` holds scenario files: worker loop, review cycle, pm triage, comment cursor and the
idle stop. Each has setup commands, the prompt given to the agent and a pass checklist of
board state, so the skill can be scored on the same tasks in every CLI.
