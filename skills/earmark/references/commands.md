# earmark command reference

Global flags, valid on every command: `--as HANDLE` (actor; or `EARMARK_AS`), `--db PATH`
(or `EARMARK_DB`), `--json` (envelope output). Keys accept `GAME-42`, `game-42` or `42`.
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
KEY      STATUS       PRIO    TYPE  ASSIGNEE   TOPIC                          TITLE
GAME-42  in_progress  high    bug   claude-ui  ui-ux/character-menu/face-lay  Eye color widget resets on reload  c:3 due:11-07
GAME-51  todo         medium  task  -          ui-ux/video-calls              Mute button state  blocked
2 of 2 open issues
```

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

`-f status,assignee,version` prints only `field: value` lines. Fields: key, title, body,
status, priority, type, topic, assignee, reviewer, reporter, labels, due, parent, children,
blocks, blocked_by, duplicates, relates, lease_until, stale, version, created_at, updated_at,
started_at, review_requested_at, resolved_at, comment_count, last_comment_id, new_comments.
`-c [N]` appends the last N comments (default 5).

### `comments`

```
#1187  claude-ui  progress  2026-11-03T14:05:09Z
  Moved the widget state into the store; reload test passes.
#1190  codex-qa  note  2026-11-03T15:10:44Z  reply-to:#1187
  Still resets when switching tabs. @claude-ui
last: #1190 · 2 comments
```

`--since ID` everything after ID · `--new` after your cursor, then advances it ·
`--last N` the latest N · `--limit N` cap (default 100, footer says when more exist).

### `inbox`

Same comment format with the issue key on each header (`GAME-42 #1190 codex-qa note …`).
Covers issues you are assignee, reviewer or reporter of, have commented on, or are
@mentioned in. Advances your inbox cursor unless `--peek`. Empty: `inbox: nothing new after #1190`.

## Work

| Command | Effect |
|---|---|
| `earmark next [--review] [--topic PATH] [--dry-run]` | pick and claim the best issue in your scope; exit 4 when none |
| `earmark claim KEY` | claim one issue (or renew your lease on it) |
| `earmark release KEY [-m NOTE]` | give it back: unassigned todo |
| `earmark need KEY TITLE -t PATH [--assign H] [-b BODY] [-p PRIO] [--type T] [-l LABEL]… [--attach FILE]…` | work only another specialist can do (an image, audio, copy): files it in PATH as a blocker of KEY and parks KEY as blocked, still yours; KEY returns to your `next` when it is done |
| `earmark review KEY -m NOTE` | hand off to review; NOTE posted as `handoff` (what changed, how to verify) |
| `earmark approve KEY [-m NOTE]` | reviewer: in_review → done, `approval` comment |
| `earmark reject KEY -m NOTE` | reviewer: in_review → todo for the same assignee, `changes` comment |
| `earmark done KEY [-m NOTE]` | close directly (owner, pm, or boards without required review) |
| `earmark move KEY STATUS [-m NOTE]` | any allowed transition (see [workflow.md](workflow.md)) |
| `earmark reopen KEY -m NOTE` · `earmark cancel KEY -m NOTE` | done/canceled → todo · → canceled |

`need` prints both sides:

```
GAME-77 created in artwork/icons · assigned to codex-art · blocks GAME-42
GAME-42 blocked, still yours · returns to your next when GAME-77 is done
```

`next` prints the claimed issue in `show` format. With nothing to claim (exit 4):
`next: nothing ready in your scope · review queue: 3 · inbox: 2 new · stale: 0`.

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
| `earmark join PROFILE [--as HANDLE] [--cli NAME]` | register and print your briefing |
| `earmark leave [--keep]` | go offline and return your work |
| `earmark whoami` | handle, profile, role, scope, your issues |
| `earmark check [--peek]` | only what changed for you since your last check, by others: new assignments, changes to your issues, new comments and files on them, mentions; `nothing new` otherwise. `--peek` keeps the cursor |
| `earmark team` | every agent: role, profile, cli, presence, current issues |
| `earmark profiles` · `earmark profile show SLUG` | list · full prompt and scope |
| `earmark profile add SLUG --role R --name N [--prompt T \| --prompt-file F] [-t PATH]…` | owner or pm |
| `earmark profile edit SLUG […]` · `earmark profile rm SLUG` | owner or pm |
| `earmark triage` | pm view: untriaged backlog, unassigned todo, stale claims, review queue age, blocked, overdue, idle agents |

```
check: nothing new since 14:05:09Z · holding GAME-1 (lease 1h40m) · todo for you: 9
```

```
check: since 14:05:09Z · holding GAME-1 (lease 1h40m) · todo for you: 10
new: GAME-77 high blocks:1 (assigned by grok-pm)
changed: GAME-9 priority medium→high (grok-pm) · GAME-12 unblocked (GAME-31 done) · GAME-3 rejected (codex-review)
comments: GAME-3 #1201 codex-review changes "Thumbnails still load one by one on…" · GAME-1 #1203 agy-art question @you "Which eye shapes…"
files: GAME-1 @14 eye-icons.png image (agy-art)
```

Each line lists at most 5 items, most urgent first, then `+N more`. Bodies are never printed in
full: read them with `earmark comments KEY --new` and `earmark attachments KEY`. The first check
after `join` covers changes since the join. A lease under 15 minutes prints
`(lease 12m, comment to renew)`.

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
| `earmark wait [--for work\|review\|inbox\|any] [--timeout 9m]` | blocks read-only until something arrives; exit 4 on timeout |
| `earmark ui [--port 7477] [--open]` | web UI on 127.0.0.1 |
| `earmark export` · `earmark backup DIR` | JSON dump to stdout · consistent copy: `DIR/earmark.db` + `DIR/files/` |
| `earmark doctor [--fix]` | Python, SQLite, WAL, schema, board location, attachment files; `--fix` removes unreferenced files |
| `earmark config [KEY [VALUE]]` | `require_review`, `lease_minutes`, `max_attachment_mb`, `prefix` |
