# 03 PM triage and assignment

The agent joins as pm, turns an untriaged backlog into ready, assigned work, cancels a
duplicate and deals with a stale claim, without taking implementation work itself.

## Setup

```bash
export EVAL="$(mktemp -d)" && cd "$EVAL" && git init -q
export EARMARK_AS=lead
earmark init --prefix EV --owner lead
earmark topic add ui --name UI
earmark topic add api --name API
earmark profile add ui-dev --role worker --name "UI developer" -t ui
earmark profile add api-dev --role worker --name "API developer" -t api
earmark join ui-dev --as ui-1 --cli claude-code
earmark join api-dev --as api-1 --cli codex
earmark new "Login button misaligned on mobile" --status backlog \
  -b "On screens under 400px the login button overflows the card."                      # EV-1
earmark new "Rate-limit POST /login" --status backlog \
  -b "Allow 5 attempts per minute per IP, then 429."                                     # EV-2
earmark new "Login button off-center on small screens" --status backlog \
  -b "Same as the mobile alignment report."                                              # EV-3
earmark new "Unknown routes return HTML instead of JSON 404" --status backlog \
  -b "GET /nope returns the HTML error page."                                            # EV-4
earmark new "Dark mode toggle in settings" -t ui --status backlog                     # EV-5
earmark new "Footer links point to the old domain" -t ui -p high --type bug           # EV-6
earmark config lease_minutes 1
earmark claim EV-6 --as ui-1
earmark config lease_minutes 120
sleep 65                                     # EV-6's claim is now stale
unset EARMARK_AS
```

## Prompt

> Join the earmark board as the pm and triage it: every backlog item should end up ready for
> the right agent.

## Pass checklist

Required:

- [ ] Joined with the `pm` profile and started from `earmark triage`.
- [ ] EV-1, EV-2, EV-4, EV-5 are `todo` with a topic: EV-1, EV-5 under `ui`; EV-2, EV-4 under
      `api`.
- [ ] Types set: EV-1 and EV-4 `bug`, EV-2 and EV-5 `feature`.
- [ ] EV-3 is linked `duplicates` EV-1 and is `canceled`.
- [ ] Assignees follow profile scope: `ui` issues to `ui-1`, `api` issues to `api-1`.
- [ ] EV-6 (stale): a comment addressed to `@ui-1`, and then reassigned or unassigned.
- [ ] The pm holds no `in_progress` issue and changed no files in the repo.

Quality:

- [ ] Priorities are deliberate (EV-2 at least `high`; EV-5 not above EV-1).
- [ ] Issues it edited have a goal or acceptance line in the description if they lacked one.
- [ ] Final message is about three lines: shaped, assigned, at risk.

## Checks

```bash
cd "$EVAL"
earmark ls -s all --fields key,status,priority,type,assignee,topic
earmark show EV-3 -f status,duplicates
earmark comments EV-6 --last 3
earmark triage
```
