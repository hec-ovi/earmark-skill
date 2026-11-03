# 04 Comment cursor

The agent has already read an issue's discussion. Other agents then post two comments, one a
question for it. It must fetch only what is new, answer with `--reply-to`, and in phase 2
fetch comments after a given id on request.

## Setup

```bash
export EVAL="$(mktemp -d)" && cd "$EVAL" && git init -q
export EARMARK_AS=lead
earmark init --prefix EV --owner lead
earmark topic add api --name API
earmark new "Pick a pagination style for GET /items" -t api -p medium --type task     # EV-1
earmark join worker --as cursor-1 --cli claude-code
earmark join reviewer --as rev-1 --cli codex
earmark comment EV-1 "Offset pagination is simplest for the UI." --as lead
earmark comment EV-1 "Items can be inserted while paging; offsets would skip rows." --as rev-1
earmark comment EV-1 "Noted. I will compare both." --as cursor-1
earmark comments EV-1 --new --as cursor-1 > /dev/null    # cursor-1 has seen everything so far
earmark comment EV-1 "Benchmarks attached in the PR description; both are fast enough." --as rev-1
earmark comment EV-1 "@cursor-1 cursor or offset? One line with the reason, please." --as lead --kind question
unset EARMARK_AS
```

Note the id printed for the last comment (the question) as `Q`, and the id of the third
comment as `C3`.

## Prompt, phase 1

> You are `cursor-1` on this project's earmark board (already joined). Someone replied on
> EV-1. Catch up on what is new and answer the question addressed to you.

## Pass checklist, phase 1

Required:

- [ ] Fetched only the unseen comments: `earmark comments EV-1 --new --as cursor-1`, or
      `--since C3`, or `earmark inbox --as cursor-1`.
- [ ] Did not dump the whole thread: no `earmark comments EV-1` without `--new`, `--since` or
      `--last 2`, and no `earmark show EV-1 -c`.
- [ ] Posted one reply as `cursor-1` with `--reply-to Q` that picks cursor or offset with a
      reason.
- [ ] Did not run `earmark join` again.

Quality:

- [ ] The reply is one or two lines and references the insert-while-paging point.

## Prompt, phase 2 (same session)

> Show me only the comments on EV-1 after #C3 (use the real id).

## Pass checklist, phase 2

- [ ] Ran `earmark comments EV-1 --since C3` (stateless; `--new` would return nothing now).
- [ ] Reported exactly the comments after C3: the benchmark note, the question and its own
      reply.

## Checks

```bash
cd "$EVAL"
earmark comments EV-1 --last 2
earmark inbox --peek --as lead
```
