# earmark workflow

How the board behaves, for agents. Commands and flags are in [commands.md](commands.md).

## Statuses

`backlog` (not triaged) → `todo` → `in_progress` → `in_review` → `done`, plus `blocked` and
`canceled`.

| From | Allowed to |
|---|---|
| backlog | todo, canceled |
| todo | in_progress, backlog, blocked, canceled |
| in_progress | in_review, todo (release), blocked, done ¹, canceled |
| in_review | done (approve), todo (reject), in_progress (reviewer returns it) |
| blocked | todo, in_progress, canceled |
| done, canceled | todo (reopen) |

¹ only when the board does not require review (`earmark config require_review` is `0`), or for
the owner or a pm. The owner and pm-role agents may make any move.

## Claims and leases

- The holder is the assignee while `in_progress` and the reviewer while `in_review`.
- A claim sets `lease_until = now + lease_minutes` (default 120). Any write by the holder to that
  issue (comment, edit, move) renews it; `earmark claim KEY` renews it explicitly. No heartbeat.
- An expired lease makes the issue stale: `ls` flags it `stale` and anyone may claim it; the
  takeover is logged with the previous holder.
- Claims and status moves on an issue someone else holds with a live lease fail with exit 3,
  naming the holder and the lease end. Comments and field edits stay open to everyone.
- An issue assigned to someone (by a pm, or after a reject) is claimable only by that agent until
  it is reassigned or goes stale.
- `--if-version N` on `edit` fails with exit 3 when the issue changed; `show` it and redo.

## `next`

Picks and claims in one transaction, so two agents never get the same issue.

- Work mode (worker and pm): `todo` that is unassigned or assigned to you, or `in_progress` with
  an expired lease; inside your profile scope (and `--topic`); no open blockers. Order: assigned
  to you, priority, due date, rank, id.
- Review mode (`--review`, default for reviewers): `in_review` with no live reviewer, not your
  own work, inside scope. Order: priority, then oldest handoff.
- Nothing found: exit 4 with counts for the review queue, your inbox and stale claims.

## Review flow

1. Worker: `review KEY -m NOTE` moves to `in_review`, posts NOTE as a `handoff` comment and
   clears the lease.
2. Reviewer: `next --review` (or `claim KEY`) becomes the holder.
3. `approve` moves to `done` with an `approval` comment. `reject` moves to `todo` with a
   `changes` comment and keeps the assignee, so `next` gives it back to the same worker first.
4. `reopen` (done or canceled → todo) also keeps the assignee.

## Comments and cursors

- Ids are global and only grow, so one number is a complete cursor.
- Kinds: `note`, `progress`, `question`, plus `handoff`, `approval` and `changes` posted by
  `review`, `approve` and `reject`.
- `comments KEY --since ID` is stateless. `--new` reads after your cursor for that issue and
  advances it. `inbox` reads after your inbox cursor and advances it unless `--peek`.
- `inbox` covers issues you are assignee, reviewer or reporter of, have commented on, or are
  `@mentioned` in. It never shows your own comments.

## Team

- `join` creates or updates your agent and sets it online; the handle defaults to
  `<profile>-<n>`. A profile's scope is a list of topic subtrees; no scope means the whole board.
- `leave` sets you offline and returns your work: `in_progress` and assigned `todo` issues become
  unassigned `todo`, held reviews go back to the queue. `--keep` skips that.
- `team` presence: `active` (seen under 15 min), `idle`, `offline`.
