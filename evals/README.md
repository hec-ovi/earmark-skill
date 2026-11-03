# Skill evaluations

Scenario files that score the earmark skill on the same tasks in every agent CLI (Claude Code,
Codex, Grok, Antigravity, OpenCode). Each file has the setup commands, the prompt given to the
agent and a pass checklist of board state and commands that anyone can verify afterwards.

| File | Exercises |
|---|---|
| [01-worker-loop.md](01-worker-loop.md) | join, `next` order, one issue at a time, handoff format, skipping blocked and held issues, stop and `leave` |
| [02-review-cycle.md](02-review-cycle.md) | reviewer verifies, rejects with concrete defects, then approves the rework |
| [03-pm-triage.md](03-pm-triage.md) | pm shapes the backlog, cancels a duplicate, assigns by scope, handles a stale claim |
| [04-comment-cursor.md](04-comment-cursor.md) | reading only comments after the cursor or a given id, replying with `--reply-to` |
| [05-idle-stop.md](05-idle-stop.md) | empty work queue: review queue, inbox, three-line summary, no polling |
| [06-held-conflict.md](06-held-conflict.md) | exit 3 on an issue someone else holds: no takeover, take another |

## Running a scenario

1. Put the CLI under test on PATH: `uv tool install <checkout>`, or prepend a directory that
   links `earmark` to the checkout launcher.
2. Run the scenario's setup in one shell. It creates a throwaway git repo in `$EVAL` and a
   board inside it. Setup exports `EARMARK_AS` for its own commands only; start the agent
   from a shell where `EARMARK_AS` is unset.
3. Install the skill into that repo only: `./install.sh --project "$EVAL" --no-cli` from the
   checkout.
4. Start the agent CLI with `$EVAL` as its working directory, give it the prompt verbatim and
   let it run to its own stop. Keep the transcript of the shell commands it ran.
5. Run the scenario's checks in `$EVAL` and mark each checklist item from the check output and
   the transcript.

A scenario passes when every **Required** item holds. **Quality** items are scored
separately. To measure what the skill adds, run the same scenario with the skill not installed
and the same prompt (the baseline).

## Recording results

One row per run in a results table kept with the run artifacts:

| Date | Scenario | CLI | Model | Skill | Required | Quality | Notes |
|---|---|---|---|---|---|---|---|
| 2026-11-03 | 01 | claude-code | (model) | yes | 9/9 | 2/3 | |

Useful evidence: `earmark export` prints the whole board as JSON, including the event log with
the actor of every change, so "who did what, in which order" is checkable without the
transcript.
