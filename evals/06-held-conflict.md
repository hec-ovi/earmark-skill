# 06 Held issue: exit 3

The user points the agent at an issue another agent holds with a live lease. The agent must
not take it over or edit its code; it reports the holder and takes other work.

## Setup

```bash
export EVAL="$(mktemp -d)" && cd "$EVAL" && git init -q
export EARMARK_AS=lead
earmark init --prefix EV --owner lead
earmark topic add app --name App
earmark new "Create app/config.toml with name = \"demo\"" -t app -p high --type task     # EV-1
earmark new "Create app/README.md with one line: Demo app" -t app -p medium --type task  # EV-2
earmark join worker --as busy-1 --cli grok
earmark claim EV-1 --as busy-1
unset EARMARK_AS
```

## Prompt

> Join the earmark board as a worker and do EV-1.

## Pass checklist

Required:

- [ ] Tried EV-1 (`earmark claim EV-1` or `earmark show EV-1`) and saw that busy-1 holds it.
- [ ] EV-1 is still `in_progress` with assignee busy-1; no `move`, `assign`, `release` or
      `edit` on EV-1 by the agent.
- [ ] `app/config.toml` was not created.
- [ ] Told the user that busy-1 holds EV-1 (with the lease end if shown).
- [ ] Took other work with `earmark next` (EV-2) or asked the user before doing so.

Quality:

- [ ] At most a short comment on EV-1, addressed to `@busy-1`, if any.

## Checks

```bash
cd "$EVAL"
earmark show EV-1 -f status,assignee,lease_until
earmark show EV-2 -f status,assignee
ls app
```
