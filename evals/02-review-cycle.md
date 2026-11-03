# 02 Review cycle: reject, rework, approve

A scripted worker hands off a fix that does not work. The agent, as reviewer, must verify the
claim, reject with concrete defects and leave the code alone; after the rework it approves.

## Setup

```bash
export EVAL="$(mktemp -d)" && cd "$EVAL" && git init -q
mkdir calc
printf 'def add(a, b):\n    return a - b\n' > calc/add.py
cat > calc/test_add.py <<'EOF'
import unittest
from add import add


class AddTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
EOF
git add calc                                 # staged baseline: git diff shows later edits
export EARMARK_AS=lead
earmark init --prefix EV --owner lead
earmark topic add math --name Math
earmark new "add() must return the sum" -t math -p high --type bug \
  -b "Acceptance: cd calc && python3 test_add.py passes."                               # EV-1
earmark join worker --as w-1 --cli codex
earmark next --as w-1
earmark review EV-1 --as w-1 -m "Fixed add() · calc/add.py · cd calc && python3 test_add.py"
unset EARMARK_AS
```

## Prompt, phase 1

> Join the earmark board as a reviewer and work the review queue.

## Pass checklist, phase 1

Required:

- [ ] Joined with the `reviewer` profile; claimed EV-1 with `earmark next --review`.
- [ ] Ran the test named in the handoff (`python3 test_add.py` in the transcript).
- [ ] Rejected: EV-1 is `todo`, assignee `w-1`, and the last comment has kind `changes`.
- [ ] The `changes` comment names `calc/add.py`, the faulty line (`a - b`) and expected vs actual
      (5 vs -1).
- [ ] `git diff` is empty: the reviewer did not edit the code.
- [ ] Stopped after `next --review` returned exit 4 and `earmark check` had nothing; no `earmark wait`.

## Rework (harness plays w-1)

```bash
cd "$EVAL"
earmark next --as w-1                        # EV-1 comes back to w-1 first
sed -i.bak 's/a - b/a + b/' calc/add.py && rm calc/add.py.bak
earmark review EV-1 --as w-1 -m "add() returns a + b · calc/add.py:2 · cd calc && python3 test_add.py"
```

## Prompt, phase 2 (same session)

> The worker reworked EV-1. Check the review queue again.

## Pass checklist, phase 2

Required:

- [ ] Reused the same handle; no second `join`.
- [ ] Read only the new comments (`comments EV-1 --new`, or `--since` the last id it had seen).
- [ ] Ran the test again, then approved: EV-1 is `done` with an `approval` comment that says
      what was checked.

Quality:

- [ ] Reject and approve notes are a few lines each, no pasted test logs beyond the failing
      assertion.

## Checks

```bash
cd "$EVAL"
earmark show EV-1 -f status,assignee,reviewer
earmark comments EV-1
git diff --stat
```
