"""JSON API routes (docs/HTTP_API.md). Each handler maps one request onto board methods.

Handlers receive a Request (board, path params, query, decoded JSON body) and return the
`data` part of the envelope; errors are earmark.errors exceptions. No SQL lives here.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

import earmark
from earmark.errors import Conflict, EarmarkError, InvalidRequest

def _enums() -> dict:
    """Enum lists for /api/board, from earmark.model (with a static fallback)."""
    try:
        from earmark import model

        return {
            "statuses": list(model.STATUSES),
            "priorities": list(model.PRIORITIES),
            "types": list(model.TYPES),
            "comment_kinds": list(model.COMMENT_KINDS),
            "roles": list(model.ROLES),
            "link_kinds": list(model.LINK_KINDS),
        }
    except (ImportError, AttributeError):  # pragma: no cover - model always ships with the server
        return {
            "statuses": ["backlog", "todo", "in_progress", "in_review", "blocked", "done", "canceled"],
            "priorities": ["urgent", "high", "medium", "low", "none"],
            "types": ["bug", "feature", "task", "chore"],
            "comment_kinds": ["note", "progress", "question", "handoff", "approval", "changes"],
            "roles": ["worker", "reviewer", "pm"],
            "link_kinds": ["blocks", "duplicates", "relates"],
        }


TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off", ""}


class Request:
    __slots__ = ("board", "params", "query", "body")

    def __init__(self, board: Any, params: dict[str, str], query: dict[str, list[str]], body: Any):
        self.board = board
        self.params = params
        self.query = query
        self.body = body

    # ---- query helpers ------------------------------------------------------------------
    def has(self, name: str) -> bool:
        return name in self.query

    def q(self, name: str) -> str | None:
        values = self.query.get(name)
        return values[-1] if values else None

    def qlist(self, name: str) -> list[str]:
        out: list[str] = []
        for value in self.query.get(name, ()):
            out.extend(part.strip() for part in value.split(",") if part.strip())
        return out

    def qbool(self, name: str, default: bool = False) -> bool:
        value = self.q(name)
        if value is None:
            return default
        low = value.strip().lower()
        if low in TRUE:
            return True
        if low in FALSE:
            return False
        raise InvalidRequest(f"query parameter '{name}' must be 1/true or 0/false, got '{value}'")

    def qint(self, name: str, *, minimum: int = 0) -> int | None:
        value = self.q(name)
        if value is None or value.strip() == "":
            return None
        try:
            number = int(value)
        except ValueError:
            raise InvalidRequest(f"query parameter '{name}' must be an integer, got '{value}'") from None
        if number < minimum:
            raise InvalidRequest(f"query parameter '{name}' must be >= {minimum}")
        return number


# ---- body validation ---------------------------------------------------------------------
STR = "string"
OPT_STR = "string or null"
INT = "integer"
OPT_INT = "integer or null"
BOOL = "boolean"
STR_LIST = "list of strings"
KEY = "string or integer"
OPT_KEY = "string, integer or null"


def _type_ok(value: Any, kind: str) -> bool:
    if value is None:
        return kind in (OPT_STR, OPT_INT, OPT_KEY)
    if kind in (STR, OPT_STR):
        return isinstance(value, str)
    if kind in (INT, OPT_INT):
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == BOOL:
        return isinstance(value, bool)
    if kind == STR_LIST:
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if kind in (KEY, OPT_KEY):
        return isinstance(value, (str, int)) and not isinstance(value, bool)
    return False


def fields(body: Any, spec: dict[str, str], required: Iterable[str] = ()) -> dict:
    """Validate a JSON object body against {name: kind}; return only the keys present."""
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise InvalidRequest("request body must be a JSON object")
    unknown = sorted(set(body) - set(spec))
    if unknown:
        raise InvalidRequest(
            f"unknown field{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}",
            hint="allowed: " + ", ".join(spec),
        )
    for name in required:
        if name not in body or body[name] is None:
            raise InvalidRequest(f"missing required field: {name}")
    for name, value in body.items():
        if not _type_ok(value, spec[name]):
            raise InvalidRequest(f"field '{name}' must be a {spec[name]}")
    return dict(body)


def _looks_like_issue(value: Any) -> bool:
    return isinstance(value, dict) and "key" in value and "links" in value


def _issue(board: Any, key: str, result: Any) -> dict:
    """Write methods may return the Issue or a smaller dict; the wire format is the Issue."""
    if _looks_like_issue(result):
        return result
    if isinstance(result, dict) and _looks_like_issue(result.get("issue")):
        return result["issue"]
    if isinstance(result, dict) and isinstance(result.get("key"), str):
        key = result["key"]
    return board.get_issue(key)


def _topic_ref(raw: str) -> str | int:
    return int(raw) if raw.isdigit() else raw


# ---- board -------------------------------------------------------------------------------
def get_board(r: Request) -> dict:
    info = r.board.info() or {}
    data = dict(info)
    data["prefix"] = info.get("prefix", getattr(r.board, "prefix", None))
    data["owner"] = info.get("owner", getattr(r.board, "owner", None))
    settings = dict(info.get("settings") or {})
    for name in ("require_review", "lease_minutes"):
        if name not in settings and name in info:
            settings[name] = info[name]
    data["settings"] = settings
    counts = info.get("counts") or {}
    data["counts"] = {
        "by_status": counts.get("by_status", {}),
        "open": counts.get("open", 0),
        "total": counts.get("total", 0),
    }
    enums = _enums()
    enums.update(info.get("enums") or {})
    data["enums"] = enums
    data["version"] = info.get("version") or earmark.__version__
    return data


# ---- topics ------------------------------------------------------------------------------
def list_topics(r: Request) -> list:
    return r.board.topics(include_archived=r.qbool("all"))


def add_topic(r: Request) -> dict:
    b = fields(r.body, {"path": STR, "name": OPT_STR, "icon": OPT_STR, "description": OPT_STR},
               required=("path",))
    path = b.pop("path")
    return r.board.add_topic(path, **{k: v for k, v in b.items() if v is not None})


def edit_topic(r: Request) -> dict:
    ref = _topic_ref(r.params["id"])
    b = fields(r.body, {"name": STR, "icon": OPT_STR, "description": OPT_STR, "path": STR,
                        "position": INT, "archived": BOOL})
    if not b:
        raise InvalidRequest("nothing to change",
                             hint="send one of: name, icon, description, path, position, archived")
    result = None
    if "path" in b:
        result = r.board.move_topic(ref, b.pop("path"))
        if isinstance(result, dict) and result.get("id") is not None:
            ref = result["id"]
    if b:
        result = r.board.edit_topic(ref, **b)
    return result


def remove_topic(r: Request) -> dict:
    ref = _topic_ref(r.params["id"])
    if r.qbool("archive"):  # extension: archive a non-empty subtree instead of deleting it
        return r.board.remove_topic(ref, archive=True)
    r.board.remove_topic(ref)
    return {"deleted": ref}


# ---- issues: read ------------------------------------------------------------------------
LIST_PARAMS = ("topic", "priority", "type", "label")
STR_PARAMS = ("min_priority", "assignee", "reviewer", "reporter", "q", "due_before",
              "updated_since", "created_since", "parent", "sort")
BOOL_PARAMS = ("exact", "overdue", "ready", "blocked", "stale", "desc")
INT_PARAMS = ("limit", "offset")


def list_issues(r: Request) -> dict:
    kw: dict[str, Any] = {}
    for name in LIST_PARAMS:
        values = r.qlist(name)
        if values:
            kw[name] = values
    statuses = r.qlist("status")
    if statuses:
        kw["status"] = statuses[0] if len(statuses) == 1 else statuses
    for name in STR_PARAMS:
        value = r.q(name)
        if value is not None and value != "":
            kw[name] = value
    for name in BOOL_PARAMS:
        if r.has(name):
            kw[name] = r.qbool(name)
    for name in INT_PARAMS:
        value = r.qint(name)
        if value is not None:
            kw[name] = value
    return r.board.list_issues(**kw)


def get_issue(r: Request) -> dict:
    return r.board.get_issue(r.params["key"])


def issue_events(r: Request) -> list:
    return r.board.issue_events(r.params["key"])


def list_comments(r: Request) -> dict:
    kw: dict[str, Any] = {}
    since = r.qint("since")
    if since is not None:
        kw["since"] = since
    limit = r.qint("limit", minimum=1)
    if limit is not None:
        kw["limit"] = limit
    return r.board.list_comments(r.params["key"], **kw)


def activity(r: Request) -> dict:
    kw: dict[str, Any] = {}
    since = r.qint("since")
    if since is not None:
        kw["since"] = since
    limit = r.qint("limit", minimum=1)
    if limit is not None:
        kw["limit"] = limit
    return r.board.activity(**kw)


# ---- issues: write -----------------------------------------------------------------------
CREATE_SPEC = {"title": STR, "body": STR, "topic": OPT_STR, "priority": STR, "type": STR,
               "status": STR, "labels": STR_LIST, "due": OPT_STR, "parent": OPT_KEY,
               "assignee": OPT_STR}
PATCH_SPEC = {"version": INT, "title": STR, "body": STR, "topic": OPT_STR, "priority": STR,
              "type": STR, "due": OPT_STR, "labels": STR_LIST, "parent": OPT_KEY}


def create_issue(r: Request) -> dict:
    b = fields(r.body, CREATE_SPEC, required=("title",))
    title = b.pop("title")
    kw = {k: (str(v) if k == "parent" else v) for k, v in b.items() if v is not None}
    result = r.board.create_issue(title, **kw)
    return _issue(r.board, result.get("key") if isinstance(result, dict) else None, result)


def update_issue(r: Request) -> dict:
    key = r.params["key"]
    b = fields(r.body, PATCH_SPEC, required=("version",))
    version = b.pop("version")
    if not b:
        raise InvalidRequest("nothing to change",
                             hint="send one of: " + ", ".join(k for k in PATCH_SPEC if k != "version"))
    if b.get("parent") is not None:
        b["parent"] = str(b["parent"])
    return _issue(r.board, key, r.board.update_issue(key, if_version=version, **b))


def move_issue(r: Request) -> dict:
    key = r.params["key"]
    b = fields(r.body, {"status": STR, "note": OPT_STR}, required=("status",))
    note = b.get("note") or None
    return _issue(r.board, key, r.board.move(key, b["status"], note=note))


def assign_issue(r: Request) -> dict:
    key = r.params["key"]
    b = fields(r.body, {"assignee": OPT_STR})
    if "assignee" not in b:
        raise InvalidRequest("missing required field: assignee", hint='send {"assignee": null} to unassign')
    return _issue(r.board, key, r.board.assign(key, b["assignee"] or None))


def rank_issue(r: Request) -> dict:
    key = r.params["key"]
    b = fields(r.body, {"before": OPT_KEY, "after": OPT_KEY, "top": BOOL, "bottom": BOOL})
    kw: dict[str, Any] = {}
    for name in ("before", "after"):
        if b.get(name) is not None:
            kw[name] = str(b[name])
    for name in ("top", "bottom"):
        if b.get(name):
            kw[name] = True
    if not kw:
        raise InvalidRequest("rank needs one of: before, after, top, bottom")
    return _issue(r.board, key, r.board.rank(key, **kw))


def _link_args(r: Request) -> tuple[str, str]:
    b = fields(r.body, {"kind": STR, "target": KEY}, required=("kind", "target"))
    return b["kind"], str(b["target"])


def link_issue(r: Request) -> dict:
    key = r.params["key"]
    kind, target = _link_args(r)
    return _issue(r.board, key, r.board.link(key, kind, target))


def unlink_issue(r: Request) -> dict:
    key = r.params["key"]
    kind, target = _link_args(r)
    return _issue(r.board, key, r.board.unlink(key, kind, target))


def delete_issue(r: Request) -> dict:
    key = r.params["key"]
    result = r.board.delete_issue(key)
    if isinstance(result, dict) and isinstance(result.get("deleted"), str):
        return {"deleted": result["deleted"]}
    if isinstance(result, dict) and isinstance(result.get("key"), str):
        return {"deleted": result["key"]}
    return {"deleted": key}


# ---- comments ----------------------------------------------------------------------------
def add_comment(r: Request) -> dict:
    b = fields(r.body, {"body": STR, "kind": STR, "reply_to": OPT_INT}, required=("body",))
    kw: dict[str, Any] = {}
    if b.get("kind"):
        kw["kind"] = b["kind"]
    if b.get("reply_to") is not None:
        kw["reply_to"] = b["reply_to"]
    return r.board.add_comment(r.params["key"], b["body"], **kw)


def edit_comment(r: Request) -> dict:
    b = fields(r.body, {"body": STR}, required=("body",))
    return r.board.edit_comment(int(r.params["id"]), b["body"])


# ---- team --------------------------------------------------------------------------------
def team(r: Request) -> dict:
    return r.board.team()


def list_profiles(r: Request) -> list:
    return r.board.profiles()


PROFILE_SPEC = {"slug": STR, "name": STR, "role": STR, "icon": OPT_STR, "prompt": STR,
                "topics": STR_LIST}


def add_profile(r: Request) -> dict:
    b = fields(r.body, PROFILE_SPEC, required=("slug", "name", "role"))
    slug = b.pop("slug")
    return r.board.add_profile(slug, **{k: v for k, v in b.items() if v is not None})


def edit_profile(r: Request) -> dict:
    spec = {k: v for k, v in PROFILE_SPEC.items() if k != "slug"}
    spec["version"] = INT
    b = fields(r.body, spec, required=("version",))
    version = b.pop("version")
    if not b:
        raise InvalidRequest("nothing to change", hint="send one of: name, role, icon, prompt, topics")
    return r.board.edit_profile(r.params["slug"], if_version=version, **b)


def remove_profile(r: Request) -> dict:
    slug = r.params["slug"]
    r.board.remove_profile(slug)
    return {"deleted": slug}


# ---- routing -----------------------------------------------------------------------------
class Route:
    """`{name}` matches one path segment; `{name:int}` matches digits only."""

    __slots__ = ("method", "pattern", "regex", "handler", "conflict")

    def __init__(self, method: str, pattern: str, handler: Callable[[Request], Any],
                 conflict: str | None = None):
        self.method = method
        self.pattern = re.sub(r"\{(\w+):\w+\}", r"{\1}", pattern)
        self.handler = handler
        self.conflict = conflict  # 'issue' or 'profile': 409 details carry the current object

        def group(m: re.Match) -> str:
            body = "[0-9]+" if m.group(2) == "int" else "[^/]+"
            return f"(?P<{m.group(1)}>{body})"

        self.regex = re.compile("^" + re.sub(r"\{(\w+)(?::(\w+))?\}", group, pattern) + "$")

    @property
    def name(self) -> str:
        return f"{self.method} {self.pattern}"


ROUTES = [
    Route("GET", "/api/board", get_board),
    Route("GET", "/api/topics", list_topics),
    Route("POST", "/api/topics", add_topic),
    Route("PATCH", "/api/topics/{id}", edit_topic),
    Route("DELETE", "/api/topics/{id}", remove_topic),
    Route("GET", "/api/issues", list_issues),
    Route("POST", "/api/issues", create_issue),
    Route("GET", "/api/issues/{key}", get_issue),
    Route("PATCH", "/api/issues/{key}", update_issue, conflict="issue"),
    Route("DELETE", "/api/issues/{key}", delete_issue, conflict="issue"),
    Route("POST", "/api/issues/{key}/status", move_issue, conflict="issue"),
    Route("POST", "/api/issues/{key}/assign", assign_issue, conflict="issue"),
    Route("POST", "/api/issues/{key}/rank", rank_issue, conflict="issue"),
    Route("POST", "/api/issues/{key}/links", link_issue, conflict="issue"),
    Route("DELETE", "/api/issues/{key}/links", unlink_issue, conflict="issue"),
    Route("GET", "/api/issues/{key}/comments", list_comments),
    Route("POST", "/api/issues/{key}/comments", add_comment, conflict="issue"),
    Route("GET", "/api/issues/{key}/events", issue_events),
    Route("PATCH", "/api/comments/{id:int}", edit_comment),
    Route("GET", "/api/activity", activity),
    Route("GET", "/api/team", team),
    Route("GET", "/api/profiles", list_profiles),
    Route("POST", "/api/profiles", add_profile),
    Route("PATCH", "/api/profiles/{slug}", edit_profile, conflict="profile"),
    Route("DELETE", "/api/profiles/{slug}", remove_profile),
]

STREAM_PATH = "/api/stream"


def match(method: str, path: str) -> tuple[Route | None, dict[str, str], list[str]]:
    """Return (route, raw params, allowed methods for the path)."""
    allowed: list[str] = []
    for route in ROUTES:
        m = route.regex.match(path)
        if not m:
            continue
        if route.method == method:
            return route, m.groupdict(), []
        allowed.append(route.method)
    if path == STREAM_PATH:
        allowed.append("GET")
    return None, {}, allowed


def run(route: Route, req: Request) -> Any:
    """Call the handler; on conflict, put the current object in error.details."""
    try:
        return route.handler(req)
    except Conflict as exc:
        _attach_current(route, req, exc)
        raise


def _attach_current(route: Route, req: Request, exc: EarmarkError) -> None:
    try:
        if route.conflict == "issue":
            exc.details = req.board.get_issue(req.params["key"])
        elif route.conflict == "profile":
            exc.details = req.board.get_profile(req.params["slug"])
    except EarmarkError:
        pass
