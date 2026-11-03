# earmark command reference

Global flags, valid on every command: `--as HANDLE` (actor; or `EARMARK_AS`), `--db PATH`
(or `EARMARK_DB`), `--json` (envelope output). Keys accept `GAME-42`, `game-42` or `42`.
Text output escapes control characters in user text (`\x1b`, `\n` in titles); `--json` is raw.
Exit codes: 0 ok · 1 invalid · 2 not found · 3 conflict · 4 empty · 5 busy.

## Read, level by level

| Level | Command | Returns |
|---|---|---|
| map | `earmark topics [PATH] [--depth N]` | topic tree with open counts |
| L0 | `earmark ls [filters]` | one line per issue |
| L1 | `earmark show KEY [-f FIELDS]` | fields, description, comment count, last comment id |
| L2 | `earmark comments KEY [--since ID \| --new] [--last N]` | comments, oldest first |
| feed | `earmark inbox [--peek]` | new comments for you across issues |

### `ls` filters

| Flag | Meaning |
|---|---|
| `-t, --topic PATH` | topic and its subtree; repeatable; `--exact` for the node only |
| `-s, --status LIST` | statuses, or `open` (default: all but done/canceled), `active` (todo, in_progress, in_review), `closed`, `all` |
| `-p, --priority LIST` | e.g. `urgent,high`; `--min-priority high` means high or more urgent |
| `-a, --assignee H` | handle, `me` or `none`; `--reviewer H`, `--reporter H` likewise |
| `--type LIST` · `-l, --label L` | type filter · label (repeatable, all must match) |
| `--due-before DATE` · `--overdue` | due filters |
| `--updated-since WHEN` · `--created-since WHEN` | `30m`, `2h`, `3d`, `1w` or a date |
| `--ready` · `--blocked` · `--stale` | no open blockers · has open blockers · expired lease |
| `-q TEXT` | substring of title or description |
| `--parent KEY` | sub-issues of KEY |
| `--sort KEY` | `priority` (default: priority, rank), `rank`, `updated`, `created`, `due`, `key`; `--desc` |
| `--limit N --offset N` | default 50; footer gives the next offset |
| `--fields LIST` · `--count` | choose columns · print the count only |

Text output (header, aligned columns, `-` for empty, flags last):

```
KEY      STATUS       PRIO    TYPE  ASSIGNEE   TOPIC                                      TITLE
GAME-42  in_progress  high    bug   claude-ui  ui-ux/character-menu/face-layer/eye-color  Eye color widget resets on reload  blocks:1 c:3 due:11-07
GAME-51  todo         medium  task  -          ui-ux/video-calls                          Mute button state  blocked
2 of 2 open issues
```

Piped output prints every cell in full; only a terminal shortens long topics and titles with `…`.

Flags: `blocks:N` holds up N open issues (worked at their priority), `c:N` comments, `due:MM-DD` (`overdue` when past), `blocked`, `stale`, `sub:N` children, `a:N` attachments.
When more exist: `50 of 212 open issues · next: --offset 50`.

### `show`

```
GAME-42  Eye color widget resets on reload
status: in_progress   priority: high   type: bug   version: 7
topic: ui-ux/character-menu/face-layer/eye-color
assignee: claude-ui (lease until 2026-11-03T16:05:00Z)   reviewer: -   reporter: hector
labels: regression   due: 2026-11-07
parent: GAME-30   sub: GAME-43, GAME-44   blocks: GAME-51   blocked by: GAME-77 (done)
created: 2026-11-03T10:02:11Z   updated: 2026-11-03T14:05:09Z
comments: 12, last #1199, 3 new for you

<description>
```

`show KEY… [-f FIELDS] [--new | --since ID | --last N]` picks exactly the parts you need, in the
order you name them. Several keys in one call save tool calls.

| Ask for | Command | Prints |
|---|---|---|
| one field | `earmark show GAME-42 -f status` | `status: in_progress` |
| a few fields | `earmark show GAME-42 -f status,title,assignee` | `field: value` lines |
| fields + new comments | `earmark show GAME-42 -f status,title,comments --new` | the lines, then the unseen comments |
| only the files | `earmark show GAME-42 -f attachments` | the attachments table |
| many issues | `earmark show GAME-42 GAME-43 GAME-51 -f status,assignee` | one line per issue |

```
GAME-42  status: in_progress  assignee: claude-ui
GAME-43  status: todo  assignee: -
GAME-51  status: blocked  assignee: codex-api
```

Scalar fields: key, title, status, priority, effective_priority, type, topic, assignee, reviewer,
reporter, labels, due, parent, lease_until, stale, version, created_at, updated_at, started_at,
review_requested_at, resolved_at, comment_count, last_comment_id, new_comments, attachment_count.
Parts: `body` (alias `description`), `comments` (honours `--new`, `--since ID`, `--last N`;
default the last 5), `attachments`, `links` (blocks, blocked by, duplicates, relates), `children`.
`-c [N]` is short for adding `comments --last N` to whatever `-f` selects; `--new`, `--since` and
`--last` add the comments part on their own (`--new` with `--last` is refused). With several keys,
each issue starts with a key line carrying its scalar fields; parts follow as a block, and blocks
are separated by a blank line:

```
GAME-8  status: in_review  assignee: claude-ui
last 1 of 4 comments:
#73  claude-ui  note  11-03 12:16  reply-to:#72  files:@11
  It does, same anchor component. Added a spectator screenshot.

GAME-2  status: done  assignee: claude-ui
last 1 of 8 comments:
#40  codex-review  approval  10-30 08:42
  Verified both paths, including running the migration twice on the same save. Approved.
```

Comment parts are headed `N new comments:`, `N comments after #ID:` or `last N of M comments:`;
empty ones read `comments: nothing new after #N`, `comments: nothing after #N` or
`comments: none`; a cut part ends with `more: earmark comments KEY --new`. Unknown keys go to
stderr (`error: no issue GAME-99`, exit 2) while the others still print. `--json` returns the
same selection as objects (a list with several keys).

### `comments`

```
#1187  claude-ui  progress  11-03 14:05
  Moved the widget state into the store; reload test passes.
#1190  codex-qa  note  11-03 15:10  reply-to:#1187
  Still resets when switching tabs. @claude-ui
last: #1190 · 2 comments
```

`--since ID` everything after ID · `--new` after your cursor, then advances it ·
`--last N` the latest N · `--limit N` cap (default 100, footer says when more exist).
Text headers show `MM-DD HH:MM` in UTC; `--json` keeps full ISO timestamps.

### `inbox`

Same comment format with the issue key on each header (`GAME-42 #1190 codex-qa note …`).
Covers issues you are assignee, reviewer or reporter of, have commented on, or are
@mentioned in. Advances your inbox cursor unless `--peek`. Empty: `inbox: nothing new after #1190`.

## Work

| Command | Effect |
|---|---|
| `earmark next [--review] [--topic PATH] [--dry-run]` | pick and claim the best issue in your scope; exit 4 when none |
| `earmark claim KEY` | claim one issue (or renew your lease on it) |
| `earmark release KEY [-m NOTE] [--next]` | give it back: unassigned todo |
| `earmark need KEY TITLE -t PATH [--assign H] [-b BODY] [-p PRIO] [--type T] [-l LABEL]… [--attach FILE]…` | work only another specialist can do (an image, audio, copy): files it in PATH as a blocker of KEY and parks KEY as blocked, still yours; KEY returns to your `next` when it is done |
| `earmark review KEY -m NOTE [--next]` | hand off to review; NOTE posted as `handoff` (what changed, how to verify) |
| `earmark approve KEY [-m NOTE] [--next]` | reviewer: in_review → done, `approval` comment |
| `earmark reject KEY -m NOTE [--next]` | reviewer: in_review → todo for the same assignee, `changes` comment |
| `earmark done KEY [-m NOTE] [--next]` | close directly (owner, pm, or boards without required review) |
| `earmark move KEY STATUS [-m NOTE]` | any allowed transition (see [workflow.md](workflow.md)) |
| `earmark reopen KEY -m NOTE` · `earmark cancel KEY -m NOTE` | done/canceled → todo · → canceled |

`need` prints both sides:

```
GAME-77 created in artwork/icons · assigned to codex-art · blocks GAME-42
GAME-42 blocked, still yours · returns to your next when GAME-77 is done
```

`next` prints the claimed issue in `show` format, then `N new comments:` and the comments you
have not seen, so starting an issue is one call. `review`, `approve`, `reject`, `done` and
`release` take `--next`: the action's line, then `next`'s output, and the exit code is `next`'s
(4 when nothing is left, though the action itself succeeded). Write commands and `next` end with
`· N changes for you: earmark check` (`· 1 change for you` for one) when your check feed has
something;
`--json` puts the count in `meta.changes`. With nothing to claim (exit 4):
`next: nothing ready for you · reviews for you: 3 · changes for you: 2 · stale: 0`, and for
`next --review`
`next: nothing to review for you (your own handoffs excluded) · changes for you: 2`.
`reviews for you` is what `next --review` could claim, `changes for you` is what `check` would
list (the count of the hint line), `stale` the lapsed claims in your scope.

## Write

| Command | Notes |
|---|---|
| `earmark new TITLE [-b BODY \| --body-file F \| --body-file -] [-t PATH] [-p PRIO] [--type T] [-l LABEL]… [--due DATE] [--parent KEY] [--assign H] [--status backlog]` | prints the new key |
| `earmark edit KEY [--title T] [-b BODY \| --body-file F \| --append TEXT] [-t PATH] [-p PRIO] [--type T] [--due DATE \| --no-due] [-l +LABEL \| -l -LABEL]… [--parent KEY \| --no-parent] [--if-version N]` | only given fields change |
| `earmark comment KEY TEXT [--kind note\|progress\|question] [--reply-to ID] [--attach FILE]…` | `-` or `--body-file F` reads the body; prints `#<id>` and any `@<attachment id>` |
| `earmark comment --edit ID TEXT` | your own comments only |
| `earmark link KEY blocks\|duplicates\|relates KEY2` · `earmark unlink …` | dependencies |
| `earmark assign KEY HANDLE\|none` | owner or pm |
| `earmark rank KEY --before KEY2 \| --after KEY2 \| --top \| --bottom` | manual order |
| `earmark delete KEY --yes` | owner only; prefer `cancel` |

Dates: `2026-11-07`, `today`, `tomorrow`, `+3d`, `+2w`.

## Attachments

Files live beside the board in `.earmark/files/`; commands print absolute paths, so open
images and files with your own tools.

| Command | Notes |
|---|---|
| `earmark attach KEY FILE… [--comment ID]` | attach to the issue, or to one of its comments; prints `@<id>` per file |
| `earmark attachments KEY` | list: id, kind, size, name, author, comment, path |
| `earmark attachment ID [--out DEST]` | one attachment's fields and path · copy it to DEST |
| `earmark detach ID` | author, owner or pm |

```
ID   KIND   SIZE    NAME                AUTHOR     COMMENT  PATH
@12  image  184 KB  eye-picker-bug.png  claude-ui  #1187    /work/game/.earmark/files/3f/3fa1…c9.png
@13  audio  2.1 MB  ambience-v2.ogg     agy-audio  -        /work/game/.earmark/files/a0/a07e…41.ogg
```

`show` adds `attachments: 2 (1 image, 1 audio)`; comment headers add `files:@12,@13`;
`ls` flags add `a:N`. Limit per file: `earmark config max_attachment_mb` (default 50).

## Topics

| Command | Notes |
|---|---|
| `earmark topics [PATH] [--depth N] [--all]` | tree, open counts per node; `--all` includes archived |
| `earmark topic add PATH [--name N] [--icon I] [--description D]` | creates missing ancestors |
| `earmark topic edit PATH [--name N] [--icon I] [--description D]` | |
| `earmark topic mv PATH NEW_PATH` | moves the subtree |
| `earmark topic rm PATH [--archive]` | empty subtree only, or archive |

```
ui-ux                         UI & UX                   14 open
  character-menu              Character menu             9 open
    face-layer                Face customization layer   4 open
      eye-color               Eye color widget           2 open
  video-calls                 Video calls                5 open
```

## Team

| Command | Notes |
|---|---|
| `earmark join PROFILE [--as HANDLE] [--cli NAME]` | register and print your briefing; without `--as`, resumes your offline handle for the same profile and CLI (`(resumed)`) |
| `earmark leave [--keep]` | go offline and return your work |
| `earmark whoami` | handle, profile, role, scope, your issues |
| `earmark check [--peek]` | only what changed for you since your last check, by others: new assignments, changes to your issues, new comments and files on them, mentions (a pm: also every issue in its scope); `nothing new` otherwise. `--peek` keeps the cursor |
| `earmark team` | every agent: role, profile, cli, presence, current issues |
| `earmark profiles` · `earmark profile show SLUG` | list · full prompt and scope |
| `earmark profile add SLUG --role R --name N [--prompt T \| --prompt-file F] [-t PATH]…` | owner or pm |
| `earmark profile edit SLUG […]` · `earmark profile rm SLUG` | owner or pm |
| `earmark triage [--limit N]` | pm view: untriaged backlog, unassigned todo, stale claims, exhausted reviews (parked after `max_review_rounds` rejects), review queue age, blocked, overdue, idle agents; at most N rows per section (default 10), a cut section ends with `+4 more: earmark ls -s todo -a none` (the call that lists it all); columns that are the same on every row are left out |

```
check: nothing new since 14:05:09Z · holding GAME-1 (lease 1h40m) · ready for you: 9
```

```
check: since 14:05:09Z · holding GAME-1 (lease 1h40m) · ready for you: 10 · 1 waiting on blockers
GAME-77  new for you (assigned by grok-pm) · high · blocks:1
GAME-3   rejected (codex-review) · 2 new comments
GAME-1   2 new comments, question for you, mentions you · 1 new file
GAME-12  unblocked (GAME-31 done)
GAME-9   priority medium→high · title changed
```

The header counts what `next` could claim now (`ready for you`: todo assigned to you or
unassigned, and stale claims, in your scope, with no open blocker) and, when some of your
assigned issues wait on open blockers, `M waiting on blockers`; a reviewer's header counts what
`next --review` could claim instead (`reviews waiting: N`). `--json` carries all of them
(`ready_for_you`, `waiting_on_blockers`, `reviews_waiting`; `todo_for_you` is an alias of
`ready_for_you`).

One line per issue that changed, issues that need you first (new for you, a question for you,
rejected, unblocked), then by priority; at most 10 lines, then `+N more`. Status and priority
show `old→new`; links name each one, added or removed as seen from the issue (`+blocks GAME-5`,
`-relates GAME-2`, `+blocked by GAME-8`; two at most, then `…`); other fields only say
`<field> changed`; comments and files are counts. A
`question for you` is a `question` that @mentions you or, mentioning nobody, sits on an issue
you are assignee or reviewer of; other comments that @mention you print `mentions you`. Nothing
is printed in full: open what matters with `earmark show KEY -f <fields>,comments --new` or
`earmark attachments KEY`. Only changes made by others count, and on each issue only those
newer than your own last write there (a claim, comment, review, move or edit), so what `next`
or `next --review` printed when it claimed the issue is not reported again. The first check
after `join` covers changes since the join. A lease under 15 minutes prints `(lease 12m, comment to renew)`.

A pm watches the board: its check adds every issue in its profile's scope (the whole board when
unscoped), in the same line format and under the same 10-line cap: new issues, status moves,
assignments, handoffs, review outcomes and caps, takeovers, and claims whose lease ran out since
its last check. Of those, only an exhausted review counts as needing it; `wait --for changes`
(and `any`) wakes on them too.

```
check: since 14:05:09Z · ready for you: 2
GAME-21  rejected (codex-qa) · review rounds exhausted
GAME-30  new issue (codex-api) · high
GAME-5   stale claim (claude-ui)
GAME-8   approved (codex-qa)
GAME-12  handoff (claude-ui)
GAME-15  assigned to codex-art
```

```
HANDLE      ROLE      PROFILE   CLI          SEEN  STATE   WORKING
claude-ui   worker    ui-lead   claude-code  2m    active  GAME-42 in_progress
codex-qa    reviewer  reviewer  codex        14m   active  GAME-37 in_review
grok-pm     pm        pm        grok         1h    idle    -
```

## Board

| Command | Notes |
|---|---|
| `earmark init [--prefix GAME] [--owner HANDLE] [--no-review]` | creates `.earmark/earmark.db`, seeds profiles |
| `earmark info` | board path, prefix, counts by status, agents online, file sizes |
| `earmark wait [--for work\|review\|changes\|any] [--timeout 9m]` | blocks read-only until work, a review or a change for you (what `check` reports; a pm's covers its scope, lapsing leases included) arrives; exit 4 on timeout. With no open issue left on the board (at start or after a change), every mode returns `wait: board · no open issues` (exit 0): time to stop |
| `earmark ui [--port 7477] [--open]` | web UI on 127.0.0.1 |
| `earmark export` · `earmark backup DIR` | JSON dump to stdout · consistent copy: `DIR/earmark.db` + `DIR/files/` |
| `earmark doctor [--fix]` | Python, SQLite, WAL, schema, board location, attachment files; `--fix` removes unreferenced files |
| `earmark config [KEY [VALUE]]` | `require_review`, `lease_minutes`, `max_attachment_mb`, `prefix` |
