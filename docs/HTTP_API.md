# HTTP API

Served by `earmark ui` on `http://127.0.0.1:7477`. JSON in and out. Every response body is the
envelope from `contracts/envelope.schema.json`: `{contract_version, ok, data, error, meta}`.
The HTTP status follows `src/earmark/errors.py`: `invalid_request`, `identity_required` and
`not_joined` 400, `forbidden` 403, `not_found`, `no_board` and `empty` 404, `conflict` 409,
`busy` 503, `internal_error` 500. A known path with the wrong method answers 405 with an
`Allow` header; a JSON body over 1 MB answers 413 (an upload: over `max_attachment_mb`).

Requests act as the board owner. Every non-GET request with a body must send `X-Earmark: 1`
and `Content-Type: application/json` (an upload sends the file's type). The server rejects a `Host` other than
`127.0.0.1:<port>` or `localhost:<port>`, refuses writes whose `Origin` is another site, refuses any
`/api/*` request whose `Sec-Fetch-Site` is `cross-site` or `same-site` (other localhost ports
included) and an `/api/stream` request with a foreign `Origin`, and sends no CORS headers.

## Shapes

`IssueSummary` (lists):

```json
{"key": "GAME-42", "id": 42, "title": "Eye color widget resets on reload",
 "status": "in_progress", "priority": "high", "type": "bug",
 "topic": "ui-ux/character-menu/face-layer/eye-color", "assignee": "claude-ui",
 "reviewer": null, "reporter": "hector", "labels": ["regression"], "due": "2026-11-07",
 "parent": "GAME-30", "children": 2, "blocked": false, "stale": false,
 "lease_until": "2026-11-03T16:05:00Z", "rank": 3072.0, "version": 7,
 "comment_count": 12, "last_comment_id": 1199,
 "created_at": "2026-11-03T10:02:11Z", "updated_at": "2026-11-03T14:05:09Z"}
```

`Issue` (detail) = `IssueSummary` plus `body`, `started_at`, `review_requested_at`,
`resolved_at`, `children` as a list of `IssueSummary`, and
`links: {"blocks": [...keys], "blocked_by": [...], "duplicates": [...], "duplicated_by": [...], "relates": [...]}`.

`Comment`:

```json
{"id": 1190, "issue": "GAME-42", "author": "codex-qa", "kind": "note",
 "body": "Still resets when switching tabs. @claude-ui", "reply_to": 1187,
 "mentions": ["claude-ui"], "created_at": "2026-11-03T15:10:44Z", "edited_at": null}
```

`Topic` (tree node): `{"id", "path", "slug", "name", "icon", "description", "parent_id",
"position", "archived", "counts": {"open": 9, "total": 31, "by_status": {"todo": 4, ...}},
"children": [Topic...]}`.

`Agent`: `{"handle", "kind", "profile", "role", "cli", "online", "presence": "active|idle|offline",
"joined_at", "last_seen", "working": [IssueSummary], "reviewing": [IssueSummary],
"assigned": [IssueSummary]}`.

`Profile`: `{"slug", "name", "role", "icon", "prompt", "topics": ["ui-ux/character-menu"],
"version", "created_at", "updated_at", "agents": ["claude-ui"]}`.

`Event`: `{"id", "issue": "GAME-42"|null, "actor", "action", "old", "new", "at"}`.

`Attachment`: `{"id": 12, "issue": "GAME-42", "comment_id": 1187|null, "name": "eye-picker-bug.png",
"mime": "image/png", "kind": "image|audio|video|pdf|text|file", "size": 188416, "sha256",
"author", "created_at", "url": "/api/attachments/12/raw", "path": "<absolute path on disk>"}`.
`IssueSummary` adds `attachment_count`; `Issue` adds `attachments: [Attachment]` (all of them,
comment ones included); `Comment` adds `attachments: [Attachment]`.

## Routes

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/board` | | `{prefix, owner, settings: {require_review, lease_minutes, max_attachment_mb, max_review_rounds}, counts: {by_status, open, total}, enums: {statuses, priorities, types, comment_kinds, roles, link_kinds}, version}` |
| GET | `/api/topics` | `?all=1` includes archived | `[Topic]` roots with children |
| POST | `/api/topics` | `{path, name?, icon?, description?}` | `Topic` |
| PATCH | `/api/topics/{id}` | `{name?, icon?, description?, path?, position?, archived?}` | `Topic` |
| DELETE | `/api/topics/{id}` | `?archive=1` archives instead of removing | `{deleted: id}` or `Topic` |
| GET | `/api/issues` | query mirrors `earmark ls`: `topic` (repeatable), `exact`, `status`, `priority`, `type`, `assignee`, `reviewer`, `reporter`, `label` (repeatable), `q`, `due_before`, `overdue`, `updated_since`, `created_since`, `ready`, `blocked`, `stale`, `parent`, `sort`, `desc`, `limit`, `offset` | `{items: [IssueSummary], total, limit, offset}` |
| POST | `/api/issues` | `{title, body?, topic?, priority?, type?, status?, labels?, due?, parent?, assignee?}` | `Issue` |
| GET | `/api/issues/{key}` | | `Issue` |
| PATCH | `/api/issues/{key}` | `{version, title?, body?, topic?, priority?, type?, due?, labels?, parent?}` | `Issue`; 409 with the current `Issue` plus `current_version`, `events_since` and `holder` in `error.details` |
| POST | `/api/issues/{key}/status` | `{status, note?}` | `Issue` |
| POST | `/api/issues/{key}/assign` | `{assignee: handle\|null}` | `Issue` |
| POST | `/api/issues/{key}/rank` | `{before?: key, after?: key, top?: true, bottom?: true}` | `Issue` |
| POST | `/api/issues/{key}/links` | `{kind, target}` | `Issue` |
| DELETE | `/api/issues/{key}/links` | `{kind, target}` | `Issue` |
| DELETE | `/api/issues/{key}` | | `{deleted: key}` |
| GET | `/api/issues/{key}/comments` | `?since=ID&limit=N` | `{items: [Comment], last_id, has_more}` |
| POST | `/api/issues/{key}/comments` | `{body, kind?: note\|progress\|question, reply_to?}` (handoff, approval and changes come from the status actions) | `Comment` |
| PATCH | `/api/comments/{id}` | `{body}` | `Comment` |
| GET | `/api/issues/{key}/attachments` | | `[Attachment]` |
| POST | `/api/issues/{key}/attachments` | raw file bytes as the body; `?name=<file name>&comment=<id>`; `Content-Type` = the file's type; `X-Earmark: 1`; up to `max_attachment_mb` | `Attachment` |
| GET | `/api/attachments/{id}` | | `Attachment` |
| GET | `/api/attachments/{id}/raw` | `Range` supported (206) for audio/video seeking | the bytes: inline for images (not SVG), audio, video and plain text; everything else, PDF included (the sandbox policy blocks browser PDF viewers), as a download. Always `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox; default-src 'none'`, `ETag` = sha256, `Cache-Control: private, max-age=31536000, immutable` |
| DELETE | `/api/attachments/{id}` | | `{deleted: id}` |
| GET | `/api/issues/{key}/events` | | `[Event]` |
| GET | `/api/activity` | `?since=EVENT_ID&limit=N` | `{items: [Event], last_id}` newest first |
| GET | `/api/team` | | `{agents: [Agent]}` |
| GET | `/api/profiles` | | `[Profile]` |
| POST | `/api/profiles` | `{slug, name, role, icon?, prompt?, topics?}` | `Profile` |
| PATCH | `/api/profiles/{slug}` | `{version, name?, role?, icon?, prompt?, topics?}` | `Profile` |
| DELETE | `/api/profiles/{slug}` | | `{deleted: slug}` |
| GET | `/api/stream` | Server-Sent Events | a `retry:` line and one `change` on connect, then `event: change` / `data: {"event_id": N, "comment_id": M}` on every commit by anyone; `: ping` every 25 s; at most 32 streams |

Static files: `/` serves `ui/index.html`; `/assets/*` serves the rest of `src/earmark/ui/`.
The UI routes in the hash: `#/` list, `#/board`, `#/issue/GAME-42`, `#/team`, `#/topic/<path>`.
