# 05 Idle board: review, check, stop

Nothing is ready for a worker, one handoff from another agent waits for review. The agent
must fall through the loop (work queue, review queue, check), review the handoff and stop with
a short summary instead of polling.

## Setup

```bash
export EVAL="$(mktemp -d)" && cd "$EVAL" && git init -q
printf 'earmark\n' > NAME
export EARMARK_AS=lead
earmark init --prefix EV --owner lead
earmark topic add docs --name Docs
earmark new "Add a NAME file with the project name" -t docs -p medium --type task \
  -b "Acceptance: NAME at the repo root contains 'earmark'."                            # EV-1
earmark join worker --as w-1 --cli codex
earmark next --as w-1
earmark review EV-1 --as w-1 -m "Added NAME · NAME · cat NAME prints earmark"
unset EARMARK_AS
```

## Prompt

> Join the earmark board as a worker and keep the board moving.

## Pass checklist

Required:

- [ ] `earmark next` returned exit 4 and the agent moved on without retrying it in a loop.
- [ ] Ran `earmark next --review`, claimed EV-1 and verified it (`cat NAME` or equivalent).
- [ ] EV-1 is `done` with an `approval` comment by the agent.
- [ ] Ran `earmark check` after the review queue was empty.
- [ ] Did not run `earmark wait` (it was not told to stay on duty).
- [ ] Final message is a summary of about three lines.
- [ ] Ran `earmark leave`.

Quality:

- [ ] Total `earmark` calls after `join` stay under 12.

## Checks

```bash
cd "$EVAL"
earmark show EV-1 -f status,assignee,reviewer
earmark comments EV-1 --last 2
earmark team
```
