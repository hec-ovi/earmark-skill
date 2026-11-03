-- earmark board schema, version 1.
-- Applied once by `earmark init`; later versions are additive migrations in db.py.
-- Timestamps are UTC ISO-8601 text 'YYYY-MM-DDTHH:MM:SSZ' (sortable as text).
-- Due dates are 'YYYY-MM-DD'. Booleans are 0/1 integers.

CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- keys: schema_version, prefix, owner, created_at, require_review ('1'|'0'), lease_minutes ('120'),
--       max_attachment_mb ('50'), max_review_rounds ('3')

CREATE TABLE topics (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_id   INTEGER REFERENCES topics(id) ON DELETE RESTRICT,
  slug        TEXT NOT NULL CHECK (slug GLOB '[a-z0-9]*' AND length(slug) BETWEEN 1 AND 48),
  path        TEXT NOT NULL UNIQUE,            -- 'ui-ux/character-menu/face-layer'
  name        TEXT NOT NULL,                   -- 'Face customization layer'
  icon        TEXT,                            -- Lucide icon name, optional
  description TEXT NOT NULL DEFAULT '',
  position    INTEGER NOT NULL DEFAULT 0,      -- order among siblings
  archived    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);
CREATE INDEX topics_parent ON topics(parent_id, position);

CREATE TABLE profiles (
  slug       TEXT PRIMARY KEY CHECK (slug GLOB '[a-z0-9]*' AND length(slug) BETWEEN 1 AND 32),
  name       TEXT NOT NULL,                    -- 'UI lead'
  role       TEXT NOT NULL CHECK (role IN ('worker','reviewer','pm')),
  icon       TEXT,
  prompt     TEXT NOT NULL DEFAULT '',         -- briefing printed by `earmark join`
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version    INTEGER NOT NULL DEFAULT 1
);

-- Topic scope of a profile; no rows = whole board. A scope row covers the topic's subtree.
CREATE TABLE profile_topics (
  profile  TEXT NOT NULL REFERENCES profiles(slug) ON DELETE CASCADE ON UPDATE CASCADE,
  topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  PRIMARY KEY (profile, topic_id)
);

CREATE TABLE agents (
  handle     TEXT PRIMARY KEY CHECK (handle GLOB '[a-z0-9]*' AND length(handle) BETWEEN 1 AND 32),
  kind       TEXT NOT NULL DEFAULT 'agent' CHECK (kind IN ('agent','human')),
  profile    TEXT REFERENCES profiles(slug) ON DELETE SET NULL ON UPDATE CASCADE,
  cli        TEXT,                             -- 'claude-code','codex','grok','agy','opencode','human', free text
  online     INTEGER NOT NULL DEFAULT 1,       -- 1 after join, 0 after leave
  joined_at  TEXT NOT NULL,
  last_seen  TEXT NOT NULL                     -- refreshed at most once per 60 s per agent
);

CREATE TABLE issues (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,   -- key is '<prefix>-<id>', never reused
  title               TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  body                TEXT NOT NULL DEFAULT '',
  type                TEXT NOT NULL DEFAULT 'task'
                        CHECK (type IN ('bug','feature','task','chore')),
  status              TEXT NOT NULL DEFAULT 'todo'
                        CHECK (status IN ('backlog','todo','in_progress','in_review','blocked','done','canceled')),
  priority            INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 0 AND 4),
                        -- 0 urgent, 1 high, 2 medium, 3 low, 4 none
  topic_id            INTEGER REFERENCES topics(id) ON DELETE RESTRICT,
  parent_id           INTEGER REFERENCES issues(id) ON DELETE SET NULL,
  reporter            TEXT NOT NULL,
  assignee            TEXT,                    -- owner of the work; kept through review and reopen
  reviewer            TEXT,                    -- holder of the review while in_review
  lease_until         TEXT,                    -- claim expiry of the current holder (assignee when
                                               -- in_progress, reviewer when in_review)
  rank                REAL NOT NULL,           -- manual order, ascending
  due_at              TEXT,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL,           -- any change, comments included
  started_at          TEXT,                    -- first move to in_progress
  review_requested_at TEXT,                    -- last move to in_review
  resolved_at         TEXT,                    -- last move to done/canceled
  version             INTEGER NOT NULL DEFAULT 1  -- +1 on every field or status change (not comments)
);
CREATE INDEX issues_status   ON issues(status, priority, rank);
CREATE INDEX issues_topic    ON issues(topic_id, status);
CREATE INDEX issues_assignee ON issues(assignee, status);
CREATE INDEX issues_reviewer ON issues(reviewer, status);
CREATE INDEX issues_parent   ON issues(parent_id);
CREATE INDEX issues_updated  ON issues(updated_at);

CREATE TABLE labels (
  issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
  label    TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 32),
  PRIMARY KEY (issue_id, label)
);
CREATE INDEX labels_label ON labels(label);

-- src blocks dst | src duplicates dst | src relates to dst (relates is stored once, read both ways)
CREATE TABLE links (
  src        INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
  dst        INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL CHECK (kind IN ('blocks','duplicates','relates')),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (src, dst, kind),
  CHECK (src <> dst)
);
CREATE INDEX links_dst ON links(dst, kind);

CREATE TABLE comments (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,   -- global, monotonic, never reused: the cursor
  issue_id   INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
  author     TEXT NOT NULL,
  kind       TEXT NOT NULL DEFAULT 'note'
               CHECK (kind IN ('note','progress','question','handoff','approval','changes')),
  body       TEXT NOT NULL CHECK (length(body) >= 1),
  reply_to   INTEGER REFERENCES comments(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  edited_at  TEXT
);
CREATE INDEX comments_issue  ON comments(issue_id, id);
CREATE INDEX comments_author ON comments(author, id);

CREATE TABLE mentions (
  comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
  handle     TEXT NOT NULL,
  PRIMARY KEY (comment_id, handle)
);
CREATE INDEX mentions_handle ON mentions(handle, comment_id);

-- Files attached to an issue or one of its comments. Bytes live beside the database in
-- .earmark/files/<sha[0:2]>/<sha256><.ext>, written once and shared by identical uploads.
CREATE TABLE attachments (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  issue_id   INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
  comment_id INTEGER REFERENCES comments(id) ON DELETE SET NULL,
  name       TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 255),   -- original file name
  mime       TEXT NOT NULL,
  size       INTEGER NOT NULL CHECK (size >= 0),
  sha256     TEXT NOT NULL CHECK (length(sha256) = 64),
  ext        TEXT NOT NULL DEFAULT '',                              -- '.png', '' when unknown
  author     TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX attachments_issue   ON attachments(issue_id, id);
CREATE INDEX attachments_comment ON attachments(comment_id);
CREATE INDEX attachments_sha     ON attachments(sha256);

-- Read cursors: scope 'inbox', 'check' or 'issue:<id>'. Written only by commands that advance them.
CREATE TABLE cursors (
  agent           TEXT NOT NULL,
  scope           TEXT NOT NULL,
  last_comment_id INTEGER NOT NULL,
  last_event_id   INTEGER NOT NULL DEFAULT 0,  -- used by the 'check' scope
  updated_at      TEXT NOT NULL,
  PRIMARY KEY (agent, scope)
);

-- Activity log. issue_id NULL = board-level event (topics, profiles, join/leave).
CREATE TABLE events (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  issue_id INTEGER REFERENCES issues(id) ON DELETE CASCADE,
  actor    TEXT NOT NULL,
  action   TEXT NOT NULL,
  old      TEXT,
  new      TEXT,
  at       TEXT NOT NULL
);
CREATE INDEX events_issue ON events(issue_id, id);
