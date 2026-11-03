# 01 Worker loop

The agent joins as a worker, takes ready issues in priority order one at a time, hands each
off with a verifiable note, skips work that is blocked or held by another agent, then stops.

## Setup

```bash
export EVAL="$(mktemp -d)" && cd "$EVAL" && git init -q
export EARMARK_AS=lead
earmark init --prefix EV --owner lead
earmark topic add docs --name Docs
earmark topic add app --name App
earmark new "Create NOTES.md with the line: earmark eval" -t docs -p high --type task \
  -b "Acceptance: NOTES.md at the repo root contains exactly the line 'earmark eval'."   # EV-1
earmark new "Create app/VERSION containing 0.1.0" -t app -p medium --type task \
  -b "Acceptance: app/VERSION holds '0.1.0' and a trailing newline."                   # EV-2
earmark new "Wire the release script to app/VERSION" -t app -p urgent --type task \
  -b "Needs EV-2 done first."                                                           # EV-3
earmark link EV-2 blocks EV-3
earmark new "Rename the app folder" -t app -p urgent --type chore                    # EV-4
earmark join worker --as busy-1 --cli codex
earmark claim EV-4 --as busy-1
unset EARMARK_AS
```

## Prompt

> Join this project's earmark board as a worker and work through the issues until nothing is
> left for you.

## Pass checklist

Required:

- [ ] Ran `earmark join worker` once; every later `earmark` write in the transcript carries
      `--as <handle>` (at most one `identity_required` error, fixed on the next call).
- [ ] Claimed EV-2 first (medium, effective urgent because it blocks EV-3) and EV-1 second (high), each through `earmark next`.
- [ ] Never had two issues `in_progress` at once (event order in `earmark export`).
- [ ] EV-1 and EV-2 are `in_review`, assignee = the agent's handle.
- [ ] Each handoff comment names the file changed and how to verify it.
- [ ] NOTES.md and app/VERSION match their acceptance lines; `git status` shows no other
      changed files.
- [ ] No events by the agent on EV-3 (blocked) or EV-4 (held by busy-1).
- [ ] After the queue emptied it ran `earmark next --review` and `earmark check`, and did not
      run `earmark wait`.
- [ ] Ran `earmark leave` at the end; `earmark team` shows the agent offline.

Quality:

- [ ] Final message is a summary of about three lines (done, in review, open or blocked).
- [ ] Did not run `earmark export`, `earmark ls --status all` or open `.earmark/earmark.db`.
- [ ] Comments are short; no pasted command output beyond a failing line.

## Checks

```bash
cd "$EVAL"
for k in EV-1 EV-2 EV-3 EV-4; do earmark show $k -f status,assignee; done
earmark comments EV-1 --last 5
earmark comments EV-2 --last 5
earmark team
git status --short
cat NOTES.md app/VERSION
```

Expected: EV-1 and EV-2 `in_review` with the agent as assignee, EV-3 `todo` unassigned, EV-4
`in_progress` by busy-1.
