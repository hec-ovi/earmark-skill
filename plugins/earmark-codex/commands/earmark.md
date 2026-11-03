---
description: Work the project's earmark issue board as a worker, reviewer or pm
argument-hint: "[join <profile> | next | review]"
---

Activate the `earmark` skill and follow `skills/earmark/SKILL.md`.

1. Resolve the CLI: `earmark` on PATH. If it is missing, ask the user to run
   `uv tool install git+https://github.com/hec-ovi/earmark-skill`; do not install it yourself.
2. Map the argument:
   - `join <profile>`: `earmark join <profile> --cli codex`, then run that role's loop.
   - `next` or empty: join as `worker` if you have no handle yet, then run the worker loop.
   - `review`: join as `reviewer` if you have no handle yet, then run the reviewer loop.
3. Pass `--as <handle>` from the join briefing on every later command.
4. Stop with a three-line summary when the loop finds nothing left; `earmark leave` at the end.

User argument: $ARGUMENTS
