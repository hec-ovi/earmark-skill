# Architecture

earmark is one stdlib-only Python package over one SQLite file per project. Agents call the
`earmark` CLI, the browser calls a localhost HTTP server, and both go through the same board
service, so every rule below lives in exactly one place.

```
 agents (Claude Code, Codex, Grok, agy, OpenCode)     you (browser)
        │  earmark <command>                                 │  http://127.0.0.1:7477
        ▼                                                    ▼
   cli/  (argparse, text + JSON rendering)           server/  (http.server, SSE)
        └──────────────────────┬─────────────────────────────┘
                               ▼
              board service  (board.py and its modules)   ← the only code that runs SQL
                               ▼
              db.py  (discovery, connection, pragmas, migrations, write transactions)
                               ▼
              <project>/.earmark/earmark.db  (SQLite, WAL)
```

## Package layout

| Path | Owns |
|---|---|
| `src/earmark/db.py`, `schema.sql` | board discovery, connections, pragmas, migrations, `write()` transactions |
| `src/earmark/board.py` (+ modules it imports) | every read and write rule in this document |
| `src/earmark/model.py`, `errors.py`, `timeutil.py` | enums, parsing and validation, error codes, time and duration parsing |
| `src/earmark/cli/` | command grammar and output rendering; no SQL |
| `src/earmark/server/` | HTTP routes and live updates; no SQL |
| `src/earmark/ui/` | static web UI: HTML, CSS, JS modules, icon sprite; no build step |
| `src/earmark/profiles/` | default profile prompts and the per-role loop text printed by `join` |
| `<project>/.earmark/files/` | attachment bytes, content-addressed, beside `earmark.db` |
| `skills/earmark/` | canonical skill (SKILL.md + references/), mirrored by `scripts/sync-skill-copies.py` |
| `contracts/` | JSON Schemas for every `--json` payload and HTTP response |

## Storage and the disk

- **Location.** First hit wins: `--db PATH`, `EARMARK_DB` (file or directory), the nearest
  `.earmark/earmark.db` walking up from the working directory, then the main checkout of the
  current git repository (`git rev-parse --git-common-dir`), so agents in git worktrees share
  one board. `earmark init` creates `.earmark/` with a `.gitignore` of `*`.
- **Local disk only.** WAL needs shared memory; the board must not sit on a network share.
- **Connection pragmas.** `journal_mode=WAL` (set at init, persistent), `synchronous=NORMAL`,
  `foreign_keys=ON`, `busy_timeout=10000`, `journal_size_limit=8388608`.
  `sqlite3.connect(..., isolation_level=None)`: autocommit, explicit transactions only.
- **Writes.** Every write is one `BEGIN IMMEDIATE … COMMIT` holding the minimum work. If the
  lock is still held after `busy_timeout`, `write()` retries 3 times with jitter, then raises
  `busy` (retriable). A write never spans user think time.
- **No write without a change.** Reads never write. Exceptions, each bounded: advancing a read
  cursor when the caller asks (`--new`, `inbox`), and `agents.last_seen`, refreshed at most
  once per 60 s per agent. There are no heartbeats, no timers that write, no polling writes.
- **Change detection is read-only.** The UI server and `earmark wait` keep one connection and
  check `PRAGMA data_version` (memory, no I/O on the main file); only a change triggers a query.
- **Migrations.** `meta.schema_version`; `db.py` applies numbered migrations in one write
  transaction on open when the file is older. Version 1 is `schema.sql`.
- **Startup.** Lazy imports: `earmark ls` loads only cli, board, db. Target under 50 ms.

## Identity

- Every write has an actor. Resolution: `--as HANDLE`, then `EARMARK_AS`, then the board
  owner when stdin and stdout are both a terminal (a person typing). Otherwise a write fails
  with `identity_required`. Reads work without identity.
- An actor must exist in `agents` (`join` creates it; `init` creates the owner as `kind=human`).
  Unknown handles fail with `not_joined` and a hint to run `earmark join`.
- Handles and slugs: `[a-z0-9][a-z0-9-]*`, at most 32 chars (topics 48).
- The owner and actors whose profile role is `pm` may make any status move; everyone else
  follows the transition table.

## Issues

- **Key** `<PREFIX>-<id>`, prefix set at init (`[A-Z][A-Z0-9]{0,9}`). Input accepts `GAME-42`,
  `game-42` and `42`. Ids come from AUTOINCREMENT and are never reused.
- **Priority** `urgent|high|medium|low|none` ↔ 0..4 (aliases `p0..p4`); lower sorts first.
- **Types** `bug|feature|task|chore`. **Labels** free tags, `[a-z0-9][a-z0-9._:-]*`, ≤ 32 chars.
- **Topic** must exist; an unknown path fails with `not_found` and up to 3 close matches.
  Topics are never created implicitly by issue writes.
- **Rank** REAL, ascending. New issues go to the bottom (`max + 1024`). Moving between two
  issues takes the midpoint; when a gap falls below 1e-6 all ranks are renumbered in steps of
  1024 inside the same transaction.
- **Version** +1 on every field or status change. Writes that pass `if_version` fail with
  `conflict` (current version and the events since it in the error) when it does not match.
  Field edits update only the fields given, so concurrent edits to different fields both land.
- **updated_at** moves on every change including comments; `version` ignores comments.

### Status transitions

| From | Allowed to |
|---|---|
| backlog | todo, canceled |
| todo | in_progress, backlog, blocked, canceled |
| in_progress | in_review, todo (release), blocked, done ¹, canceled |
| in_review | done (approve), todo (reject), in_progress (reviewer returns it) |
| blocked | todo, in_progress, canceled |
| done, canceled | todo (reopen) |

¹ only when `meta.require_review = '0'`, or the actor is the owner or a `pm`.

Side effects: → `in_progress` sets assignee to the actor when empty, sets the lease and
`started_at` (first time). → `in_review` clears reviewer and lease, sets `review_requested_at`.
→ `done|canceled` sets `resolved_at`, clears the lease. Reopen clears `resolved_at` and keeps
the assignee, so the original worker sees it first. Release (`→ todo` by the holder) clears
assignee and lease. Reject keeps the assignee and clears the reviewer.

### Claims and leases

- The **holder** is the assignee while `in_progress` and the reviewer while `in_review`.
  `lease_until = now + meta.lease_minutes` (default 120) on claim.
- Any write by the holder to that issue (comment, edit, move) renews the lease. `claim KEY`
  on an issue you hold renews it explicitly. There is no heartbeat.
- A lease in the past makes the issue **stale**: listed with `stale`, claimable by others.
  Taking over logs a `takeover` event with the previous holder.
- Status moves and claims on an issue someone else holds with a live lease fail with
  `conflict` naming the holder and the lease end. Comments and field edits stay open to all.
- An issue assigned to someone (by a pm or after a reject) is claimable only by that assignee
  until reassigned, unless it is stale.

### `next`: pick and claim in one transaction

Inside one `BEGIN IMMEDIATE`, select the first candidate and claim it.

- **Work mode** (default for worker and pm roles): status `todo` and (unassigned, or assigned
  to the actor), or status `in_progress` with an expired lease; inside the actor's profile
  scope (subtrees; no scope = whole board) and `--topic` when given; not blocked by any
  issue whose status is not `done|canceled`.
- **Review mode** (`--review`, default for the reviewer role): status `in_review`, reviewer
  empty or stale, assignee is not the actor, inside scope. Claim sets reviewer and lease.
- **Effective priority.** An issue that blocks open issues takes the most urgent priority among
  the issues it blocks (one level), so a low-priority asset that holds up high-priority work is
  worked as high. `show` prints both (`priority: low (effective high, blocks GAME-42)`).
- **Order**: assigned to the actor first, then effective priority, then issues that block
  others before those that do not, then due date (none last), then rank, then id. Review mode
  orders by effective priority, then `review_requested_at`.
- Nothing found: exit code 4 (`empty`) with counts that tell the agent where to look next
  (review queue size, inbox count, stale claims).

### Needs: work only another agent can do

Agents never message each other to get work done. A dependency is an issue.

- `need KEY TITLE -t TOPIC [--assign HANDLE] …` runs in one write transaction: it creates the
  new issue in TOPIC (reporter = actor, priority defaults to KEY's, assigned to HANDLE when
  given), links `NEW blocks KEY`, and when the
  actor holds KEY moves KEY to `blocked`, keeping the assignee and clearing the lease. It posts
  a `note` on KEY naming NEW. Without `--assign`, `next` hands NEW to an agent whose scope covers TOPIC.
- **Auto-unblock.** When an issue moves to `done` or `canceled`, every issue it blocks that is
  `blocked` and has no other open blocker moves to `todo` with its assignee kept (event
  `unblocked`, actor = whoever resolved the blocker). The assignee's `next` returns it first.
- `show` prints blockers with their status (`blocked by: GAME-77 (done)`), so the resumed
  agent reads the result where it was delivered: the blocker's comments and attachments.
- A worker without a blocker issue (waiting on a person, an outside service) still uses
  `move KEY blocked -m …`; that one is released by hand.

## Comments and cursors

- Comment ids are global and monotonic, so one number is a complete cursor.
- **Kinds**: `note`, `progress`, `question`, `handoff` (posted by `review`), `approval`
  (posted by `approve`), `changes` (posted by `reject`). `reply_to` references another comment.
- `@handle` in a body is recorded in `mentions` when the comment is written.
- `comments KEY --since ID`: stateless, everything after ID. `--new`: after the actor's
  cursor `issue:<id>`, then advances it to the last id returned.
- `inbox`: comments after the actor's `inbox` cursor, not written by the actor, on issues the
  actor is assignee, reviewer or reporter of, has commented on, or that mention the actor.
  Advances the cursor to the last id returned unless `--peek`. `has_more` when the limit cut it.
- Comment authors may edit their own comment (`edited_at`); ids never change.

## Topics

- Materialized paths: `ui-ux/character-menu/face-layer/eye-color`, depth ≤ 16.
- A topic filter matches the node and its whole subtree (`path = X OR path LIKE 'X/%'`);
  `--exact` matches the node only.
- `topic add` creates missing ancestors (name defaults to the slug, editable later).
- Moving or renaming rewrites the paths of the node and its descendants in one transaction.
  Issues and profile scopes point at topic ids, so they follow automatically.
- Removing needs an empty subtree (no issues, no children) unless `--archive`.
- Counts per node roll up the subtree: open (not done/canceled) and per status.

## Attachments

- Files belong to an issue, optionally to one of its comments (images, audio, video, PDFs,
  logs, anything). Bytes live beside the database in `.earmark/files/<sha[0:2]>/<sha256><ext>`;
  SQLite stores only the `attachments` row, so the database stays small and fast.
- **Content-addressed, written once.** The writer streams the input to a temp file in
  `.earmark/files/` while hashing, then `os.replace`s it onto its final name (or drops it when
  that name already exists). Identical uploads share one file. Files are never modified.
- **Order.** Bytes first, then the row in one write transaction. A crash in between leaves at
  most an unreferenced file, which `doctor` reports and `doctor --fix` removes.
- **Removal.** `detach` (the author, the owner or a pm) deletes the row and, after commit, the
  file when no other row references its sha256. Deleting an issue does the same for its files.
- **Limits.** `meta.max_attachment_mb` (default 50) per file; names keep their base name only
  (no directories), at most 255 chars; `ext` is the lowercased original extension.
- **Kind** is derived from the MIME type (by extension, `mimetypes`): `image`, `audio`,
  `video`, `pdf`, `text` or `file`.
- **Agents read by path.** `earmark attachments KEY` prints absolute paths so agents open
  images and files with their own tools; nothing is base64'd into the terminal.
- An attach by the holder renews the lease like any holder write; events `attach`, `detach`.
- `backup DIR` writes `DIR/earmark.db` and copies `files/`; `export` lists attachment rows.

## Team

- **Profiles** carry a role (`worker|reviewer|pm`), a prompt and a topic scope. `init` seeds
  `worker`, `reviewer` and `pm` from `src/earmark/profiles/`.
- `join PROFILE [--as HANDLE]` creates or updates the agent (handle defaults to
  `<profile>-<n>`, smallest free n), sets it online and prints the briefing: identity line,
  profile prompt, scope, issues assigned, inbox count and the role's loop.
- `leave` sets the agent offline and returns its work: `in_progress` and assigned `todo`
  issues become unassigned `todo`, held reviews return to the review queue. `--keep` skips it.
- `check` is the cheap pulse an agent runs at milestones: one read-only line with its held
  issues and lease left, todo assigned to it (most urgent first, blockers flagged), its issues in
  review or blocked, and the inbox count with questions addressed to it.
- **Presence** shown in `team`: `active` (online, seen < 15 min), `idle` (online, older),
  `offline`.

## Events

One row per change: `created`, `status`, `title`, `body`, `priority`, `type`, `topic`,
`assignee`, `reviewer`, `due`, `labels`, `parent`, `link`, `unlink`, `rank`, `claim`,
`release`, `takeover`, `review`, `approve`, `reject`, `reopen`; board level: `topic_add`,
`topic_edit`, `topic_move`, `topic_remove`, `profile_add`, `profile_edit`, `profile_remove`,
`join`, `leave`. The UI timeline and activity feed read this table.

## Output contract

- Text output is plain, aligned and stable, written for agents: no color or glyphs unless
  stdout is a terminal. `--json` prints the envelope in `contracts/envelope.schema.json`.
- Exit codes: 0 ok · 1 invalid request or internal error · 2 not found · 3 conflict ·
  4 empty (`next` found nothing, `wait` timed out) · 5 busy.
- Errors go to stderr as `error: <message>` and, when useful, `hint: <next command>`.

## HTTP server

- `earmark ui` binds 127.0.0.1 (default port 7477) and serves `src/earmark/ui/` plus the API in
  `docs/HTTP_API.md`. It runs only while open; closing it leaves nothing running.
- Requests act as the board owner.
- Local-only hardening: reject requests whose `Host` is not `127.0.0.1|localhost:<port>`
  (DNS rebinding), require `X-Earmark: 1` on every non-GET request (blocks cross-site form
  posts), send no CORS headers, and set a strict Content-Security-Policy on HTML.
