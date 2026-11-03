"""Enums and input parsing shared by every layer: keys, priorities, statuses, slugs, labels."""

from __future__ import annotations

import re
from typing import Iterable

from earmark.errors import InvalidRequest, NotFound

STATUSES = ("backlog", "todo", "in_progress", "in_review", "blocked", "done", "canceled")
CLOSED = ("done", "canceled")
OPEN = tuple(s for s in STATUSES if s not in CLOSED)
ACTIVE = ("todo", "in_progress", "in_review")
STATUS_GROUPS = {"open": OPEN, "active": ACTIVE, "closed": CLOSED, "all": STATUSES}
_STATUS_ALIASES = {"cancelled": "canceled", "inprogress": "in_progress", "review": "in_review",
                   "progress": "in_progress", "wip": "in_progress", "inreview": "in_review"}

PRIORITIES = ("urgent", "high", "medium", "low", "none")
TYPES = ("bug", "feature", "task", "chore")
COMMENT_KINDS = ("note", "progress", "question", "handoff", "approval", "changes")
USER_COMMENT_KINDS = ("note", "progress", "question")
ROLES = ("worker", "reviewer", "pm")
LINK_KINDS = ("blocks", "duplicates", "relates")
AGENT_KINDS = ("agent", "human")

# status -> statuses reachable by a regular actor (owner and pm may make any move)
TRANSITIONS = {
    "backlog": ("todo", "canceled"),
    "todo": ("in_progress", "backlog", "blocked", "canceled"),
    "in_progress": ("in_review", "todo", "blocked", "done", "canceled"),
    "in_review": ("done", "todo", "in_progress"),
    "blocked": ("todo", "in_progress", "canceled"),
    "done": ("todo",),
    "canceled": ("todo",),
}

SETTINGS = ("require_review", "lease_minutes", "prefix")
DEFAULT_LEASE_MINUTES = 120
MAX_TOPIC_DEPTH = 16

HANDLE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
TOPIC_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
LABEL_RE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,31}")
PREFIX_RE = re.compile(r"[A-Z][A-Z0-9]{0,9}")
KEY_RE = re.compile(r"#?(?:([A-Za-z][A-Za-z0-9]{0,9})-)?(\d+)")
MENTION_RE = re.compile(r"(?<![\w@])@([a-z0-9][a-z0-9-]{0,31})")


def _items(value: Iterable[str] | str | None) -> list[str]:
    """Accept 'a,b', ['a', 'b,c'] or None; return stripped non-empty parts."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    for chunk in value:
        out.extend(p.strip() for p in str(chunk).split(",") if p.strip())
    return out


def _choice(value: str, choices: tuple[str, ...], what: str) -> str:
    norm = str(value or "").strip().lower()
    if norm not in choices:
        raise InvalidRequest(f"invalid {what} {value!r}; use one of: {', '.join(choices)}")
    return norm


# ---- statuses ---------------------------------------------------------------------------

def parse_status(value: str) -> str:
    norm = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    norm = _STATUS_ALIASES.get(norm.replace("_", ""), _STATUS_ALIASES.get(norm, norm))
    if norm not in STATUSES:
        raise InvalidRequest(f"invalid status {value!r}; use one of: {', '.join(STATUSES)}")
    return norm


def parse_statuses(value: Iterable[str] | str | None) -> list[str]:
    """Statuses or groups (open, active, closed, all). None or empty means 'open'."""
    parts = _items(value) or ["open"]
    out: list[str] = []
    for part in parts:
        group = STATUS_GROUPS.get(part.lower())
        for status in group if group else (parse_status(part),):
            if status not in out:
                out.append(status)
    return out


# ---- priorities, types, kinds -----------------------------------------------------------

def parse_priority(value: str | int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value <= 4:
            return value
        raise InvalidRequest(f"invalid priority {value!r}; use 0..4")
    norm = str(value or "").strip().lower()
    if norm in PRIORITIES:
        return PRIORITIES.index(norm)
    if len(norm) == 2 and norm[0] == "p" and norm[1] in "01234":
        return int(norm[1])
    if norm in ("0", "1", "2", "3", "4"):
        return int(norm)
    raise InvalidRequest(f"invalid priority {value!r}; use one of: {', '.join(PRIORITIES)} (or p0..p4)")


def parse_priorities(value: Iterable[str] | str | None) -> list[int]:
    return sorted({parse_priority(p) for p in _items(value)})


def priority_name(value: int) -> str:
    return PRIORITIES[value]


def parse_type(value: str) -> str:
    return _choice(value, TYPES, "type")


def parse_types(value: Iterable[str] | str | None) -> list[str]:
    return sorted({parse_type(t) for t in _items(value)})


def parse_comment_kind(value: str, allowed: tuple[str, ...] = USER_COMMENT_KINDS) -> str:
    return _choice(value, allowed, "comment kind")


def parse_role(value: str) -> str:
    return _choice(value, ROLES, "role")


def parse_link_kind(value: str) -> str:
    return _choice(str(value or "").replace("_", "-"), LINK_KINDS, "link kind")


def parse_bool(value: str | bool | int) -> bool:
    if isinstance(value, bool):
        return value
    norm = str(value).strip().lower()
    if norm in ("1", "true", "yes", "on", "y"):
        return True
    if norm in ("0", "false", "no", "off", "n"):
        return False
    raise InvalidRequest(f"invalid boolean {value!r}; use 1/0, true/false, yes/no")


# ---- keys -------------------------------------------------------------------------------

def format_key(prefix: str, issue_id: int | None) -> str | None:
    return None if issue_id is None else f"{prefix}-{issue_id}"


def parse_key(value: str | int, prefix: str) -> int:
    """'GAME-42', 'game-42', '42' (or an int) → 42."""
    if isinstance(value, int) and not isinstance(value, bool):
        if value > 0:
            return value
        raise InvalidRequest(f"invalid issue key {value!r}")
    match = KEY_RE.fullmatch(str(value or "").strip())
    if not match:
        raise InvalidRequest(f"invalid issue key {value!r}; use {prefix}-42 or 42")
    given, number = match.groups()
    if given and given.upper() != prefix:
        raise NotFound(f"{value} is not on this board (keys here look like {prefix}-{number})",
                       hint=f"earmark show {prefix}-{number}")
    return int(number)


# ---- slugs, handles, labels, prefixes ---------------------------------------------------

def parse_handle(value: str, what: str = "handle") -> str:
    norm = str(value or "").strip().lower().lstrip("@")
    if not HANDLE_RE.fullmatch(norm):
        raise InvalidRequest(f"invalid {what} {value!r}; use [a-z0-9][a-z0-9-]*, at most 32 chars")
    return norm


def parse_slug(value: str) -> str:
    return parse_handle(value, "slug")


def parse_topic_path(value: str) -> str:
    norm = str(value or "").strip().strip("/").lower()
    parts = norm.split("/") if norm else []
    if not parts or not all(TOPIC_SLUG_RE.fullmatch(p) for p in parts):
        raise InvalidRequest(f"invalid topic path {value!r}; segments are [a-z0-9][a-z0-9-]*, "
                             f"at most 48 chars, joined by '/'")
    if len(parts) > MAX_TOPIC_DEPTH:
        raise InvalidRequest(f"topic path {value!r} is deeper than {MAX_TOPIC_DEPTH} levels")
    return "/".join(parts)


def topic_ancestors(path: str) -> list[str]:
    """'a/b/c' → ['a', 'a/b'] (strict ancestors, root first)."""
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def parse_label(value: str) -> str:
    norm = str(value or "").strip().lower()
    if not LABEL_RE.fullmatch(norm):
        raise InvalidRequest(f"invalid label {value!r}; use [a-z0-9][a-z0-9._:-]*, at most 32 chars")
    return norm


def parse_labels(value: Iterable[str] | str | None) -> list[str]:
    return sorted({parse_label(v) for v in _items(value)})


def parse_prefix(value: str) -> str:
    norm = str(value or "").strip().upper()
    if not PREFIX_RE.fullmatch(norm):
        raise InvalidRequest(f"invalid prefix {value!r}; use [A-Z][A-Z0-9]{{0,9}}")
    return norm


def default_prefix(directory_name: str) -> str:
    """First ≤6 alphanumerics of the directory name, uppercased; 'EM' unless it starts with a letter."""
    letters = "".join(ch for ch in directory_name if ch.isascii() and ch.isalnum())[:6].upper()
    return letters if letters[:1].isalpha() else "EM"


def slugify(text: str, max_len: int = 32) -> str:
    """'Hector Oviedo' → 'hector-oviedo'; '' when nothing usable remains."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return slug[:max_len].rstrip("-")


def mentions(body: str) -> list[str]:
    """Candidate @handles in a comment body, in order of first appearance."""
    seen: list[str] = []
    for handle in MENTION_RE.findall(body or ""):
        if handle not in seen:
            seen.append(handle)
    return seen
