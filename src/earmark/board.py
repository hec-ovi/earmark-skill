"""Board service: the only layer that runs SQL. The CLI and the HTTP server call these methods.

Contract (frozen for v0.1; additive changes only):
- Every method returns JSON-ready dicts/lists whose shapes are the ones in docs/HTTP_API.md
  (IssueSummary, Issue, Comment, Topic, Agent, Profile, Event) and contracts/board.schema.json.
- Inputs arrive as raw strings or simple values from the CLI or query strings; the board parses
  and validates them (keys, priorities, dates, durations, 'me'/'none', status groups).
- Errors are raised as earmark.errors subclasses with a message and, when useful, a hint.
- Field edits: only keys present in **fields change; a value of None clears nullable fields
  (due, parent, topic).
- Writes require an actor (see ARCHITECTURE.md, Identity) and run in db.write().
"""

from __future__ import annotations

from typing import Any, Iterable


class Board:
    # ---- lifecycle ---------------------------------------------------------------------
    path: str
    prefix: str
    owner: str
    actor: str | None

    @classmethod
    def open(cls, db: str | None = None, actor: str | None = None, *, cwd: str | None = None) -> "Board":
        """Discover and open a board (see ARCHITECTURE.md, Storage). Raises NoBoard."""
        raise NotImplementedError

    @classmethod
    def init(cls, directory: str, *, prefix: str | None = None, owner: str | None = None,
             require_review: bool = True) -> "Board":
        """Create <directory>/.earmark/earmark.db, seed profiles, create the owner agent."""
        raise NotImplementedError

    def close(self) -> None: ...

    def set_actor(self, handle: str | None) -> None:
        """Set the acting handle for subsequent calls (validated lazily on first write)."""
        raise NotImplementedError

    def touch(self) -> None:
        """Refresh agents.last_seen for the actor, at most once per 60 s. No-op without actor."""
        raise NotImplementedError

    # ---- board -------------------------------------------------------------------------
    def info(self) -> dict: raise NotImplementedError
    def config(self, key: str | None = None, value: str | None = None) -> dict: raise NotImplementedError
    def doctor(self) -> list[dict]: raise NotImplementedError          # [{name, ok, detail}]
    def export(self) -> dict: raise NotImplementedError
    def backup(self, dest: str) -> dict: raise NotImplementedError
    def data_version(self) -> int: raise NotImplementedError             # PRAGMA data_version
    def heads(self) -> dict: raise NotImplementedError                   # {event_id, comment_id}

    # ---- issues: read ------------------------------------------------------------------
    def list_issues(self, *, topic: Iterable[str] = (), exact: bool = False,
                    status: Iterable[str] | str | None = None, priority: Iterable[str] = (),
                    min_priority: str | None = None, assignee: str | None = None,
                    reviewer: str | None = None, reporter: str | None = None,
                    type: Iterable[str] = (), label: Iterable[str] = (), q: str | None = None,
                    due_before: str | None = None, overdue: bool = False,
                    updated_since: str | None = None, created_since: str | None = None,
                    ready: bool = False, blocked: bool = False, stale: bool = False,
                    parent: str | None = None, sort: str = "priority", desc: bool = False,
                    limit: int = 50, offset: int = 0) -> dict:
        """{items: [IssueSummary], total, limit, offset}. status None means 'open'."""
        raise NotImplementedError

    def get_issue(self, key: str, *, comments: int = 0) -> dict:
        """Issue; adds new_comments for the actor, and 'comments' (last N) when comments > 0."""
        raise NotImplementedError

    def issue_events(self, key: str) -> list[dict]: raise NotImplementedError
    def activity(self, *, since: int | None = None, limit: int = 50) -> dict: raise NotImplementedError

    # ---- issues: write -----------------------------------------------------------------
    def create_issue(self, title: str, *, body: str = "", topic: str | None = None,
                     priority: str = "medium", type: str = "task", status: str = "todo",
                     labels: Iterable[str] = (), due: str | None = None,
                     parent: str | None = None, assignee: str | None = None) -> dict:
        raise NotImplementedError

    def update_issue(self, key: str, *, if_version: int | None = None, append: str | None = None,
                     add_labels: Iterable[str] = (), remove_labels: Iterable[str] = (),
                     **fields: Any) -> dict:
        """fields ⊆ {title, body, topic, priority, type, due, labels, parent}."""
        raise NotImplementedError

    def move(self, key: str, status: str, *, note: str | None = None) -> dict: raise NotImplementedError
    def claim(self, key: str) -> dict: raise NotImplementedError
    def release(self, key: str, *, note: str | None = None) -> dict: raise NotImplementedError

    def next(self, *, review: bool | None = None, topics: Iterable[str] = (), dry_run: bool = False) -> dict:
        """Pick and claim in one transaction. review None = role default. Raises Empty with
        details {review_queue, inbox, stale, scope} when nothing is claimable."""
        raise NotImplementedError

    def submit_review(self, key: str, *, note: str) -> dict: raise NotImplementedError
    def approve(self, key: str, *, note: str | None = None) -> dict: raise NotImplementedError
    def reject(self, key: str, *, note: str) -> dict: raise NotImplementedError
    def done(self, key: str, *, note: str | None = None) -> dict: raise NotImplementedError
    def reopen(self, key: str, *, note: str) -> dict: raise NotImplementedError
    def cancel(self, key: str, *, note: str) -> dict: raise NotImplementedError
    def assign(self, key: str, handle: str | None) -> dict: raise NotImplementedError
    def rank(self, key: str, *, before: str | None = None, after: str | None = None,
             top: bool = False, bottom: bool = False) -> dict: raise NotImplementedError
    def link(self, key: str, kind: str, target: str) -> dict: raise NotImplementedError
    def unlink(self, key: str, kind: str, target: str) -> dict: raise NotImplementedError
    def delete_issue(self, key: str) -> dict: raise NotImplementedError

    # ---- comments ----------------------------------------------------------------------
    def add_comment(self, key: str, body: str, *, kind: str = "note", reply_to: int | None = None) -> dict:
        raise NotImplementedError

    def edit_comment(self, comment_id: int, body: str) -> dict: raise NotImplementedError

    def list_comments(self, key: str, *, since: int | None = None, new: bool = False,
                      last: int | None = None, limit: int = 100) -> dict:
        """{items: [Comment], last_id, has_more}. new=True reads after and advances the cursor."""
        raise NotImplementedError

    def inbox(self, *, peek: bool = False, limit: int = 50) -> dict:
        """{items: [Comment], last_id, has_more, cursor}. Advances unless peek."""
        raise NotImplementedError

    # ---- topics ------------------------------------------------------------------------
    def topics(self, *, root: str | None = None, depth: int | None = None,
               include_archived: bool = False) -> list[dict]: raise NotImplementedError
    def add_topic(self, path: str, *, name: str | None = None, icon: str | None = None,
                  description: str | None = None) -> dict: raise NotImplementedError
    def edit_topic(self, ref: str | int, *, name: str | None = None, icon: str | None = None,
                   description: str | None = None, position: int | None = None,
                   archived: bool | None = None) -> dict: raise NotImplementedError
    def move_topic(self, ref: str | int, new_path: str) -> dict: raise NotImplementedError
    def remove_topic(self, ref: str | int, *, archive: bool = False) -> dict: raise NotImplementedError

    # ---- team --------------------------------------------------------------------------
    def profiles(self) -> list[dict]: raise NotImplementedError
    def get_profile(self, slug: str) -> dict: raise NotImplementedError
    def add_profile(self, slug: str, *, name: str, role: str, icon: str | None = None,
                    prompt: str = "", topics: Iterable[str] = ()) -> dict: raise NotImplementedError
    def edit_profile(self, slug: str, *, if_version: int | None = None, **fields: Any) -> dict:
        """fields ⊆ {name, role, icon, prompt, topics}."""
        raise NotImplementedError
    def remove_profile(self, slug: str) -> dict: raise NotImplementedError

    def join(self, profile: str, *, handle: str | None = None, cli: str | None = None) -> dict:
        """Briefing: {agent: Agent, profile: Profile, scope: [paths], assigned: [IssueSummary],
        inbox_new: int, loop: str (role loop text), board: {prefix, require_review, lease_minutes}}."""
        raise NotImplementedError

    def leave(self, *, keep: bool = False) -> dict: raise NotImplementedError
    def whoami(self) -> dict: raise NotImplementedError
    def team(self) -> dict: raise NotImplementedError                    # {agents: [Agent]}
    def triage(self) -> dict: raise NotImplementedError

    def wait_check(self, mode: str) -> dict | None:
        """mode work|review|inbox|any. Read-only. Returns {reason, ...} when something is
        available for the actor, else None. `earmark wait` calls it after data_version moves."""
        raise NotImplementedError
